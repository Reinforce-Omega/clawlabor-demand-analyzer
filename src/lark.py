import asyncio
import logging

import httpx

from .config import Config
from .models import CATEGORY_LABELS, DemandEvent, DemandRewrite, LLMAnalysis

logger = logging.getLogger(__name__)


class LarkNotifier:
    def __init__(self, config: Config) -> None:
        self._webhook_url = config.lark_webhook_url
        self._client = httpx.AsyncClient(timeout=15)

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    async def send(
        self,
        event: DemandEvent,
        rewrite: DemandRewrite,
        analysis: LLMAnalysis,
    ) -> None:
        """Send an interactive card via the configured Lark webhook URL.

        Raises on permanent failure so the caller can decide not to ACK the
        Redis message.
        """
        card = self._build_card(event, rewrite, analysis)

        for attempt in range(3):
            resp = await self._client.post(
                self._webhook_url,
                json={"msg_type": "interactive", "card": card},
            )

            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == 0:
                    logger.info("Lark notification sent", extra={"lark_status": "ok"})
                    return
                logger.warning(
                    "Lark API error",
                    extra={"lark_code": data.get("code"), "lark_msg": data.get("msg")},
                )

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 5))
                logger.warning("Lark rate-limited, waiting", extra={"retry_after": retry_after})
                await asyncio.sleep(retry_after)
                continue

            if attempt < 2:
                await asyncio.sleep(2 ** attempt)

        raise LarkDeliveryError("Lark notification failed after 3 attempts")

    # ------------------------------------------------------------------
    # Card builder
    # ------------------------------------------------------------------

    def _build_card(
        self,
        event: DemandEvent,
        rewrite: DemandRewrite,
        analysis: LLMAnalysis,
    ) -> dict:
        priority_pct = f"{analysis.priority_score:.0%}"
        category_label = CATEGORY_LABELS.get(analysis.category, analysis.category)
        caps = "\n".join(f"• {c}" for c in analysis.required_capabilities) or "—"

        def _fmt_fields(fields: list) -> str:
            lines = []
            for f in fields:
                req = " *(required)*" if getattr(f, "required", False) else ""
                lines.append(f"• **{f.name}** `{f.type}`{req}: {f.description}")
            return "\n".join(lines) or "—"

        def _bullets(items: list[str]) -> str:
            return "\n".join(f"• {x}" for x in items) or "—"

        def _kv(d: dict[str, str]) -> str:
            return "\n".join(f"• **{k}**: {v}" for k, v in d.items()) or "—"

        header_color = {"low": "green", "medium": "yellow", "high": "red"}.get(
            analysis.complexity, "blue"
        )

        category_hints_label = (
            ", ".join(CATEGORY_LABELS.get(s, s) for s in rewrite.category_hints) or "—"
        )

        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"[New Unmet Demand] {category_label} · priority {priority_pct}",
                },
                "template": header_color,
            },
            "elements": [
                # ─── Section 1: Original signal ───────────────────────────
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": "**📥 Original Demand**",
                    },
                },
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": (
                            f"**Signal Type** `{event.signal_type}`\n"
                            f"**Description**\n> {event.description}"
                        ),
                    },
                },
                {"tag": "hr"},
                # ─── Section 2: Stage-1 rewrite ───────────────────────────
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": (
                            "**🔄 Rewrite (Generic Task Shape)**\n"
                            f"_source_language_: `{rewrite.source_language}` · "
                            f"_confidence_: {rewrite.confidence:.2f}"
                        ),
                    },
                },
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**Normalized Task**\n{rewrite.normalized_task}",
                    },
                },
                {
                    "tag": "div",
                    "fields": [
                        {
                            "is_short": True,
                            "text": {
                                "tag": "lark_md",
                                "content": f"**Category Hints**\n{category_hints_label}",
                            },
                        },
                        {
                            "is_short": True,
                            "text": {
                                "tag": "lark_md",
                                "content": f"**Constraints**\n{_kv(rewrite.constraints)}",
                            },
                        },
                    ],
                },
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**Capability Terms**\n{_bullets(rewrite.capability_terms)}",
                    },
                },
                {
                    "tag": "div",
                    "fields": [
                        {
                            "is_short": True,
                            "text": {
                                "tag": "lark_md",
                                "content": (
                                    f"**Input Artifacts**\n{_bullets(rewrite.input_artifacts)}"
                                ),
                            },
                        },
                        {
                            "is_short": True,
                            "text": {
                                "tag": "lark_md",
                                "content": (
                                    f"**Output Artifacts**\n{_bullets(rewrite.output_artifacts)}"
                                ),
                            },
                        },
                    ],
                },
                {"tag": "hr"},
                # ─── Section 3: Stage-2 proposal ──────────────────────────
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": "**🎯 Proposed SKU**",
                    },
                },
                {
                    "tag": "div",
                    "fields": [
                        {
                            "is_short": True,
                            "text": {
                                "tag": "lark_md",
                                "content": f"**Complexity**\n{analysis.complexity}",
                            },
                        },
                        {
                            "is_short": True,
                            "text": {
                                "tag": "lark_md",
                                "content": f"**Priority Score**\n{analysis.priority_score:.2f}",
                            },
                        },
                    ],
                },
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": (
                            f"**Suggested Agent**\n"
                            f"**{analysis.suggested_agent_name}** — "
                            f"{analysis.suggested_agent_description}"
                        ),
                    },
                },
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**Required Capabilities**\n{caps}",
                    },
                },
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": (
                            f"**Input Schema**\n{_fmt_fields(analysis.input_schema.fields)}"
                        ),
                    },
                },
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": (
                            f"**Output Schema**\n{_fmt_fields(analysis.output_schema.fields)}"
                        ),
                    },
                },
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**Reasoning**\n{analysis.reasoning}",
                    },
                },
                {"tag": "hr"},
                {
                    "tag": "div",
                    "fields": [
                        {
                            "is_short": True,
                            "text": {
                                "tag": "lark_md",
                                "content": f"**Agent ID**\n`{event.agent_id}`",
                            },
                        },
                        {
                            "is_short": True,
                            "text": {
                                "tag": "lark_md",
                                "content": f"**Created At**\n{event.created_at}",
                            },
                        },
                    ],
                },
            ],
        }


class LarkDeliveryError(Exception):
    pass
