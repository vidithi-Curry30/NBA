"""
End-to-end API smoke test.

Populates Redis with a synthetic game via the processor, then hits every
API endpoint and prints a pass/fail report.

Usage:
    # Start Redis first (docker-compose up redis), then:
    python -m scripts.test_api

Or against a live stack:
    BASE_URL=http://localhost:8000 python -m scripts.test_api
"""

import asyncio
import json
import os
import sys
import time

import redis.asyncio as aioredis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
TEST_GAME_ID = "TEST_SMOKE_001"

# ---------------------------------------------------------------------------
# Seed data — push synthetic events to the stream so the processor
# (if running) can build state, OR write state directly for API-only tests.
# ---------------------------------------------------------------------------

SYNTHETIC_STATE = {
    "game_id": TEST_GAME_ID,
    "home_team": "BOS",
    "away_team": "DAL",
    "home_score": 78,
    "away_score": 71,
    "period": 3,
    "game_clock": "4:32",
    "game_status": "in_progress",
    "pace": 102.4,
    "possessions": 61,
    "last_10_possessions": [
        "home_score", "away_score", "home_score", "home_score", "no_score",
        "away_score", "home_score", "no_score", "home_score", "no_score"
    ],
    "current_possession": "home",
    "player_fouls": {
        "Tatum": 3,
        "Brown": 2,
        "Doncic": 4,
        "Irving": 1,
    },
    "home_players_on_court": ["Tatum", "Brown", "White", "Horford", "Porzingis"],
    "away_players_on_court": ["Doncic", "Irving", "Finney-Smith", "Lively", "Washington"],
}


async def seed_redis(r: aioredis.Redis) -> None:
    state_key = f"game_state:{TEST_GAME_ID}"
    stream_key = f"game_events:{TEST_GAME_ID}"

    # Write game state directly (bypasses processor — fine for API testing)
    await r.hset(state_key, "data", json.dumps(SYNTHETIC_STATE))

    # Write a handful of stream events so /events has data to return
    event_types = [
        {"event_type": "score", "team": "home", "player": "Tatum", "points": "3"},
        {"event_type": "score", "team": "away", "player": "Doncic", "points": "2"},
        {"event_type": "rebound", "team": "home", "player": "Horford"},
        {"event_type": "foul", "team": "away", "player": "Doncic"},
        {"event_type": "score", "team": "home", "player": "Brown", "points": "2"},
        {"event_type": "turnover", "team": "away", "player": "Irving"},
        {"event_type": "score", "team": "home", "player": "White", "points": "3"},
    ]
    for ev in event_types:
        await r.xadd(stream_key, ev)

    print(f"  Seeded game_state and {len(event_types)} stream events for {TEST_GAME_ID}")


async def cleanup_redis(r: aioredis.Redis) -> None:
    await r.delete(f"game_state:{TEST_GAME_ID}")
    await r.delete(f"game_events:{TEST_GAME_ID}")


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

async def fetch(url: str) -> tuple[int, dict | None]:
    import urllib.request
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:
        return 0, None


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

PASS = "✓"
FAIL = "✗"
SKIP = "–"


def check(label: str, condition: bool, detail: str = "") -> bool:
    icon = PASS if condition else FAIL
    suffix = f"  {detail}" if detail else ""
    print(f"  {icon} {label}{suffix}")
    return condition


async def run_tests() -> int:
    failures = 0

    print(f"\n{'='*60}")
    print(f"NBA API Smoke Test  →  {BASE_URL}")
    print(f"{'='*60}\n")

    # -- Health check -------------------------------------------------------
    print("[ /health ]")
    status, body = await fetch(f"{BASE_URL}/health")
    ok = check("returns 200 with status=ok", status == 200 and body == {"status": "ok"}, str(body))
    failures += not ok

    # -- Seed Redis ---------------------------------------------------------
    print("\n[ Seeding Redis ]")
    try:
        r = aioredis.from_url(REDIS_URL, decode_responses=True)
        await seed_redis(r)
    except Exception as e:
        print(f"  {FAIL} Cannot connect to Redis at {REDIS_URL}: {e}")
        print("  Skipping all endpoint tests — start Redis first.\n")
        return 1

    gid = TEST_GAME_ID

    try:
        # -- /game/{id}/state -----------------------------------------------
        print(f"\n[ /game/{gid}/state ]")
        status, body = await fetch(f"{BASE_URL}/game/{gid}/state")
        ok = check("returns 200", status == 200, f"(got {status})")
        failures += not ok
        if ok:
            check("home_team=BOS", body.get("home_team") == "BOS")
            check("home_score=78", body.get("home_score") == 78)
            check("period=3", body.get("period") == 3)

        # -- /game/{id}/momentum --------------------------------------------
        print(f"\n[ /game/{gid}/momentum ]")
        status, body = await fetch(f"{BASE_URL}/game/{gid}/momentum")
        ok = check("returns 200", status == 200, f"(got {status})")
        failures += not ok
        if ok:
            check("momentum_score is int", isinstance(body.get("momentum_score"), int),
                  str(body.get("momentum_score")))
            check("z_score present", "z_score" in body)
            check("interpretation present", bool(body.get("interpretation")))
            # With 6 home / 1 away scoring possessions: z = (6 - 3.5) / 1.32 ≈ 1.9 → BOS on a run
            check("interpretation is non-empty string", bool(body.get("interpretation")),
                  body.get("interpretation"))

        # -- /game/{id}/pace ------------------------------------------------
        print(f"\n[ /game/{gid}/pace ]")
        status, body = await fetch(f"{BASE_URL}/game/{gid}/pace")
        ok = check("returns 200", status == 200, f"(got {status})")
        failures += not ok
        if ok:
            check("pace=102.4", abs(body.get("pace", 0) - 102.4) < 0.01,
                  str(body.get("pace")))
            check("league_average present", "league_average" in body)
            check("pace_differential present", "pace_differential" in body)

        # -- /game/{id}/efficiency ------------------------------------------
        print(f"\n[ /game/{gid}/efficiency ]")
        status, body = await fetch(f"{BASE_URL}/game/{gid}/efficiency")
        ok = check("returns 200", status == 200, f"(got {status})")
        failures += not ok
        if ok:
            check("home_team=BOS", body.get("home_team") == "BOS")
            check("home_offensive_rating >= 0", body.get("home_offensive_rating", -1) >= 0)
            check("away_offensive_rating >= 0", body.get("away_offensive_rating", -1) >= 0)

        # -- /game/{id}/win-probability ------------------------------------
        print(f"\n[ /game/{gid}/win-probability ]")
        status, body = await fetch(f"{BASE_URL}/game/{gid}/win-probability")
        ok = check("returns 200", status == 200, f"(got {status})")
        failures += not ok
        if ok:
            h = body.get("home_win_probability", -1)
            a = body.get("away_win_probability", -1)
            check("probabilities sum to 1.0", abs(h + a - 1.0) < 0.001,
                  f"home={h} away={a}")
            check("home favored (BOS +7 in Q3)", h > 0.60, f"home_wp={h:.3f}")
            check("not final", body.get("is_final") is False)

        # -- /game/{id}/foul-trouble ---------------------------------------
        print(f"\n[ /game/{gid}/foul-trouble ]")
        status, body = await fetch(f"{BASE_URL}/game/{gid}/foul-trouble")
        ok = check("returns 200", status == 200, f"(got {status})")
        failures += not ok
        if ok:
            check("period=3", body.get("period") == 3)
            # Tatum has 3 fouls in Q3 → in trouble (>=4 threshold pre-Q4 is 4, so 3 not in trouble)
            # Doncic has 4 fouls → in trouble
            away_trouble = body.get("away_foul_trouble", {})
            check("Doncic (4 fouls) in trouble",
                  "Doncic" in away_trouble,
                  f"away_trouble={away_trouble}")
            home_trouble = body.get("home_foul_trouble", {})
            check("Tatum (3 fouls) NOT in trouble (threshold=4)",
                  "Tatum" not in home_trouble,
                  f"home_trouble={home_trouble}")

        # -- /game/{id}/events ---------------------------------------------
        print(f"\n[ /game/{gid}/events ]")
        status, body = await fetch(f"{BASE_URL}/game/{gid}/events")
        ok = check("returns 200", status == 200, f"(got {status})")
        failures += not ok
        if ok:
            count = body.get("count", 0)
            check("at least 7 events returned", count >= 7, f"count={count}")
            first = body.get("events", [{}])[0]
            check("events have stream_id", "stream_id" in first)
            check("events have fields", "fields" in first)

        # -- 404 on unknown game -------------------------------------------
        print(f"\n[ 404 on unknown game ]")
        status, body = await fetch(f"{BASE_URL}/game/NONEXISTENT_GAME/state")
        check("returns 404", status == 404, f"(got {status})")

    finally:
        await cleanup_redis(r)
        await r.aclose()
        print(f"\n  (Cleaned up test keys from Redis)")

    # -- Summary ------------------------------------------------------------
    print(f"\n{'='*60}")
    if failures == 0:
        print(f"  {PASS} All tests passed")
    else:
        print(f"  {FAIL} {failures} test(s) failed")
    print(f"{'='*60}\n")

    return failures


if __name__ == "__main__":
    sys.exit(asyncio.run(run_tests()))
