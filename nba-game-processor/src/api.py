"""
FastAPI REST API: serves current game state from the Redis Hash.

All reads except /events are O(1) lookups against the materialized Redis
Hash maintained by the processor. /events reads from the stream directly and
is the one O(n) endpoint, intended for debugging.
"""

import logging
import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from src.metrics import (
    LEAGUE_AVERAGE_OFFENSIVE_RATING,
    LEAGUE_AVERAGE_PACE,
    compute_efficiency,
)
from src.state import GameState
from src.win_probability import predict_win_probability

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
    is_final: bool


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

    return WinProbabilityResponse(
        game_id=game_id,
        home_team=state.home_team,
        away_team=state.away_team,
        home_win_probability=round(home_prob, 4),
        away_win_probability=round(1.0 - home_prob, 4),
        is_final=state.game_status == "final",
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


@app.get("/health")
async def health() -> dict:
    """Health check used by Fly.io and Docker."""
    return {"status": "ok"}
