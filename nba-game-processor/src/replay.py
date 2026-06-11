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
from dotenv import load_dotenv
from nba_api.stats.endpoints import playbyplayv2

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

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


def _normalize_historical_event(game_id: str, row: dict) -> dict:
    """Convert a playbyplayv2 row into the same event schema the poller produces."""
    event_type_id = str(row.get("EVENTMSGTYPE", ""))
    # nba_api event type IDs: 1=made shot, 2=missed shot, 3=free throw,
    # 4=rebound, 5=turnover, 6=foul, 8=substitution, 12=period start, 13=end.
    desc = str(row.get("HOMEDESCRIPTION") or row.get("VISITORDESCRIPTION") or "")

    if event_type_id == "1":
        event_type = "score"
    elif event_type_id == "3":
        # Type 3 covers both made and missed free throws; nba_api prefixes
        # missed ones with "MISS" in the description.
        event_type = "missed shot" if desc.upper().startswith("MISS") else "score"
    elif event_type_id == "2":
        event_type = "missed shot"
    elif event_type_id == "5":
        event_type = "turnover"
    elif event_type_id == "8":
        event_type = "substitution"
    elif event_type_id == "12":
        event_type = "period start"
    elif event_type_id == "13":
        event_type = "end of game"
    else:
        event_type = "other"

    raw_clock = str(row.get("PCTIMESTRING", "12:00"))
    home_score_str = str(row.get("SCORE", "") or "")
    home_score, away_score = "", ""
    if "  -  " in home_score_str or " - " in home_score_str:
        parts = home_score_str.replace("  -  ", " - ").split(" - ")
        if len(parts) == 2:
            away_score, home_score = parts[0].strip(), parts[1].strip()

    return {
        "game_id": game_id,
        "event_type": event_type,
        "description": desc,
        "home_score": home_score,
        "away_score": away_score,
        "period": str(row.get("PERIOD", "")),
        "clock": raw_clock,
        "player": str(row.get("PLAYER1_NAME", "")),
        "player_in": str(row.get("PLAYER2_NAME", "")),
        "player_out": str(row.get("PLAYER1_NAME", "")),
        "team": str(row.get("PLAYER1_TEAM_ABBREVIATION", "")),
        "home_team": "",
        "away_team": "",
        "action_number": str(row.get("EVENTNUM", "")),
    }


async def _push_event(redis_client: aioredis.Redis, game_id: str, event: dict) -> None:
    stream_key = STREAM_KEY_TEMPLATE.format(game_id=game_id)
    await redis_client.xadd(stream_key, event, maxlen=10_000, approximate=True)


async def _run_replay(game_id: str, speed: float) -> None:
    """Fetch historical play-by-play and push events paced by the real game clock."""
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)

    try:
        logger.info("Fetching play-by-play for game %s from nba_api...", game_id)
        pbp = playbyplayv2.PlayByPlayV2(game_id=game_id)
        rows = pbp.get_data_frames()[0].to_dict(orient="records")
        logger.info("Fetched %d events.", len(rows))

        prev_clock_seconds: float | None = None
        prev_period: int | None = None

        for row in rows:
            event = _normalize_historical_event(game_id, row)

            try:
                period = int(row.get("PERIOD", 1))
                parts = str(row.get("PCTIMESTRING", "12:00")).split(":")
                mins, secs = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
                clock_seconds = mins * 60 + secs

                if prev_clock_seconds is not None and prev_period == period:
                    delta = (prev_clock_seconds - clock_seconds) / speed
                    # Clock resets to 12:00 at period boundaries, which would
                    # otherwise produce a large negative delta.
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
