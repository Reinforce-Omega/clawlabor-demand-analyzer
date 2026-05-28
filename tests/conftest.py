"""Real-LLM integration tests — no mocks.

Skipped automatically unless OPENAI_API_KEY (or compatible) is set in env.
Set LLM_MODEL / LLM_BASE_URL to point at a different provider (OpenRouter etc).
"""

import os

import pytest

from src.config import Config
from src.llm import LLMAnalyzer


def _has_credentials() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


pytestmark = pytest.mark.skipif(
    not _has_credentials(),
    reason="OPENAI_API_KEY not set — real LLM tests skipped.",
)


@pytest.fixture(scope="session")
def analyzer() -> LLMAnalyzer:
    config = Config(
        redis_url="redis://unused",
        openai_api_key=os.environ["OPENAI_API_KEY"],
        lark_webhook_url="https://unused",
        llm_model=os.environ.get("LLM_MODEL", "gpt-4o"),
        llm_base_url=os.environ.get("LLM_BASE_URL") or None,
    )
    return LLMAnalyzer(config)
