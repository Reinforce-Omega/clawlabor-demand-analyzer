import asyncio
import json
import logging
import time

from openai import AsyncOpenAI

from .config import Config
from .models import ANALYSIS_TOOL, LLMAnalysis

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are an agent marketplace analyst. Your job is to analyze unmet user demands "
    "and propose new agents that could fulfill them. Always call the analyze_demand function "
    "with a complete, structured proposal. For file arguments use type 'url'; "
    "for file outputs use type 'url'."
)

_TIMEOUT_SECONDS = 30


class LLMAnalyzer:
    def __init__(self, config: Config) -> None:
        self._client = AsyncOpenAI(api_key=config.openai_api_key, base_url=config.llm_base_url)
        self._model = config.llm_model

    async def analyze(self, description: str) -> LLMAnalysis:
        """Call the LLM once (with one retry on timeout) and return structured analysis."""
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                return await asyncio.wait_for(
                    self._call(description), timeout=_TIMEOUT_SECONDS
                )
            except TimeoutError as exc:
                last_exc = exc
                if attempt == 0:
                    logger.warning("LLM timeout on first attempt, retrying")
                    continue
        raise last_exc  # type: ignore[misc]

    async def _call(self, description: str) -> LLMAnalysis:
        t0 = time.monotonic()
        response = await self._client.chat.completions.create(
            model=self._model,
            max_tokens=1024,
            tools=[ANALYSIS_TOOL],
            tool_choice={"type": "function", "function": {"name": "analyze_demand"}},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Analyze the following unmet demand and return a structured proposal.\n\n"
                        f"Task description: {description}"
                    ),
                },
            ],
        )
        latency_ms = int((time.monotonic() - t0) * 1000)

        choice = response.choices[0]
        tool_calls = choice.message.tool_calls
        if not tool_calls:
            raise ValueError("LLM response contained no tool call")

        raw = json.loads(tool_calls[0].function.arguments)
        analysis = LLMAnalysis.model_validate(raw)
        logger.info(
            "LLM analysis complete",
            extra={
                "llm_latency_ms": latency_ms,
                "category": analysis.category,
                "complexity": analysis.complexity,
            },
        )
        return analysis
