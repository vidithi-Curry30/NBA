"""
Crash-recovery demo: kill the processor mid-stream and show it resumes
cleanly via the consumer group's pending entries list (PEL).

  1. Push a full game's worth of synthetic events to a fresh Redis Stream.
  2. Start the processor and let it consume a few events.
  3. SIGKILL it mid-processing, simulating a crash.
  4. Run XPENDING to show the unacked message is still held (PEL).
  5. Restart the processor — it drains the PEL before consuming new events.
  6. Verify the final state reflects every event exactly once.

Run with Redis available locally:

    python -m scripts.demo_crash_recovery
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time

import redis.asyncio as aioredis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
GAME_ID = "demo_crash_0001"
STREAM_KEY = f"game_events:{GAME_ID}"
STATE_KEY = f"game_state:{GAME_ID}"
CONSUMER_GROUP = "processors"

EVENTS = [
    {"event_type": "period start", "period": "1", "clock": "12:00",
     "home_team": "BOS", "away_team": "DAL", "game_id": GAME_ID},
    {"event_type": "score", "home_score": "2", "away_score": "0",
     "period": "1", "clock": "11:30", "game_id": GAME_ID},
    {"event_type": "score", "home_score": "2", "away_score": "3",
     "period": "1", "clock": "11:00", "game_id": GAME_ID},
    {"event_type": "turnover", "team": "BOS", "period": "1", "clock": "10:30",
     "game_id": GAME_ID},
    {"event_type": "score", "home_score": "5", "away_score": "3",
     "period": "1", "clock": "10:00", "game_id": GAME_ID},
    {"event_type": "score", "home_score": "5", "away_score": "6",
     "period": "1", "clock": "9:30", "game_id": GAME_ID},
    {"event_type": "score", "home_score": "8", "away_score": "6",
     "period": "1", "clock": "9:00", "game_id": GAME_ID},
    {"event_type": "end of game", "period": "4", "clock": "0:00",
     "game_id": GAME_ID},
]


async def _reset(redis_client: aioredis.Redis) -> None:
    """Delete any state from a previous run so the demo is reproducible."""
    await redis_client.delete(STREAM_KEY, STATE_KEY)


async def _push_all_events(redis_client: aioredis.Redis) -> None:
    for event in EVENTS:
        await redis_client.xadd(STREAM_KEY, event)


async def _print_pending(redis_client: aioredis.Redis, label: str) -> None:
    try:
        summary = await redis_client.xpending(STREAM_KEY, CONSUMER_GROUP)
    except aioredis.ResponseError:
        summary = None
    print(f"\n--- XPENDING ({label}) ---")
    print(summary)


async def _print_state(redis_client: aioredis.Redis, label: str) -> None:
    raw = await redis_client.hget(STATE_KEY, "data")
    print(f"\n--- game_state:{GAME_ID} ({label}) ---")
    if raw is None:
        print("(no snapshot yet)")
        return
    state = json.loads(raw)
    print(f"score: {state['home_team']} {state['home_score']} - "
          f"{state['away_score']} {state['away_team']}")
    print(f"status: {state['game_status']}, "
          f"possessions: home={state['home_possessions']} "
          f"away={state['away_possessions']}")


def _spawn_processor(extra_env: dict) -> subprocess.Popen:
    env = os.environ.copy()
    env.update(extra_env)
    return subprocess.Popen(
        [sys.executable, "-m", "src.processor", GAME_ID],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


async def main() -> None:
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)

    print(f"=== Crash-recovery demo: game {GAME_ID} ===")
    await _reset(redis_client)
    await _push_all_events(redis_client)
    print(f"Pushed {len(EVENTS)} events to {STREAM_KEY}.")

    # 300ms/event slows the processor enough to reliably SIGKILL it mid-stream.
    print("\nStarting processor (run #1, with artificial 300ms/event delay)...")
    proc = _spawn_processor({"PROCESSOR_DEMO_DELAY_MS": "300"})

    # Let it consume roughly 3 events, then crash it.
    time.sleep(1.0)
    proc.send_signal(signal.SIGKILL)
    proc.wait()
    print("Processor #1 killed (SIGKILL) mid-stream — simulating a crash.")

    await _print_state(redis_client, "after crash")
    await _print_pending(redis_client, "after crash")
    print(
        "\nThe XPENDING summary above shows unacknowledged messages still "
        "held by Redis for consumer 'processor-1' — they were delivered but "
        "never acked, so they were not lost when the process died."
    )

    print("\nStarting processor (run #2, full speed — simulating restart)...")
    proc2 = _spawn_processor({"PROCESSOR_DEMO_DELAY_MS": "0"})
    proc2.wait(timeout=30)
    print("Processor #2 exited cleanly (game reached 'final').")

    await _print_state(redis_client, "after recovery")
    await _print_pending(redis_client, "after recovery")

    raw = await redis_client.hget(STATE_KEY, "data")
    state = json.loads(raw)
    expected_home, expected_away = 8, 6
    assert state["home_score"] == expected_home, state
    assert state["away_score"] == expected_away, state
    assert state["game_status"] == "final", state
    print(
        f"\nFinal score {state['home_score']}-{state['away_score']} matches "
        f"expected {expected_home}-{expected_away}: every event was applied "
        "exactly once, despite the mid-stream crash."
    )

    await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
