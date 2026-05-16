import asyncio
import logging
import os
import socket

from .config import Config
from .consumer import RedisConsumer
from .lark import LarkNotifier
from .llm import LLMAnalyzer
from .models import DemandEvent

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._consumer_name = (
            f"worker-{socket.gethostname()}-{os.getpid()}"
        )
        # Per-run retry tracking.  Persistent retry info lives in the PEL delivery count.
        self._retry_counts: dict[str, int] = {}

    async def run(self) -> None:
        consumer = await RedisConsumer.create(self._config, self._consumer_name)
        llm = LLMAnalyzer(self._config)
        lark = LarkNotifier(self._config)

        try:
            await consumer.claim_pending()
            logger.info("Worker started", extra={"consumer": self._consumer_name})

            while True:
                events = await consumer.read_batch()
                if events:
                    await asyncio.gather(
                        *[self._process(consumer, llm, lark, ev) for ev in events],
                        return_exceptions=True,
                    )
        finally:
            await consumer.close()
            await lark.close()

    # ------------------------------------------------------------------

    async def _process(
        self,
        consumer: RedisConsumer,
        llm: LLMAnalyzer,
        lark: LarkNotifier,
        event: DemandEvent,
    ) -> None:
        log_ctx = {"message_id": event.message_id}
        retry_count = self._retry_counts.get(event.message_id, 0)

        try:
            analysis = await llm.analyze(event.description)
        except ValueError as exc:
            # Malformed LLM output is deterministic — dead-letter immediately.
            logger.error("LLM returned invalid output, dead-lettering", extra={**log_ctx, "error": str(exc)})
            await self._dead_letter(consumer, event, exc)
            return
        except Exception as exc:
            logger.error("LLM analysis failed", extra={**log_ctx, "error": str(exc), "retry_count": retry_count})
            await self._maybe_dead_letter(consumer, event, exc)
            return

        try:
            await lark.send(event, analysis)
        except Exception as exc:
            logger.error("Lark delivery failed", extra={**log_ctx, "error": str(exc), "retry_count": retry_count})
            await self._maybe_dead_letter(consumer, event, exc)
            return

        await consumer.ack(event.message_id)
        self._retry_counts.pop(event.message_id, None)
        logger.info(
            "Message processed",
            extra={**log_ctx, "category": analysis.category, "lark_status": "ok"},
        )

    async def _maybe_dead_letter(
        self, consumer: RedisConsumer, event: DemandEvent, exc: Exception
    ) -> None:
        count = self._retry_counts.get(event.message_id, 0) + 1
        self._retry_counts[event.message_id] = count
        if count >= self._config.max_retries:
            await self._dead_letter(consumer, event, exc)
        # Otherwise leave the message in the PEL to be retried.

    async def _dead_letter(
        self, consumer: RedisConsumer, event: DemandEvent, exc: Exception
    ) -> None:
        fields = {
            "agent_id": event.agent_id,
            "description": event.description,
            "signal_type": event.signal_type,
            "created_at": event.created_at,
        }
        await consumer.dead_letter(event.message_id, fields, exc)
        self._retry_counts.pop(event.message_id, None)
