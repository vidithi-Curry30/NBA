"""
Replay mode: feed historical play-by-play through the live pipeline.

Fetches a completed game's events from nba_api and pushes them through the
same Redis Stream the live poller uses, so the processor consumes them
exactly as it would live events.
"""

import asyncio
import logging
import os

import click
import redis.asyncio as aioredis
import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# The CDN endpoint doesn't require authentication or special headers and isn't
# rate-limited the way stats.nba.com is. It serves the same live play-by-play
# data but is cached at the edge, so it works reliably from home networks.
CDN_URL = "https://cdn.nba.com/static/json/liveData/playbyplay/playbyplay_{game_id}.json"
CDN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
}

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
STREAM_KEY_TEMPLATE = "game_events:{game_id}"

# A few known-good completed games for --list-games. nba_api's game finder
# needs a date range, so this is just a convenience shortlist.
SAMPLE_GAMES = [
    ("0022301214", "2024-04-14", "BOS vs MIA — Regular Season"),
    ("0022301215", "2024-04-14", "LAL vs GSW — Regular Season"),
    ("0042300401", "2024-05-21", "BOS vs IND — Conference Finals Game 1"),
    ("0042300402", "2024-05-23", "BOS vs IND — Conference Finals Game 2"),
    ("0042300501", "2024-06-06", "BOS vs DAL — Finals Game 1"),
]


def _fetch_play_by_play(game_id: str) -> tuple[list[dict], str, str]:
    """
    Fetch play-by-play from the NBA CDN. Returns (actions, home_team, away_team).

    The CDN uses the same live-data format as the live poller, so we reuse
    the same field names. This endpoint is publicly accessible without auth
    and doesn't rate-limit home IP addresses the way stats.nba.com does.
    """
    url = CDN_URL.format(game_id=game_id)
    resp = requests.get(url, headers=CDN_HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    game = data["game"]
    home_team = game.get("homeTeam", {}).get("teamTricode", "")
    away_team = game.get("awayTeam", {}).get("teamTricode", "")
    actions = game.get("actions", [])
    return actions, home_team, away_team


def _normalize_cdn_event(game_id: str, play: dict, home_team: str, away_team: str) -> dict:
    """Convert a CDN action dict into the same schema the live poller produces."""
    action_type = str(play.get("actionType", "")).lower()

    if action_type in ("2pt", "3pt", "freethrow"):
        event_type = "score" if play.get("shotResult", "").lower() == "made" else "missed shot"
    elif action_type == "substitution":
        event_type = "substitution"
    elif action_type == "rebound":
        event_type = "rebound"
    elif action_type == "period":
        event_type = "period start"
    elif action_type == "turnover":
        event_type = "turnover"
    elif action_type == "foul":
        event_type = "foul"
    elif action_type == "game":
        event_type = "end of game"
    else:
        event_type = action_type

    sub_type = str(play.get("subType", "")).lower() if action_type in ("substitution", "rebound") else ""
    raw_clock = str(play.get("clock", "PT12M00S")).replace("PT", "").replace("M", ":").rstrip("S")

    return {
        "game_id": game_id,
        "event_type": event_type,
        "description": str(play.get("description", "")),
        "home_score": str(play.get("scoreHome", "")),
        "away_score": str(play.get("scoreAway", "")),
        "period": str(play.get("period", "")),
        "clock": raw_clock,
        "player": str(play.get("playerNameI", "")),
        "sub_type": sub_type,
        "team": str(play.get("teamTricode", "")),
        "home_team": home_team,
        "away_team": away_team,
        "action_number": str(play.get("actionNumber", "")),
    }


async def _push_event(redis_client: aioredis.Redis, game_id: str, event: dict) -> None:
    stream_key = STREAM_KEY_TEMPLATE.format(game_id=game_id)
    await redis_client.xadd(stream_key, event, maxlen=10_000, approximate=True)


async def _run_replay(game_id: str, speed: float) -> None:
    """Fetch historical play-by-play and push events paced by the real game clock."""
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)

    try:
        logger.info("Fetching play-by-play for game %s from NBA CDN...", game_id)
        actions, home_team, away_team = _fetch_play_by_play(game_id)
        logger.info("Fetched %d events. %s (home) vs %s (away)", len(actions), home_team, away_team)

        prev_clock_seconds: float | None = None
        prev_period: int | None = None

        for play in actions:
            event = _normalize_cdn_event(game_id, play, home_team, away_team)

            try:
                period = int(play.get("period", 1))
                raw_clock = str(play.get("clock", "PT12M00S"))
                clock_str = raw_clock.replace("PT", "").replace("M", ":").rstrip("S")
                parts = clock_str.split(":")
                mins = int(parts[0])
                secs = int(float(parts[1])) if len(parts) > 1 else 0
                clock_seconds = mins * 60 + secs

                if prev_clock_seconds is not None and prev_period == period:
                    delta = (prev_clock_seconds - clock_seconds) / speed
                    # Clock resets at period boundaries — skip negative deltas.
                    if delta > 0:
                        await asyncio.sleep(delta)

                prev_clock_seconds = clock_seconds
                prev_period = period
            except (ValueError, IndexError):
                pass

            await _push_event(redis_client, game_id, event)

        logger.info("Replay complete for game %s", game_id)
    finally:
        await redis_client.aclose()


@click.command()
@click.option("--game", default=None, help="NBA game ID to replay (e.g. 0042300401)")
@click.option(
    "--speed",
    default=10.0,
    show_default=True,
    help="Speed multiplier (10 = 10x real time)",
)
@click.option("--list-games", is_flag=True, help="Print sample completed games and exit")
def main(game: str | None, speed: float, list_games: bool) -> None:
    """Replay a historical NBA game through the live processing pipeline."""
    if list_games:
        click.echo("\nRecent completed games available for replay:\n")
        click.echo(f"  {'Game ID':<15} {'Date':<12} Description")
        click.echo(f"  {'-'*15} {'-'*12} {'-'*40}")
        for gid, date, desc in SAMPLE_GAMES:
            click.echo(f"  {gid:<15} {date:<12} {desc}")
        click.echo(
            "\nExample: python -m src.replay --game 0042300401 --speed 20\n"
        )
        return

    if not game:
        click.echo("Error: --game is required. Use --list-games to see available IDs.")
        raise click.Abort()

    click.echo(f"Starting replay for game {game} at {speed}x speed...")
    asyncio.run(_run_replay(game, speed))


if __name__ == "__main__":
    main()
