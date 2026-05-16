import os
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class Config:
    redis_url: str
    openai_api_key: str
    lark_webhook_url: str

    redis_stream_name: str = "demands:unmet"
    redis_consumer_group: str = "demand-analyzer"
    redis_claim_threshold_ms: int = 300_000

    llm_model: str = "gpt-4o"
    llm_base_url: str | None = None

    worker_batch_size: int = 10
    worker_block_ms: int = 2000
    max_retries: int = 3


_SETTINGS_FILE = Path(__file__).parent / "settings" / "config.yaml"


def _load_yaml() -> dict:
    with open(_SETTINGS_FILE) as f:
        return yaml.safe_load(f) or {}


def load_config() -> Config:
    def require_with_fallback(env_name: str, yaml_val: str | None) -> str:
        val = os.environ.get(env_name) or yaml_val
        if not val:
            raise ValueError(f"{env_name} must be set via environment variable or config file")
        return val

    y = _load_yaml()
    redis = y.get("redis", {})
    llm = y.get("llm", {})
    lark = y.get("lark", {})
    worker = y.get("worker", {})

    return Config(
        redis_url=require_with_fallback("REDIS_URL", redis.get("url")),
        openai_api_key=require_with_fallback("OPENAI_API_KEY", llm.get("openai_api_key")),
        lark_webhook_url=require_with_fallback("LARK_WEBHOOK_URL", lark.get("webhook_url")),
        redis_stream_name=os.environ.get("REDIS_STREAM_NAME", redis.get("stream_name", "demands:unmet")),
        redis_consumer_group=os.environ.get("REDIS_CONSUMER_GROUP", redis.get("consumer_group", "demand-analyzer")),
        redis_claim_threshold_ms=int(os.environ.get("REDIS_CLAIM_THRESHOLD_MS", redis.get("claim_threshold_ms", 300000))),
        llm_model=os.environ.get("LLM_MODEL", llm.get("model", "gpt-4o")),
        llm_base_url=os.environ.get("LLM_BASE_URL") or llm.get("base_url") or None,
        worker_batch_size=int(os.environ.get("WORKER_BATCH_SIZE", worker.get("batch_size", 10))),
        worker_block_ms=int(os.environ.get("WORKER_BLOCK_MS", worker.get("block_ms", 2000))),
        max_retries=int(os.environ.get("MAX_RETRIES", worker.get("max_retries", 3))),
    )
