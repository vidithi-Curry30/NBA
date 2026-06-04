"""
Redis Stream consumer: reads events, updates GameState, writes snapshot.

This is the hot path of the pipeline. It reads events one at a time from the
Redis Stream, applies them to a GameState model, and writes the updated state
back to a Redis Hash that the API reads in O(1).
"""

import asyncio
import json
import logging
import os

import redis.asyncio as aioredis
from dotenv import load_dotenv

from src.state import GameState

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
STREAM_KEY_TEMPLATE = "game_events:{game_id}"
STATE_KEY_TEMPLATE = "game_state:{game_id}"

# WHY consumer group named "processors": consumer groups allow multiple
# processor instances to share the event stream without duplicating work.
# Today we run one; in production you could add replicas behind a load balancer.
CONSUMER_GROUP = "processors"
CONSUMER_NAME = "processor-1"

# WHY 4 hours: NBA games last ~2.5 hours. 4 hours keeps state queryable for
# post-game analysis without letting Redis grow unboundedly across all games.
STATE_TTL_SECONDS = 4 * 60 * 60

# WHY block=1000 (1 second): XREADGROUP blocks until a new message arrives or
# the timeout expires. 1s means the loop wakes at most once per second when
# idle — no busy-waiting, but fast enough to be effectively real-time.
XREADGROUP_BLOCK_MS = 1000


async def _ensure_consumer_group(
    redis_client: aioredis.Redis, stream_key: str
) -> None:
    """
    Create the consumer group if it doesn't already exist.

    WHY XGROUP CREATE with MKSTREAM: MKSTREAM creates the stream key atomically
    with the group, so the processor can start before the poller pushes the
    first event — no race condition on startup ordering.
    """
    try:
        await redis_client.xgroup_create(
            stream_key, CONSUMER_GROUP, id="0", mkstream=True
        )
        logger.info("Consumer group '%s' created on %s", CONSUMER_GROUP, stream_key)
    except aioredis.ResponseError as exc:
        # WHY catch ResponseError specifically: Redis raises BUSYGROUP if the
        # group already exists; that's expected on restart, not an error.
        if "BUSYGROUP" in str(exc):
            logger.debug("Consumer group already exists — continuing.")
        else:
            raise


async def _write_state_snapshot(
    redis_client: aioredis.Redis, state: GameState
) -> None:
    """
    Serialize GameState to JSON and write it to a Redis Hash with TTL.

    WHY write to Redis Hash instead of leaving state in process memory: the API
    runs in a separate process and needs to read state. A Hash gives O(1) reads
    from any process on any machine — this is the materialized view half of the
    CQRS pattern (stream = write path, hash = read path).
    """
    state_key = STATE_KEY_TEMPLATE.format(game_id=state.game_id)
    payload = state.model_dump_json()

    # WHY pipeline: HSET + EXPIRE as a pipeline is one round-trip to Redis
    # instead of two, halving the latency of the write path.
    async with redis_client.pipeline(transaction=True) as pipe:
        pipe.hset(state_key, mapping={"data": payload})
        pipe.expire(state_key, STATE_TTL_SECONDS)
        await pipe.execute()


async def _process_message(
    redis_client: aioredis.Redis,
    stream_key: str,
    message_id: str,
    fields: dict,
    state: GameState,
) -> GameState:
    """
    Apply one stream message to the game state and acknowledge it.

    WHY XACK after processing, not before: this is at-least-once delivery.
    If the processor crashes between receive and ack, Redis redelivers the
    message on restart. Processing an event twice is safe (score won't change
    if the delta is 0); dropping an event silently would corrupt state.
    """
    state.update(fields)
    await _write_state_snapshot(redis_client, state)

    # WHY ack after the snapshot write: if we acked before writing, a crash
    # between ack and write would leave Redis Hash with stale state and no
    # way to recover the missed event.
    await redis_client.xack(stream_key, CONSUMER_GROUP, message_id)
    logger.debug("Processed and acked message %s", message_id)
    return state


async def process_game(game_id: str) -> None:
    """
    Main consumer loop: read events from stream and maintain game state.

    WHY consumer groups instead of basic XREAD: consumer groups persist the
    last-acknowledged offset per consumer. On restart, XREADGROUP delivers
    unacknowledged messages first (the PEL — pending entry list), then new
    ones. Basic XREAD would require storing the offset externally or risk
    reprocessing the entire stream from the beginning.
    """
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    stream_key = STREAM_KEY_TEMPLATE.format(game_id=game_id)

    await _ensure_consumer_group(redis_client, stream_key)

    # WHY initialize GameState with game_id here: the model needs a game_id
    # to construct the correct Redis Hash key for writes. Other fields default
    # to zero/empty and are updated by the first event.
    state = GameState(game_id=game_id)
    logger.info("Processor started for game %s", game_id)

    try:
        while True:
            # WHY ">" as the start ID: in consumer groups, ">" means "give me
            # messages not yet delivered to any consumer". Previously unacked
            # messages are delivered automatically before new ones.
            results = await redis_client.xreadgroup(
                groupname=CONSUMER_GROUP,
                consumername=CONSUMER_NAME,
                streams={stream_key: ">"},
                count=10,
                block=XREADGROUP_BLOCK_MS,
            )

            if not results:
                # WHY continue on empty result: block=1000 means we waited 1s
                # and no events arrived. Loop again — the game may just be slow.
                continue

            for _stream, messages in results:
                for message_id, fields in messages:
                    state = await _process_message(
                        redis_client, stream_key, message_id, fields, state
                    )

            if state.game_status == "final":
                logger.info("Game %s ended — processor stopping.", game_id)
                break
    finally:
        await redis_client.aclose()


def run(game_id: str) -> None:
    """
    Entry point for running the processor as a standalone process.

    WHY separate process from poller: if the processor has a bug and crashes,
    events keep accumulating in the stream without being lost. When the
    processor restarts it picks up exactly where it left off via the consumer
    group's persisted offset.
    """
    asyncio.run(process_game(game_id))


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m src.processor <game_id>")
        sys.exit(1)
    run(sys.argv[1])
