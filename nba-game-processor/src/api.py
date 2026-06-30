"""
FastAPI REST API: serves current game state from the Redis Hash.

All reads except /events are O(1) lookups against the materialized Redis
Hash maintained by the processor. /events reads from the stream directly and
is the one O(n) endpoint, intended for debugging.
"""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from src.dashboard import DASHBOARD_HTML
from src.metrics import (
    LEAGUE_AVERAGE_OFFENSIVE_RATING,
    LEAGUE_AVERAGE_PACE,
    compute_efficiency,
)
from src.state import GameState
from src.win_probability import kelly_fraction, predict_win_probability

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
STATE_KEY_TEMPLATE = "game_state:{game_id}"
STREAM_KEY_TEMPLATE = "game_events:{game_id}"

redis_client: aioredis.Redis | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Verify the Redis connection on startup; clean up on shutdown."""
    global redis_client
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        await redis_client.ping()
        logger.info("Redis connection verified at %s", REDIS_URL)
    except Exception as exc:
        # Don't crash the container on a transient Redis outage — let
        # endpoints 503 instead so the orchestrator doesn't restart-loop.
        logger.warning("Redis unavailable on startup: %s — endpoints will 503", exc)
    yield
    await redis_client.aclose()


app = FastAPI(
    title="NBA Game Processor API",
    description="Real-time NBA game state served from a Redis-backed event pipeline.",
    version="1.0.0",
    lifespan=lifespan,
)


async def _get_state_from_redis(game_id: str) -> GameState:
    """Read and deserialize GameState from the Redis Hash materialized view."""
    if redis_client is None:
        raise HTTPException(status_code=503, detail="Redis not available")

    state_key = STATE_KEY_TEMPLATE.format(game_id=game_id)
    raw = await redis_client.hget(state_key, "data")

    if raw is None:
        raise HTTPException(
            status_code=404,
            detail="Game not found or not yet started",
        )

    return GameState.model_validate_json(raw)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class MomentumResponse(BaseModel):
    game_id: str
    last_10_possessions: list[str]
    momentum_score: int
    z_score: float
    interpretation: str


class PaceResponse(BaseModel):
    game_id: str
    pace: float
    league_average: float
    pace_differential: float
    interpretation: str


class EfficiencyResponse(BaseModel):
    game_id: str
    home_team: str
    away_team: str
    home_offensive_rating: float
    away_offensive_rating: float
    home_ortg_vs_average: float
    away_ortg_vs_average: float
    interpretation: str


class WinProbabilityResponse(BaseModel):
    game_id: str
    home_team: str
    away_team: str
    home_win_probability: float
    away_win_probability: float
    kelly_fraction: float
    kelly_interpretation: str
    is_final: bool


class LatencyResponse(BaseModel):
    game_id: str
    samples: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    min_ms: float
    max_ms: float


class FoulTroubleResponse(BaseModel):
    game_id: str
    home_team: str
    away_team: str
    home_foul_trouble: dict[str, int]
    away_foul_trouble: dict[str, int]
    all_player_fouls: dict[str, int]
    period: int


class StreamEvent(BaseModel):
    """One entry from the Redis Stream — the raw event as pushed by the poller."""
    stream_id: str
    fields: dict


class EventsResponse(BaseModel):
    game_id: str
    count: int
    events: list[StreamEvent]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/dashboard/{game_id}", response_class=HTMLResponse, include_in_schema=False)
async def get_dashboard(game_id: str) -> HTMLResponse:
    """Live visual dashboard for a game."""
    return HTMLResponse(DASHBOARD_HTML.format(game_id=game_id))


@app.get("/game/{game_id}/state", response_model=GameState)
async def get_game_state(game_id: str) -> GameState:
    """Return the full current GameState."""
    return await _get_state_from_redis(game_id)


@app.get("/game/{game_id}/momentum", response_model=MomentumResponse)
async def get_momentum(game_id: str) -> MomentumResponse:
    """
    Momentum over the last 10 possessions.

    z_score treats each scoring possession as a coin flip under the null
    hypothesis that neither team is "on a run" (p=0.5). For n scoring
    possessions, home_scoring ~ Binomial(n, 0.5) with std sqrt(n)/2, so
    z = (home_scoring - n/2) / (sqrt(n)/2). |z| > 1.5 flags a real run.
    """
    state = await _get_state_from_redis(game_id)

    home_scoring = state.last_10_possessions.count("home_score")
    away_scoring = state.last_10_possessions.count("away_score")
    momentum_score = home_scoring - away_scoring

    n = home_scoring + away_scoring
    if n > 0:
        z_score = (home_scoring - n / 2) / (n ** 0.5 / 2)
    else:
        z_score = 0.0

    if z_score > 1.5:
        interpretation = f"{state.home_team} on a run"
    elif z_score < -1.5:
        interpretation = f"{state.away_team} on a run"
    else:
        interpretation = "Contested"

    return MomentumResponse(
        game_id=game_id,
        last_10_possessions=list(state.last_10_possessions),
        momentum_score=momentum_score,
        z_score=round(z_score, 2),
        interpretation=interpretation,
    )


@app.get("/game/{game_id}/pace", response_model=PaceResponse)
async def get_pace(game_id: str) -> PaceResponse:
    """Current pace vs. league average (Oliver, "Basketball on Paper", 2004)."""
    state = await _get_state_from_redis(game_id)
    pace_differential = round(state.pace - LEAGUE_AVERAGE_PACE, 2)

    if pace_differential > 5:
        interpretation = "High-pace game — expect more total possessions"
    elif pace_differential < -5:
        interpretation = "Low-pace game — defensive battle, fewer possessions"
    else:
        interpretation = "Average pace"

    return PaceResponse(
        game_id=game_id,
        pace=round(state.pace, 2),
        league_average=LEAGUE_AVERAGE_PACE,
        pace_differential=pace_differential,
        interpretation=interpretation,
    )


@app.get("/game/{game_id}/efficiency", response_model=EfficiencyResponse)
async def get_efficiency(game_id: str) -> EfficiencyResponse:
    """Offensive rating (points per 100 possessions) for both teams vs. league average."""
    state = await _get_state_from_redis(game_id)
    snap = compute_efficiency(state)

    if snap.home_offensive_rating > snap.away_offensive_rating + 10:
        interpretation = f"{state.home_team} offense dominant"
    elif snap.away_offensive_rating > snap.home_offensive_rating + 10:
        interpretation = f"{state.away_team} offense dominant"
    else:
        interpretation = "Offenses evenly matched"

    return EfficiencyResponse(
        game_id=game_id,
        home_team=state.home_team,
        away_team=state.away_team,
        home_offensive_rating=snap.home_offensive_rating,
        away_offensive_rating=snap.away_offensive_rating,
        home_ortg_vs_average=snap.home_ortg_vs_average,
        away_ortg_vs_average=snap.away_ortg_vs_average,
        interpretation=interpretation,
    )


@app.get("/game/{game_id}/win-probability", response_model=WinProbabilityResponse)
async def get_win_probability(game_id: str) -> WinProbabilityResponse:
    """P(home team wins), from a logistic regression over score diff and time remaining."""
    state = await _get_state_from_redis(game_id)
    home_prob = predict_win_probability(state)
    kf = kelly_fraction(home_prob)

    if kf > 0.05:
        kelly_interp = f"Bet home ({state.home_team}): {kf*100:.1f}% of bankroll"
    elif kf < -0.05:
        kelly_interp = f"Bet away ({state.away_team}): {abs(kf)*100:.1f}% of bankroll"
    else:
        kelly_interp = "No edge — near even odds"

    return WinProbabilityResponse(
        game_id=game_id,
        home_team=state.home_team,
        away_team=state.away_team,
        home_win_probability=round(home_prob, 4),
        away_win_probability=round(1.0 - home_prob, 4),
        kelly_fraction=kf,
        kelly_interpretation=kelly_interp,
        is_final=state.game_status == "final",
    )


@app.get("/game/{game_id}/foul-trouble", response_model=FoulTroubleResponse)
async def get_foul_trouble(game_id: str) -> FoulTroubleResponse:
    """
    Players currently in foul trouble: 4+ fouls before Q4, or 5+ in Q4.

    Foul trouble is a hidden game-state variable the score alone misses —
    a team's star picking up a 4th foul in Q2 changes their win probability
    more than most 3-point swings.
    """
    state = await _get_state_from_redis(game_id)
    trouble = state.foul_trouble_players()

    home_trouble = {p: f for p, f in trouble.items() if p in state.home_players_on_court}
    away_trouble = {p: f for p, f in trouble.items() if p in state.away_players_on_court}

    return FoulTroubleResponse(
        game_id=game_id,
        home_team=state.home_team,
        away_team=state.away_team,
        home_foul_trouble=home_trouble,
        away_foul_trouble=away_trouble,
        all_player_fouls=state.player_fouls,
        period=state.period,
    )


@app.get("/game/{game_id}/events", response_model=EventsResponse)
async def get_events(
    game_id: str,
    limit: int = Query(default=50, ge=1, le=500),
) -> EventsResponse:
    """
    Most recent raw events from the Redis Stream — the append-only event log.

    This is the one endpoint that reads the stream instead of the
    materialized view, for debugging and inspection. `limit` bounds the cost.
    """
    if redis_client is None:
        raise HTTPException(status_code=503, detail="Redis not available")

    stream_key = STREAM_KEY_TEMPLATE.format(game_id=game_id)
    raw_entries = await redis_client.xrevrange(stream_key, count=limit)

    if not raw_entries:
        raise HTTPException(status_code=404, detail="Game not found or not yet started")

    events = [
        StreamEvent(stream_id=msg_id, fields=fields)
        for msg_id, fields in raw_entries
    ]

    return EventsResponse(game_id=game_id, count=len(events), events=events)


@app.get("/game/{game_id}/latency", response_model=LatencyResponse)
async def get_latency(game_id: str) -> LatencyResponse:
    """
    Pipeline latency percentiles (p50/p95/p99) for this game.

    Measures wall-clock time from when the processor begins applying an event
    to when the Redis snapshot write completes. This is the end-to-end delay
    between an event entering the stream and the materialized view being
    queryable — analogous to market-data-to-signal latency in a trading system.
    """
    if redis_client is None:
        raise HTTPException(status_code=503, detail="Redis not available")

    latency_key = f"game_latency:{game_id}"
    raw = await redis_client.zrange(latency_key, 0, -1, withscores=False)

    if not raw:
        raise HTTPException(status_code=404, detail="No latency data yet for this game")

    samples = sorted(float(entry.split(":", 1)[1]) for entry in raw)
    n = len(samples)

    def percentile(p: float) -> float:
        idx = max(0, int(p / 100 * n) - 1)
        return round(samples[idx], 3)

    return LatencyResponse(
        game_id=game_id,
        samples=n,
        p50_ms=percentile(50),
        p95_ms=percentile(95),
        p99_ms=percentile(99),
        min_ms=round(samples[0], 3),
        max_ms=round(samples[-1], 3),
    )


@app.get("/game/{game_id}/stream", include_in_schema=False)
async def stream_game(game_id: str) -> StreamingResponse:
    """
    Server-Sent Events endpoint: pushes a JSON game snapshot every second.

    Clients connect once and receive a push on every state change instead of
    polling. The dashboard uses this to eliminate the 2-second polling interval
    and reduce unnecessary requests by ~10x under low-activity conditions.
    """
    async def event_generator():
        last_seen = None
        while True:
            try:
                state_key = STATE_KEY_TEMPLATE.format(game_id=game_id)
                raw = await redis_client.hget(state_key, "data") if redis_client else None
                if raw and raw != last_seen:
                    last_seen = raw
                    yield f"data: {raw}\n\n"
                elif raw is None:
                    yield f"data: {json.dumps({'error': 'game not found'})}\n\n"
            except Exception:
                pass
            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/health")
async def health() -> dict:
    """Health check used by Fly.io and Docker."""
    return {"status": "ok"}
