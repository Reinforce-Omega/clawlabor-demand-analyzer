import asyncio
import json
import logging
import time

from openai import AsyncOpenAI

from .config import Config
from .models import (
    ANALYSIS_TOOL,
    CATEGORY_SLUGS,
    DemandRewrite,
    LLMAnalysis,
    REWRITE_TOOL,
)

logger = logging.getLogger(__name__)


REWRITE_SYSTEM_PROMPT = (
    "You normalize unmet-demand descriptions for an AI capability marketplace. "
    "Call rewrite_demand exactly once with a complete JSON object."
)


REWRITE_USER_PROMPT = """\
The buyer may write in any natural language. Your job is to:
1. Detect the source language of the description.
2. Rewrite the description into a clean ENGLISH retrieval query that
   describes the TASK SHAPE, regardless of the source language.
3. Strip instance-specific noise (proper nouns, filenames, URLs, dates,
   ticket ids, version strings, repo names, person/company/customer names)
   — translate each to its generic artifact type only.

All free-text fields you return (normalized_task, capability_terms,
desired_outputs, input_artifacts, output_artifacts, negative_capability_terms)
MUST be in English even when the source language is not English.

Examples of instance noise → generic abstraction:
- "invoice_2024_Q3_acme_corp.pdf"          → "PDF document"
- "demo_video_final_v2.mp4"                → "MP4 video file"
- "ContractDraft_v8_2025-07-14.docx"       → "Word document, contract"
- "PR #4127 in foo/bar"                    → "pull request diff"
- "https://example.com/post/123"           → "web page URL"
- "meeting_2026-05-26_legal.m4a"           → "M4A audio recording"
- "summarize DeepSeek's recent updates"    → "summarize a company's recent public updates"
- "monitor OpenAI announcements weekly"    → "monitor a company's announcements on a recurring schedule"

normalized_task, capability_terms, input_artifacts, output_artifacts MUST
NOT contain literal filenames, URLs, version strings, ticket IDs, dates,
or customer/company/repo/person names. constraints likewise: only TASK
SHAPE keys (language, format, framework, deadline, platform, count, recency).
Do NOT add ad-hoc keys like "repository", "use_case", "ticket", "customer",
"target_company" — those leak instance noise into retrieval.

Allowed category_hints slugs (closed set):
research_analysis, writing_content, code_engineering, data_automation,
design_image, audio_video, business_ops, legal_finance, education_coaching,
agent_integration, other

When the buyer wants output in their own language, set
constraints.language to that language (e.g. "Chinese", "Japanese") so
seller-side responses know to use it, even though retrieval terms above
are normalized to English.

Demand description:
{description}
"""


PROPOSE_SYSTEM_PROMPT = (
    "You design generic marketplace SKUs from rewritten demand signals. "
    "You work ONLY from the structured rewrite — never invent or reintroduce "
    "instance details that were already abstracted away. Call propose_agent "
    "exactly once with a complete JSON object."
)


PROPOSE_USER_PROMPT = """\
Below is the rewritten generic shape of an unmet demand. Design a generic
marketplace agent/SKU that would have satisfied this demand.

Hard rules — violating any of these means your output is wrong:
1. Use ONLY the rewrite. You do not have access to the original description.
2. suggested_agent_name MUST be generic. NO proper nouns: no company names,
   product names, brand names, person names, repo names, country/city names.
   If the rewrite mentioned a specific entity, treat it as ONE INSTANCE of
   a more general capability and name the agent at that general level.
   Bad:  "DeepSeek Activity Summarizer", "Acme PDF Extractor",
         "GitHub PR Reviewer", "Tokyo Restaurant Finder"
   Good: "Company Activity Summarizer", "Invoice PDF Extractor",
         "Pull Request Reviewer", "Local Restaurant Finder"
3. category MUST equal rewrite.category_hints[0] if non-empty, else "other".
4. required_capabilities MUST be a 3-8 item refinement of rewrite.capability_terms
   (you may merge/rephrase, but stay at the same level of generality).
5. input_schema fields come from rewrite.input_artifacts + the parameters
   needed for variation (e.g. if the demand is "summarize a company's
   updates", the schema needs a `company_name` param, not "DeepSeek").
6. output_schema fields come from rewrite.output_artifacts + rewrite.desired_outputs.
7. complexity reflects how hard the GENERIC capability is, not this one instance.
8. priority_score: 0-1. High = generic capability the marketplace is missing
   and many users would want. Low = niche or one-off.
9. reasoning: 1-3 sentences. Reference rewrite fields, not raw description.

Rewrite (JSON):
{rewrite_json}
"""


_TIMEOUT_SECONDS = 30


class LLMAnalyzer:
    def __init__(self, config: Config) -> None:
        self._client = AsyncOpenAI(api_key=config.openai_api_key, base_url=config.llm_base_url)
        self._model = config.llm_model

    async def analyze(self, description: str) -> tuple[DemandRewrite, LLMAnalysis]:
        """Two-stage analysis: rewrite raw demand, then propose from rewrite.

        Returns both stages so downstream consumers (Lark card) can show the
        full reasoning chain: raw -> rewrite -> proposal.
        """
        rewrite = await self._with_retry(self._rewrite, description, op="rewrite")
        proposal = await self._with_retry(self._propose, rewrite, op="propose")
        return rewrite, self._coerce_consistency(proposal, rewrite)

    # ------------------------------------------------------------------
    # Stage 1: rewrite
    # ------------------------------------------------------------------

    async def _rewrite(self, description: str) -> DemandRewrite:
        t0 = time.monotonic()
        response = await self._client.chat.completions.create(
            model=self._model,
            max_tokens=700,
            temperature=0.0,
            tools=[REWRITE_TOOL],
            tool_choice={"type": "function", "function": {"name": "rewrite_demand"}},
            messages=[
                {"role": "system", "content": REWRITE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": REWRITE_USER_PROMPT.format(description=description[:3000]),
                },
            ],
        )
        latency_ms = int((time.monotonic() - t0) * 1000)

        raw = _extract_tool_args(response, "rewrite_demand")
        rewrite = DemandRewrite.model_validate(raw)
        logger.info(
            "LLM rewrite complete",
            extra={
                "stage": "rewrite",
                "llm_latency_ms": latency_ms,
                "source_language": rewrite.source_language,
                "category_hints": rewrite.category_hints,
                "confidence": rewrite.confidence,
            },
        )
        return rewrite

    # ------------------------------------------------------------------
    # Stage 2: propose
    # ------------------------------------------------------------------

    async def _propose(self, rewrite: DemandRewrite) -> LLMAnalysis:
        t0 = time.monotonic()
        rewrite_json = rewrite.model_dump_json(indent=2)
        response = await self._client.chat.completions.create(
            model=self._model,
            max_tokens=1024,
            tools=[ANALYSIS_TOOL],
            tool_choice={"type": "function", "function": {"name": "propose_agent"}},
            messages=[
                {"role": "system", "content": PROPOSE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": PROPOSE_USER_PROMPT.format(rewrite_json=rewrite_json),
                },
            ],
        )
        latency_ms = int((time.monotonic() - t0) * 1000)

        raw = _extract_tool_args(response, "propose_agent")
        analysis = LLMAnalysis.model_validate(raw)
        logger.info(
            "LLM proposal complete",
            extra={
                "stage": "propose",
                "llm_latency_ms": latency_ms,
                "category": analysis.category,
                "complexity": analysis.complexity,
                "priority_score": analysis.priority_score,
            },
        )
        return analysis

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _with_retry(self, fn, arg, *, op: str):
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                return await asyncio.wait_for(fn(arg), timeout=_TIMEOUT_SECONDS)
            except TimeoutError as exc:
                last_exc = exc
                if attempt == 0:
                    logger.warning(
                        "LLM timeout on first attempt, retrying", extra={"stage": op}
                    )
                    continue
        raise last_exc  # type: ignore[misc]

    def _coerce_consistency(
        self, proposal: LLMAnalysis, rewrite: DemandRewrite
    ) -> LLMAnalysis:
        """Enforce the hard rule that category tracks rewrite.category_hints[0].

        The LLM is told this in the prompt, but the schema enum alone does not
        guarantee alignment. We overwrite defensively rather than retry.
        """
        expected = rewrite.category_hints[0] if rewrite.category_hints else "other"
        if expected not in CATEGORY_SLUGS:
            expected = "other"
        if proposal.category != expected:
            logger.warning(
                "Coercing proposal.category to rewrite.category_hints[0]",
                extra={"original": proposal.category, "coerced_to": expected},
            )
            proposal = proposal.model_copy(update={"category": expected})
        return proposal


def _extract_tool_args(response, expected_name: str) -> dict:
    choice = response.choices[0]
    tool_calls = choice.message.tool_calls
    if not tool_calls:
        raise ValueError(f"LLM response contained no tool call (expected {expected_name})")
    call = tool_calls[0]
    if call.function.name != expected_name:
        raise ValueError(
            f"LLM called unexpected tool {call.function.name!r}, expected {expected_name!r}"
        )
    return json.loads(call.function.arguments)
