"""
CLI tool for inspecting Redis Stream contents directly, independently of
the poller and processor.

Usage:
    python scripts/inspect_stream.py --game 0042300401
    python scripts/inspect_stream.py --game 0042300401 --tail 20
    python scripts/inspect_stream.py --game 0042300401 --follow
"""

import asyncio
import os
import time

import click
import redis.asyncio as aioredis
from dotenv import load_dotenv

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
STREAM_KEY_TEMPLATE = "game_events:{game_id}"


def _fmt_event(msg_id: str, fields: dict) -> str:
    """Format a stream entry for human-readable terminal output."""
    ts_ms = int(msg_id.split("-")[0])
    ts_s = ts_ms / 1000
    time_str = time.strftime("%H:%M:%S", time.localtime(ts_s))

    event_type = fields.get("event_type", "?").upper()
    period = fields.get("period", "?")
    clock = fields.get("clock", "?")
    home = fields.get("home_team", "?")
    away = fields.get("away_team", "?")
    home_score = fields.get("home_score", "-")
    away_score = fields.get("away_score", "-")
    desc = fields.get("description", "")[:60]

    score_str = f"{home} {home_score} - {away_score} {away}" if home_score else ""
    return f"[{time_str}] P{period} {clock:>6}  {event_type:<15}  {score_str:<25}  {desc}"


async def _inspect(game_id: str, tail: int, follow: bool) -> None:
    """Print recent stream entries, optionally polling for new ones."""
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    stream_key = STREAM_KEY_TEMPLATE.format(game_id=game_id)

    try:
        # XREVRANGE gets the last `tail` entries (newest first) without
        # scanning from the start; reverse for chronological display.
        entries = await redis_client.xrevrange(stream_key, count=tail)
        if not entries:
            click.echo(f"No events found for game {game_id}. Is the poller running?")
            return

        entries.reverse()
        click.echo(f"\n--- Last {len(entries)} events for game {game_id} ---\n")
        for msg_id, fields in entries:
            click.echo(_fmt_event(msg_id, fields))

        if not follow:
            count = await redis_client.xlen(stream_key)
            click.echo(f"\nTotal events in stream: {count}")
            return

        # Follow mode: block-wait for new entries.
        click.echo("\n--- Following stream (Ctrl-C to stop) ---\n")
        last_id = entries[-1][0] if entries else "0"

        while True:
            # 2s timeout keeps this responsive to Ctrl-C without busy-waiting.
            new_entries = await redis_client.xread(
                streams={stream_key: last_id}, count=50, block=2000
            )
            if new_entries:
                for _key, messages in new_entries:
                    for msg_id, fields in messages:
                        click.echo(_fmt_event(msg_id, fields))
                        last_id = msg_id

    except asyncio.CancelledError:
        pass
    finally:
        await redis_client.aclose()


@click.command()
@click.option("--game", required=True, help="NBA game ID (e.g. 0042300401)")
@click.option("--tail", default=50, show_default=True, help="Number of recent events to show")
@click.option("--follow", is_flag=True, help="Keep watching for new events (like tail -f)")
def main(game: str, tail: int, follow: bool) -> None:
    """Inspect the Redis Stream for a game without touching the pipeline."""
    try:
        asyncio.run(_inspect(game, tail, follow))
    except KeyboardInterrupt:
        click.echo("\nStopped.")


if __name__ == "__main__":
    main()
