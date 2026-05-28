"""End-to-end smoke test against the real LLM endpoint.

Runs the full two-stage pipeline (rewrite -> propose) on a real description
and prints both stages so the operator can eyeball quality. Asserts only
that the pipeline returns structurally-valid output.

Run:
    OPENAI_API_KEY=... uv run pytest tests/test_llm_integration.py -s
"""

from __future__ import annotations

import pytest

from src.models import CATEGORY_SLUGS


pytestmark = pytest.mark.asyncio


DEMAND_DESCRIPTION = (
    "I want an agent that monitors recent public updates about DeepSeek and "
    "produces concise, source-backed summaries of what the company is doing, "
    "including product launches, model releases, partnerships, research "
    "updates, and notable announcements."
)


async def test_end_to_end_pipeline(analyzer):
    rewrite, final = await analyzer.analyze(DEMAND_DESCRIPTION)

    print("\n" + "=" * 70)
    print("STAGE 1 — REWRITE")
    print("=" * 70)
    print(rewrite.model_dump_json(indent=2))

    print("\n" + "=" * 70)
    print("STAGE 2 — PROPOSAL")
    print("=" * 70)
    print(final.model_dump_json(indent=2))

    # Structural sanity only — quality is judged by reading the printout.
    assert final.category in CATEGORY_SLUGS
    assert final.suggested_agent_name
    assert final.required_capabilities
    assert final.input_schema.fields
    assert final.output_schema.fields
    assert 0.0 <= final.priority_score <= 1.0
