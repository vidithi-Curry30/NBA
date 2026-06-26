"""
Terminal UI for NBA game replay.

Reads live game state from the Redis materialized view and renders a
live-updating scoreboard. Pair with `python -m src.replay` in another
terminal (or the same machine) to watch a historical game unfold in
real time.

Usage:
    python -m src.tui --game 0042300401
"""

import asyncio
import math
import os
import sys

import click
import redis.asyncio as aioredis
from dotenv import load_dotenv
from rich.columns import Columns
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.state import GameState

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
STATE_KEY_TEMPLATE = "game_state:{game_id}"
REFRESH_INTERVAL = 1.0  # seconds between display refreshes


def _win_prob_bar(prob: float, width: int = 20) -> Text:
    """Render a two-color bar: home (blue) | away (red), proportional to win prob."""
    home_blocks = round(prob * width)
    away_blocks = width - home_blocks
    bar = Text()
    bar.append("█" * home_blocks, style="bold blue")
    bar.append("█" * away_blocks, style="bold red")
    return bar


def _momentum_bar(possessions: list[str], home_team: str, away_team: str) -> Text:
    """Last 10 possessions as colored dots: blue=home score, red=away, grey=turnover."""
    bar = Text()
    for p in possessions:
        if p == "home_score":
            bar.append("●", style="bold blue")
        elif p == "away_score":
            bar.append("●", style="bold red")
        else:
            bar.append("○", style="dim")
        bar.append(" ")
    return bar


def _foul_trouble_text(state: GameState) -> Text:
    trouble = state.foul_trouble_players()
    if not trouble:
        return Text("None", style="dim")
    t = Text()
    for i, (player, fouls) in enumerate(trouble.items()):
        if fouls >= 6:
            style = "bold red"
            label = f"{player} (FOULED OUT)"
        else:
            style = "yellow"
            label = f"{player} ({fouls} fouls)"
        t.append(label, style=style)
        if i < len(trouble) - 1:
            t.append("  ")
    return t


def _period_label(period: int) -> str:
    if period <= 4:
        return f"Q{period}"
    return f"OT{period - 4}"


def _compute_win_prob(state: GameState) -> float:
    """Import lazily so TUI doesn't fail if model file missing."""
    try:
        from src.win_probability import predict_win_probability
        return predict_win_probability(state)
    except Exception:
        return 0.5


def _build_display(state: GameState | None, game_id: str) -> Panel:
    if state is None:
        return Panel(
            Text("Waiting for game data... (is the processor running?)", style="dim"),
            title=f"NBA Live — {game_id}",
            border_style="dim",
        )

    home = state.home_team or "HOME"
    away = state.away_team or "AWAY"

    # --- Scoreboard ---
    score_table = Table.grid(padding=(0, 3))
    score_table.add_column(justify="right", min_width=6)
    score_table.add_column(justify="center", min_width=14)
    score_table.add_column(justify="left", min_width=6)

    score_table.add_row(
        Text(home, style="bold blue"),
        Text(f"{_period_label(state.period)}  {state.clock}", style="bold white"),
        Text(away, style="bold red"),
    )
    score_table.add_row(
        Text(str(state.home_score), style="bold blue", justify="right"),
        Text("vs", style="dim", justify="center"),
        Text(str(state.away_score), style="bold red"),
    )

    # --- Win probability ---
    home_prob = _compute_win_prob(state)
    wp_bar = _win_prob_bar(home_prob)
    wp_label = Text(
        f"{home} {home_prob*100:.0f}%  |  {away} {(1-home_prob)*100:.0f}%",
        style="dim",
    )

    # --- Momentum ---
    momentum_bar = _momentum_bar(list(state.last_10_possessions), home, away)
    home_recent = list(state.last_10_possessions).count("home_score")
    away_recent = list(state.last_10_possessions).count("away_score")
    if home_recent > away_recent + 2:
        momentum_label = Text(f"{home} on a run ({home_recent}-{away_recent} last 10)", style="blue")
    elif away_recent > home_recent + 2:
        momentum_label = Text(f"{away} on a run ({away_recent}-{home_recent} last 10)", style="red")
    else:
        momentum_label = Text(f"Contested ({home_recent}-{away_recent} last 10)", style="dim")

    # --- Pace ---
    pace_diff = state.pace - 100.0
    pace_str = f"{state.pace:.1f} poss/48min  ({'+' if pace_diff >= 0 else ''}{pace_diff:.1f} vs avg)"

    # --- Foul trouble ---
    foul_text = _foul_trouble_text(state)

    # --- Stats table ---
    stats = Table.grid(padding=(0, 2))
    stats.add_column(style="dim", min_width=16)
    stats.add_column()

    stats.add_row("Win Probability", wp_bar)
    stats.add_row("", wp_label)
    stats.add_row("Momentum (last 10)", momentum_bar)
    stats.add_row("", momentum_label)
    stats.add_row("Pace", Text(pace_str))
    stats.add_row(
        "Possessions",
        Text(f"{home} {state.home_possessions}  |  {away} {state.away_possessions}"),
    )
    stats.add_row("Foul Trouble", foul_text)

    if state.home_players_on_court or state.away_players_on_court:
        home_court = ", ".join(state.home_players_on_court) or "—"
        away_court = ", ".join(state.away_players_on_court) or "—"
        stats.add_row(f"{home} on court", Text(home_court, style="blue"))
        stats.add_row(f"{away} on court", Text(away_court, style="red"))

    status_style = "dim" if state.game_status == "in_progress" else "bold yellow"
    status_label = "FINAL" if state.game_status == "final" else "LIVE"

    from rich.rule import Rule
    from rich import box

    layout = Table.grid()
    layout.add_column()
    layout.add_row(score_table)
    layout.add_row(Text(""))
    layout.add_row(stats)

    return Panel(
        layout,
        title=f"[bold]NBA Live[/bold] — {game_id}",
        subtitle=f"[{status_style}]{status_label}[/{status_style}]",
        border_style="blue" if state.game_status == "in_progress" else "yellow",
        padding=(1, 2),
    )


async def _run_tui(game_id: str) -> None:
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    console = Console()
    state_key = STATE_KEY_TEMPLATE.format(game_id=game_id)

    console.print(f"[dim]Connecting to Redis at {REDIS_URL}...[/dim]")

    try:
        await redis_client.ping()
    except Exception as exc:
        console.print(f"[red]Cannot connect to Redis: {exc}[/red]")
        console.print("[dim]Start Redis first: docker-compose up redis[/dim]")
        return

    console.print(f"[green]Connected.[/green] Watching game [bold]{game_id}[/bold]. Press Ctrl-C to quit.\n")

    async def _read_state() -> GameState | None:
        raw = await redis_client.hget(state_key, "data")
        if raw is None:
            return None
        return GameState.model_validate_json(raw)

    with Live(
        _build_display(None, game_id),
        console=console,
        refresh_per_second=2,
        screen=True,
    ) as live:
        try:
            while True:
                state = await _read_state()
                live.update(_build_display(state, game_id))

                if state and state.game_status == "final":
                    await asyncio.sleep(3)
                    break

                await asyncio.sleep(REFRESH_INTERVAL)
        except asyncio.CancelledError:
            pass

    await redis_client.aclose()


@click.command()
@click.option("--game", required=True, help="NBA game ID to watch (e.g. 0042300401)")
def main(game: str) -> None:
    """
    Live terminal scoreboard for an NBA game.

    Run the processor first, then start a replay in another terminal:

    \b
        # Terminal 1: start processor
        python -m src.processor --game 0042300401

        # Terminal 2: start replay at 20x speed
        python -m src.replay --game 0042300401 --speed 20

        # Terminal 3: watch the TUI
        python -m src.tui --game 0042300401
    """
    try:
        asyncio.run(_run_tui(game))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
