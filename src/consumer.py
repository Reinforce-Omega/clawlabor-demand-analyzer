import logging
from typing import AsyncIterator

import redis.asyncio as aioredis

from .config import Config
from .models import DemandEvent

logger = logging.getLogger(__name__)

_DEAD_LETTER_STREAM = "demands:dead-letter"


class RedisConsumer:
    def __init__(
        self,
        client: aioredis.Redis,
        config: Config,
        consumer_name: str,
    ) -> None:
        self._client = client
        self._stream = config.redis_stream_name
        self._group = config.redis_consumer_group
        self._consumer = consumer_name
        self._batch = config.worker_batch_size
        self._block_ms = config.worker_block_ms
        self._claim_threshold_ms = config.redis_claim_threshold_ms

    @classmethod
    async def create(cls, config: Config, consumer_name: str) -> "RedisConsumer":
        client: aioredis.Redis = aioredis.from_url(
            config.redis_url, decode_responses=True
        )
        # Ensure the consumer group exists; MKSTREAM creates the stream if absent.
        try:
            await client.xgroup_create(
                config.redis_stream_name,
                config.redis_consumer_group,
                id="0",
                mkstream=True,
            )
        except aioredis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        return cls(client, config, consumer_name)

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Startup: reclaim stuck pending messages from crashed workers.
    # ------------------------------------------------------------------

    async def claim_pending(self) -> None:
        cursor = "0-0"
        claimed_count = 0
        while True:
            result = await self._client.xautoclaim(
                self._stream,
                self._group,
                self._consumer,
                min_idle_time=self._claim_threshold_ms,
                start_id=cursor,
                count=100,
            )
            # result is (next_cursor, [(id, fields), ...], [deleted_ids])
            next_cursor, entries, _ = result
            claimed_count += len(entries)
            if next_cursor == "0-0" or not entries:
                break
            cursor = next_cursor

        if claimed_count:
            logger.info(
                "Reclaimed pending messages on startup",
                extra={"claimed_count": claimed_count},
            )

    # ------------------------------------------------------------------
    # Main read loop
    # ------------------------------------------------------------------

    async def read_batch(self) -> list[DemandEvent]:
        result = await self._client.xreadgroup(
            groupname=self._group,
            consumername=self._consumer,
            streams={self._stream: ">"},
            count=self._batch,
            block=self._block_ms,
        )
        if not result:
            return []

        events: list[DemandEvent] = []
        for _stream_name, messages in result:
            for message_id, fields in messages:
                try:
                    events.append(_parse_event(message_id, fields))
                except Exception as exc:
                    logger.error(
                        "Failed to parse demand event, moving to dead-letter",
                        extra={"message_id": message_id, "error": str(exc)},
                    )
                    await self.dead_letter(message_id, fields, exc)
        return events

    # ------------------------------------------------------------------
    # ACK / dead-letter
    # ------------------------------------------------------------------

    async def ack(self, message_id: str) -> None:
        await self._client.xack(self._stream, self._group, message_id)

    async def dead_letter(
        self, message_id: str, fields: dict, error: Exception
    ) -> None:
        await self._client.xadd(
            _DEAD_LETTER_STREAM,
            {
                "original_stream": self._stream,
                "original_id": message_id,
                "error": str(error),
                **{k: v for k, v in fields.items()},
            },
        )
        await self._client.xack(self._stream, self._group, message_id)
        logger.warning(
            "Message moved to dead-letter",
            extra={"message_id": message_id, "error": str(error)},
        )


def _parse_event(message_id: str, fields: dict) -> DemandEvent:
    return DemandEvent(
        message_id=message_id,
        agent_id=fields["agent_id"],
        description=fields["description"],
        signal_type=fields["signal_type"],
        created_at=fields["created_at"],
    )
