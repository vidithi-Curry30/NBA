"""
Multi-game scaling demo: run two processor instances concurrently, each
consuming a different game's Redis Stream, and check both reach the correct
final state independently.

Run with Redis available locally:

    python -m scripts.demo_multi_game
"""

import asyncio
import json
import os
import subprocess
import sys

import redis.asyncio as aioredis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

GAME_A = "demo_multi_a"
GAME_B = "demo_multi_b"

EVENTS_A = [
    {"event_type": "period start", "period": "1", "clock": "12:00",
     "home_team": "BOS", "away_team": "DAL", "game_id": GAME_A},
    {"event_type": "score", "home_score": "3", "away_score": "0",
     "period": "1", "clock": "11:30", "game_id": GAME_A},
    {"event_type": "score", "home_score": "3", "away_score": "2",
     "period": "1", "clock": "11:00", "game_id": GAME_A},
    {"event_type": "score", "home_score": "6", "away_score": "2",
     "period": "1", "clock": "10:30", "game_id": GAME_A},
    {"event_type": "end of game", "period": "4", "clock": "0:00", "game_id": GAME_A},
]

EVENTS_B = [
    {"event_type": "period start", "period": "1", "clock": "12:00",
     "home_team": "LAL", "away_team": "GSW", "game_id": GAME_B},
    {"event_type": "score", "home_score": "0", "away_score": "2",
     "period": "1", "clock": "11:40", "game_id": GAME_B},
    {"event_type": "score", "home_score": "0", "away_score": "5",
     "period": "1", "clock": "11:10", "game_id": GAME_B},
    {"event_type": "turnover", "team": "LAL", "period": "1", "clock": "10:50",
     "game_id": GAME_B},
    {"event_type": "score", "home_score": "3", "away_score": "5",
     "period": "1", "clock": "10:20", "game_id": GAME_B},
    {"event_type": "end of game", "period": "4", "clock": "0:00", "game_id": GAME_B},
]


async def _reset_and_seed(redis_client: aioredis.Redis) -> None:
    for game_id, events in ((GAME_A, EVENTS_A), (GAME_B, EVENTS_B)):
        stream_key = f"game_events:{game_id}"
        state_key = f"game_state:{game_id}"
        await redis_client.delete(stream_key, state_key)
        for event in events:
            await redis_client.xadd(stream_key, event)
    print(f"Seeded {len(EVENTS_A)} events for {GAME_A} and "
          f"{len(EVENTS_B)} events for {GAME_B}.")


async def _print_state(redis_client: aioredis.Redis, game_id: str) -> None:
    raw = await redis_client.hget(f"game_state:{game_id}", "data")
    if raw is None:
        print(f"{game_id}: (no snapshot)")
        return
    state = json.loads(raw)
    print(
        f"{game_id}: {state['home_team']} {state['home_score']} - "
        f"{state['away_score']} {state['away_team']} "
        f"(status={state['game_status']})"
    )


async def main() -> None:
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    await _reset_and_seed(redis_client)

    print("\nStarting two processor instances concurrently — one per game...")
    proc_a = subprocess.Popen([sys.executable, "-m", "src.processor", GAME_A])
    proc_b = subprocess.Popen([sys.executable, "-m", "src.processor", GAME_B])

    proc_a.wait(timeout=30)
    proc_b.wait(timeout=30)
    print("Both processors exited (each reached 'final' independently).")

    print("\n--- Final state for both games ---")
    await _print_state(redis_client, GAME_A)
    await _print_state(redis_client, GAME_B)

    raw_a = await redis_client.hget(f"game_state:{GAME_A}", "data")
    raw_b = await redis_client.hget(f"game_state:{GAME_B}", "data")
    state_a = json.loads(raw_a)
    state_b = json.loads(raw_b)
    assert state_a["home_score"] == 6 and state_a["away_score"] == 2
    assert state_b["home_score"] == 3 and state_b["away_score"] == 5
    assert state_a["game_status"] == "final"
    assert state_b["game_status"] == "final"
    await redis_client.aclose()

    print(
        "\nBoth games reached the correct final score independently and "
        "concurrently. Adding capacity for more simultaneous games just "
        "means running another `python -m src.processor <game_id>` process."
    )


if __name__ == "__main__":
    asyncio.run(main())
