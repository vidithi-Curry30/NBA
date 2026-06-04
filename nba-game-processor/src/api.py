"""
FastAPI REST API: serves current game state from Redis Hash.

All reads are O(1) lookups against a materialized Redis Hash maintained by
the processor. The API never touches the Redis Stream — that's the CQRS
pattern: stream is the write path, hash is the read path.
"""

import json
import logging
import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.state import GameState

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
STATE_KEY_TEMPLATE = "game_state:{game_id}"

# WHY league average 100.0: NBA league average pace has been ~98-102 possessions
# per 48 minutes over the past five seasons. 100 is a clean round number that
# serves as an intuitive baseline for pace_differential interpretation.
LEAGUE_AVERAGE_PACE = 100.0

redis_client: aioredis.Redis | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Verify Redis connection on startup; clean up on shutdown.

    WHY fail-fast on startup rather than on first request: a missing Redis
    connection will cause every endpoint to 500. Surfacing it at startup makes
    the failure obvious in container logs and health checks immediately, rather
    than confusing the first caller with a cryptic 500.
    """
    global redis_client
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        await redis_client.ping()
        logger.info("Redis connection verified at %s", REDIS_URL)
    except Exception as exc:
        # WHY warn rather than hard crash: allows the API to start in
        # degraded mode during Redis restarts without killing the entire
        # container and triggering an orchestrator restart loop.
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
    """
    Read and deserialize GameState from the Redis Hash materialized view.

    WHY read from Redis Hash and not from the stream: the stream is
    append-only and sequential — querying it for current state requires
    replaying all events (O(n)). The processor maintains a materialized
    snapshot in the hash so reads are always O(1) regardless of game length.
    This is the read side of the CQRS pattern.
    """
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


class MomentumResponse(BaseModel):
    """Response schema for the /momentum endpoint."""
    game_id: str
    last_10_possessions: list[str]
    # WHY momentum_score = home_scoring_count - away_scoring_count:
    # positive means home team on a run, negative means away. Near zero
    # means contested. 10 possessions is long enough to be meaningful
    # (not noise) but short enough to reflect the current game state
    # rather than first-half history.
    momentum_score: int
    interpretation: str


class PaceResponse(BaseModel):
    """Response schema for the /pace endpoint."""
    game_id: str
    pace: float
    league_average: float
    # WHY pace_differential: raw pace is hard to interpret without a baseline.
    # Differential against 100 tells you immediately whether this game is
    # faster or slower than average — the same intuition as +/- in trading.
    pace_differential: float
    interpretation: str


@app.get("/game/{game_id}/state", response_model=GameState)
async def get_game_state(game_id: str) -> GameState:
    """
    Return the full current GameState for a game.

    WHY return the full state rather than individual fields: callers can
    cache the full object client-side and derive any metric locally;
    field-level endpoints would require multiple round-trips for dashboards.
    """
    return await _get_state_from_redis(game_id)


@app.get("/game/{game_id}/momentum", response_model=MomentumResponse)
async def get_momentum(game_id: str) -> MomentumResponse:
    """
    Return momentum signal derived from the last 10 possessions.

    WHY 10 possessions: roughly 3-4 minutes of game time — captures a
    meaningful run without averaging out the current stretch over the
    entire game. In basketball analytics this window size is a common
    convention for "recent form" metrics.
    """
    state = await _get_state_from_redis(game_id)

    home_scoring = state.last_10_possessions.count("home_score")
    away_scoring = state.last_10_possessions.count("away_score")
    momentum_score = home_scoring - away_scoring

    if momentum_score > 2:
        interpretation = f"{state.home_team} on a run"
    elif momentum_score < -2:
        interpretation = f"{state.away_team} on a run"
    else:
        interpretation = "Contested"

    return MomentumResponse(
        game_id=game_id,
        last_10_possessions=state.last_10_possessions,
        momentum_score=momentum_score,
        interpretation=interpretation,
    )


@app.get("/game/{game_id}/pace", response_model=PaceResponse)
async def get_pace(game_id: str) -> PaceResponse:
    """
    Return current pace and deviation from league average.

    WHY pace matters: it contextualizes the score. A 120-110 game at pace 115
    is a fast-break blowout; the same score at pace 85 is a grinding defensive
    battle. Pace is one of the four factors in NBA efficiency analysis.
    """
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


@app.get("/health")
async def health() -> dict:
    """
    Health check endpoint used by Fly.io and Docker health probes.

    WHY a dedicated /health rather than reusing /game/{id}/state: health
    checks should be lightweight and always succeed if the process is alive —
    they should not depend on any specific game data existing in Redis.
    """
    return {"status": "ok"}
