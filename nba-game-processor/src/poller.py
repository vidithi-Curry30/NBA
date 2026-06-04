"""
NBA play-by-play poller.

Polls nba_api every 3 seconds, detects new events since the last poll,
and pushes each event to a Redis Stream. This is the entry point for all
game data — live or replayed — into the pipeline.
"""

import asyncio
import json
import logging
import os
import time
from typing import Optional

import redis.asyncio as aioredis
from dotenv import load_dotenv
from nba_api.live.nba.endpoints import playbyplay, scoreboard

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
# WHY 3 seconds: nba_api rate-limits at roughly 1 request/second per endpoint.
# 3s gives comfortable headroom while keeping latency acceptable for a stats
# application where sub-second freshness isn't required (unlike HFT order books).
POLL_INTERVAL = float(os.getenv("NBA_API_DELAY", "3"))
STREAM_KEY_TEMPLATE = "game_events:{game_id}"

# WHY exponential backoff with these bounds: start at 2s to avoid hammering a
# temporarily struggling API, cap at 30s so we don't wait half a period between
# retries. Standard production practice for any external HTTP dependency.
BACKOFF_INITIAL = 2.0
BACKOFF_MAX = 30.0


def _build_event(game_id: str, play: dict, home_team: str, away_team: str) -> dict:
    """
    Normalize a raw nba_api play dict into the canonical event schema.

    WHY a separate normalizer: nba_api field names change between API versions.
    Isolating the mapping here means only this function needs updating if the
    upstream schema changes; everything downstream consumes the stable schema.
    """
    action_type = str(play.get("actionType", "")).lower()
    sub_type = str(play.get("subType", "")).lower()

    # Determine event_type in terms the processor's state machine understands.
    if action_type in ("2pt", "3pt", "freethrow"):
        if play.get("shotResult", "").lower() == "made":
            event_type = "score"
        else:
            event_type = "missed shot"
    elif action_type == "substitution":
        event_type = "substitution"
    elif action_type == "period":
        event_type = "period start"
    elif action_type == "turnover":
        event_type = "turnover"
    elif action_type == "game":
        event_type = "end of game"
    else:
        event_type = action_type

    # WHY sub_type propagation: the live API emits substitutions as two separate
    # events — one with subType="out" (player leaving) and one with subType="in"
    # (player entering). State.py's _handle_substitution detects this pattern and
    # updates the roster correctly. Without sub_type, we can't tell which direction
    # the substitution goes from a single event.
    sub_type = str(play.get("subType", "")).lower() if action_type == "substitution" else ""

    return {
        "game_id": game_id,
        "event_type": event_type,
        "description": str(play.get("description", "")),
        "home_score": str(play.get("scoreHome", "")),
        "away_score": str(play.get("scoreAway", "")),
        "period": str(play.get("period", "")),
        "clock": str(play.get("clock", "").replace("PT", "").replace("M", ":").rstrip("S")),
        "player": str(play.get("playerNameI", "")),
        "sub_type": sub_type,
        "team": str(play.get("teamTricode", "")),
        "home_team": home_team,
        "away_team": away_team,
        "action_number": str(play.get("actionNumber", "")),
    }


async def _push_to_stream(
    redis_client: aioredis.Redis, game_id: str, event: dict
) -> None:
    """
    Publish a single event dict to the Redis Stream for this game.

    WHY Redis Stream instead of calling the processor directly: the stream
    provides persistence (events survive a processor crash), observability
    (the stream can be inspected independently), and decoupling (poller and
    processor share zero code and can be restarted independently).
    """
    stream_key = STREAM_KEY_TEMPLATE.format(game_id=game_id)
    # WHY maxlen with ~ (approximate trimming): keeps the stream bounded in
    # memory (~10k events covers any game) without the O(n) cost of exact trim.
    await redis_client.xadd(stream_key, event, maxlen=10_000, approximate=True)


async def _fetch_plays_with_backoff(
    game_id: str, attempt: int
) -> Optional[list[dict]]:
    """
    Call nba_api for play-by-play, returning None on failure with logged error.

    WHY this wrapper rather than inline try/except: keeps the retry logic in
    the caller (_poll_game) and the API call isolated here for easier mocking
    in tests and cleaner error attribution in logs.
    """
    try:
        pbp = playbyplay.PlayByPlay(game_id=game_id)
        return pbp.get_dict()["game"]["actions"]
    except Exception as exc:
        # WHY catch broad Exception here: nba_api raises a mix of requests
        # exceptions, JSON decode errors, and its own custom errors depending
        # on the failure mode. We log the type so it's still diagnosable.
        logger.warning("nba_api error (attempt %d): %s: %s", attempt, type(exc).__name__, exc)
        return None


async def _get_game_meta(game_id: str) -> tuple[str, str, str]:
    """
    Fetch home team, away team, and game status from the scoreboard.

    WHY separate scoreboard call: play-by-play actions don't always include
    team tricodes for every event; scoreboard is the reliable source for
    game-level metadata and status ("Final", "In Progress", etc.).
    """
    try:
        sb = scoreboard.ScoreBoard()
        games = sb.get_dict()["scoreboard"]["games"]
        for game in games:
            if game.get("gameId") == game_id:
                home = game.get("homeTeam", {}).get("teamTricode", "")
                away = game.get("awayTeam", {}).get("teamTricode", "")
                status = game.get("gameStatusText", "")
                return home, away, status
    except Exception as exc:
        logger.warning("Scoreboard fetch failed: %s", exc)
    return "", "", ""


async def poll_game(game_id: str) -> None:
    """
    Main polling loop: detect new events and push them to Redis Stream.

    WHY track last_action_number instead of polling all events each time:
    play-by-play returns the full game history on every call. Comparing
    against the last seen action number lets us push only the delta, avoiding
    duplicate events in the stream.
    """
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    last_action_number = -1
    backoff = BACKOFF_INITIAL
    attempt = 0

    logger.info("Starting poller for game %s", game_id)

    try:
        while True:
            home_team, away_team, game_status = await _get_game_meta(game_id)

            # WHY check "Final" before fetching plays: avoids one unnecessary
            # API call and makes the shutdown condition explicit in logs.
            if "final" in game_status.lower():
                logger.info("Game %s is Final — poller stopping.", game_id)
                break

            plays = await _fetch_plays_with_backoff(game_id, attempt)

            if plays is None:
                # WHY exponential backoff on failure, not fixed sleep:
                # a struggling API benefits from reduced load; hammering it
                # prolongs the outage. Cap prevents indefinitely long waits.
                wait = min(backoff * (2 ** attempt), BACKOFF_MAX)
                logger.info("Backing off %.1fs before retry.", wait)
                await asyncio.sleep(wait)
                attempt += 1
                continue

            attempt = 0
            backoff = BACKOFF_INITIAL

            new_events = [
                p for p in plays
                if int(p.get("actionNumber", 0)) > last_action_number
            ]

            for play in new_events:
                event = _build_event(game_id, play, home_team, away_team)
                await _push_to_stream(redis_client, game_id, event)
                action_num = int(play.get("actionNumber", last_action_number))
                if action_num > last_action_number:
                    last_action_number = action_num

            if new_events:
                logger.info("Pushed %d new events (last action %d)", len(new_events), last_action_number)

            await asyncio.sleep(POLL_INTERVAL)
    finally:
        await redis_client.aclose()


def run(game_id: str) -> None:
    """
    Entry point for running the poller as a standalone process.

    WHY a separate process (not a thread in processor.py): fault isolation —
    if nba_api is slow or throws, the poller can retry without touching the
    processor. Each component has one responsibility and can be debugged alone.
    """
    asyncio.run(poll_game(game_id))


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m src.poller <game_id>")
        sys.exit(1)
    run(sys.argv[1])
