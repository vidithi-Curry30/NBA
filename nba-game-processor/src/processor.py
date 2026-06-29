"""
Redis Stream consumer: reads events, updates GameState, writes a snapshot.

This is the hot path of the pipeline. It reads events one at a time from the
Redis Stream, applies them to a GameState, and writes the result to a Redis
Hash that the API reads in O(1).

Scaling model
-------------
One processor process per game — games are independent and share nothing
(separate stream keys, consumer groups, and state hashes). This is the right
unit of parallelism: the bottleneck is I/O with Redis, not CPU, and a single
asyncio process handles that easily at NBA play-by-play rates (~1 event/3s).

Each processor identifies itself with a unique CONSUMER_NAME within the
shared consumer group. Two processors with the same name targeting the same
game would each only receive a subset of messages and produce divergent state,
so uniqueness per game matters. The default is "processor-<hostname>"; on a
container platform (Fly.io, k8s) the hostname is unique per replica, so the
default is safe. Pass --consumer-name or CONSUMER_NAME to override.
"""

import asyncio
import logging
import os
import socket

import redis.asyncio as aioredis
from dotenv import load_dotenv

from src.state import GameState

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
STREAM_KEY_TEMPLATE = "game_events:{game_id}"
STATE_KEY_TEMPLATE = "game_state:{game_id}"

CONSUMER_GROUP = "processors"
# Default to hostname so each container/machine gets a unique name without
# coordination.  Override via --consumer-name CLI arg or CONSUMER_NAME env var.
_DEFAULT_CONSUMER_NAME = f"processor-{socket.gethostname()}"

# Keep state queryable for a few hours after a game ends without letting
# Redis grow unboundedly across all games.
STATE_TTL_SECONDS = 4 * 60 * 60

# Wake at most once per second when idle.
XREADGROUP_BLOCK_MS = 1000

# Optional artificial delay per message, used by demo_crash_recovery.py to
# slow processing enough to reliably kill -9 mid-stream. Off by default.
PROCESSOR_DEMO_DELAY_MS = int(os.getenv("PROCESSOR_DEMO_DELAY_MS", "0"))


async def _ensure_consumer_group(
    redis_client: aioredis.Redis, stream_key: str
) -> None:
    """Create the consumer group (and stream) if it doesn't exist yet."""
    try:
        await redis_client.xgroup_create(
            stream_key, CONSUMER_GROUP, id="0", mkstream=True
        )
        logger.info("Consumer group '%s' created on %s", CONSUMER_GROUP, stream_key)
    except aioredis.ResponseError as exc:
        if "BUSYGROUP" in str(exc):
            logger.debug("Consumer group already exists — continuing.")
        else:
            raise


async def _write_state_snapshot(
    redis_client: aioredis.Redis, state: GameState
) -> None:
    """Serialize GameState to JSON and write it to a Redis Hash with a TTL."""
    state_key = STATE_KEY_TEMPLATE.format(game_id=state.game_id)
    payload = state.model_dump_json()

    async with redis_client.pipeline(transaction=True) as pipe:
        pipe.hset(state_key, mapping={"data": payload})
        pipe.expire(state_key, STATE_TTL_SECONDS)
        await pipe.execute()


async def _load_state_snapshot(
    redis_client: aioredis.Redis, game_id: str
) -> GameState | None:
    """Load the last materialized snapshot, if one exists, for crash recovery."""
    state_key = STATE_KEY_TEMPLATE.format(game_id=game_id)
    raw = await redis_client.hget(state_key, "data")
    if raw is None:
        return None
    return GameState.model_validate_json(raw)


async def _drain_pending_messages(
    redis_client: aioredis.Redis,
    stream_key: str,
    state: GameState,
    consumer_name: str,
) -> GameState:
    """
    Reprocess any messages left in this consumer's pending entries list (PEL).

    XREADGROUP with id=">" only returns messages never delivered to any
    consumer in this group — it does not redeliver a message already
    delivered to (and still pending for) this same consumer. After a crash,
    those are exactly the messages XPENDING shows as outstanding. Reading
    with id="0" asks Redis for this consumer's own PEL, oldest first, so
    they're applied (and acked) before any new events are consumed.
    """
    while True:
        results = await redis_client.xreadgroup(
            groupname=CONSUMER_GROUP,
            consumername=consumer_name,
            streams={stream_key: "0"},
            count=10,
        )
        messages = results[0][1] if results else []
        if not messages:
            return state

        for message_id, fields in messages:
            state = await _process_message(
                redis_client, stream_key, message_id, fields, state, consumer_name
            )

        if state.game_status == "final":
            return state


async def _process_message(
    redis_client: aioredis.Redis,
    stream_key: str,
    message_id: str,
    fields: dict,
    state: GameState,
    consumer_name: str,
) -> GameState:
    """Apply one stream message to the game state and acknowledge it."""
    if PROCESSOR_DEMO_DELAY_MS:
        await asyncio.sleep(PROCESSOR_DEMO_DELAY_MS / 1000.0)

    state.update(fields)
    await _write_state_snapshot(redis_client, state)

    # Ack after the snapshot write: this is at-least-once delivery, so a
    # crash before the ack just means the message is reprocessed on restart.
    # Acking before the write would risk a stale snapshot with no way to
    # recover the missed event.
    await redis_client.xack(stream_key, CONSUMER_GROUP, message_id)
    logger.debug("Processed and acked message %s (consumer=%s)", message_id, consumer_name)
    return state


async def process_game(game_id: str, consumer_name: str = _DEFAULT_CONSUMER_NAME) -> None:
    """Main consumer loop: read events from the stream and maintain game state."""
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    stream_key = STREAM_KEY_TEMPLATE.format(game_id=game_id)

    await _ensure_consumer_group(redis_client, stream_key)

    # Rehydrate from the last snapshot (if any) so that replaying PEL
    # messages after a restart doesn't double-count already-acked events.
    state = await _load_state_snapshot(redis_client, game_id)
    if state is None:
        state = GameState(game_id=game_id)
    logger.info("Processor started for game %s (consumer=%s)", game_id, consumer_name)

    state = await _drain_pending_messages(redis_client, stream_key, state, consumer_name)

    try:
        while state.game_status != "final":
            results = await redis_client.xreadgroup(
                groupname=CONSUMER_GROUP,
                consumername=consumer_name,
                streams={stream_key: ">"},
                count=10,
                block=XREADGROUP_BLOCK_MS,
            )

            if not results:
                continue

            for _stream, messages in results:
                for message_id, fields in messages:
                    state = await _process_message(
                        redis_client, stream_key, message_id, fields, state, consumer_name
                    )

            if state.game_status == "final":
                logger.info("Game %s ended — processor stopping.", game_id)
                break
    finally:
        await redis_client.aclose()


def run(game_id: str, consumer_name: str = _DEFAULT_CONSUMER_NAME) -> None:
    asyncio.run(process_game(game_id, consumer_name))


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="NBA game stream processor")
    parser.add_argument("game_id", nargs="?", help="NBA game ID to process")
    parser.add_argument(
        "--consumer-name",
        default=os.getenv("CONSUMER_NAME", _DEFAULT_CONSUMER_NAME),
        help=(
            "Unique consumer name within the Redis consumer group. "
            "Defaults to 'processor-<hostname>'. Must be unique per game — "
            "two processors with the same name on the same game would each "
            "receive only a subset of messages and produce divergent state."
        ),
    )
    args = parser.parse_args()

    game_id = args.game_id or os.getenv("GAME_ID")
    if not game_id:
        parser.print_usage()
        print("error: game_id required (positional arg or GAME_ID env var)")
        sys.exit(1)

    run(game_id, args.consumer_name)
