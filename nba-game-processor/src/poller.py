"""
NBA play-by-play poller.

Polls nba_api every few seconds, detects new events since the last poll,
and pushes each event to a Redis Stream. This is the entry point for all
live game data into the pipeline.
"""

import asyncio
import logging
import os
from typing import Optional

import redis.asyncio as aioredis
from dotenv import load_dotenv
from nba_api.live.nba.endpoints import playbyplay, scoreboard

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
# nba_api rate-limits at roughly 1 req/s per endpoint; 3s leaves headroom.
POLL_INTERVAL = float(os.getenv("NBA_API_DELAY", "3"))
STREAM_KEY_TEMPLATE = "game_events:{game_id}"

BACKOFF_INITIAL = 2.0
BACKOFF_MAX = 30.0


def _build_event(game_id: str, play: dict, home_team: str, away_team: str) -> dict:
    """Normalize a raw nba_api play dict into the schema src/state.py expects."""
    action_type = str(play.get("actionType", "")).lower()

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

    # The live API emits substitutions as two events per swap, one with
    # subType="out" and one with subType="in" — state.py uses this to
    # update the on-court roster.
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
    stream_key = STREAM_KEY_TEMPLATE.format(game_id=game_id)
    # approximate=True keeps the stream bounded without an O(n) exact trim.
    await redis_client.xadd(stream_key, event, maxlen=10_000, approximate=True)


async def _fetch_plays_with_backoff(
    game_id: str, attempt: int
) -> Optional[list[dict]]:
    try:
        pbp = playbyplay.PlayByPlay(game_id=game_id)
        return pbp.get_dict()["game"]["actions"]
    except Exception as exc:
        logger.warning("nba_api error (attempt %d): %s: %s", attempt, type(exc).__name__, exc)
        return None


async def _get_game_meta(game_id: str) -> tuple[str, str, str]:
    """Team tricodes and game status come from the scoreboard, not play-by-play."""
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
    """Main polling loop: detect new events and push them to the Redis Stream."""
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    last_action_number = -1
    backoff = BACKOFF_INITIAL
    attempt = 0

    logger.info("Starting poller for game %s", game_id)

    try:
        while True:
            home_team, away_team, game_status = await _get_game_meta(game_id)

            if "final" in game_status.lower():
                logger.info("Game %s is Final — poller stopping.", game_id)
                break

            plays = await _fetch_plays_with_backoff(game_id, attempt)

            if plays is None:
                wait = min(backoff * (2 ** attempt), BACKOFF_MAX)
                logger.info("Backing off %.1fs before retry.", wait)
                await asyncio.sleep(wait)
                attempt += 1
                continue

            attempt = 0
            backoff = BACKOFF_INITIAL

            # play-by-play returns the full game history each call; only
            # push events newer than the last one we've seen.
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
    asyncio.run(poll_game(game_id))


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m src.poller <game_id>")
        sys.exit(1)
    run(sys.argv[1])
