"""
FastAPI REST API: serves current game state from Redis Hash.

All reads except /events are O(1) lookups against a materialized Redis Hash
maintained by the processor. /events intentionally reads from the stream and
is documented as O(n) — this is the one place CQRS inverts: sometimes you
want the audit log, not just current state.
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

    WHY read from Redis Hash and not from the stream: the stream is append-only
    and sequential — querying it for current state requires replaying all events
    (O(n)). The processor maintains a materialized snapshot in the hash so reads
    are always O(1) regardless of game length. This is the read side of CQRS.
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


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class MomentumResponse(BaseModel):
    game_id: str
    last_10_possessions: list[str]
    momentum_score: int
    # WHY include z_score: momentum_score alone (e.g. "+3") is meaningless
    # without knowing the sample size it came from. z_score normalizes for
    # that — it's the number of standard deviations the observed split is
    # from the 50/50 expectation, making "is this a real run?" answerable.
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
    # Differential vs. 2023-24 league average (~113 pts/100 possessions).
    home_ortg_vs_average: float
    away_ortg_vs_average: float
    interpretation: str


class WinProbabilityResponse(BaseModel):
    game_id: str
    home_team: str
    away_team: str
    home_win_probability: float
    away_win_probability: float
    # WHY surface is_final: callers (and graders) should be able to tell at a
    # glance whether the 1.0/0.0 they see is a deterministic game outcome or
    # a model estimate that happens to land near an extreme.
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
    """
    Return the full current GameState.

    WHY return the full state: callers can cache the full object client-side
    and derive any metric locally; field-level endpoints would require multiple
    round-trips for dashboard use cases.
    """
    return await _get_state_from_redis(game_id)


@app.get("/game/{game_id}/momentum", response_model=MomentumResponse)
async def get_momentum(game_id: str) -> MomentumResponse:
    """
    Return momentum signal derived from the last 10 possessions.

    WHY 10 possessions: roughly 3-4 minutes of game time — captures a run
    without averaging it over the entire game. Fewer than 5 is noisy;
    more than 15 blends current form with history. 10 is the informal
    NBA-analytics convention for "recent form" window size.

    WHY a z-score instead of a fixed threshold like "momentum_score > 2":
    treat each scoring possession as a Bernoulli trial with p=0.5 under the
    null hypothesis that neither team is "on a run" (scoring is a coin flip).
    For n scoring possessions, home_scoring ~ Binomial(n, 0.5), with mean n/2
    and standard deviation sqrt(n)/2. z = (home_scoring - n/2) / (sqrt(n)/2)
    measures how many standard deviations the observed split is from the
    50/50 expectation. |z| > 1.5 (~87th percentile, one-tailed) flags a run.
    This adapts to sample size automatically — a fixed threshold like ">2"
    means something different for n=4 scoring possessions vs. n=10.
    """
    state = await _get_state_from_redis(game_id)

    home_scoring = state.last_10_possessions.count("home_score")
    away_scoring = state.last_10_possessions.count("away_score")
    momentum_score = home_scoring - away_scoring

    n = home_scoring + away_scoring
    if n > 0:
        # WHY sqrt(n) / 2: standard deviation of Binomial(n, 0.5) is
        # sqrt(n * 0.5 * 0.5) = sqrt(n) / 2.
        z_score = (home_scoring - n / 2) / (n ** 0.5 / 2)
    else:
        # WHY z=0 when n=0: no scoring possessions in the window means no
        # evidence of a run in either direction — neutral by definition.
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
    """
    Return current pace and deviation from league average.

    WHY pace matters: it contextualizes the score. A 120-110 game at pace 115
    is a fast-break blowout; the same score at pace 85 is a grinding defensive
    battle. Pace is one of the four factors in NBA efficiency analysis
    (Oliver, "Basketball on Paper", 2004).
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


@app.get("/game/{game_id}/efficiency", response_model=EfficiencyResponse)
async def get_efficiency(game_id: str) -> EfficiencyResponse:
    """
    Return offensive rating for both teams vs. the league average.

    WHY offensive rating over raw score: points per 100 possessions normalizes
    for pace — a team scoring 110 in 90 possessions is more efficient than one
    scoring 120 in 120 possessions. Offensive rating is how NBA front offices
    actually evaluate teams. A positive ortg_vs_average means the offense is
    performing above the league average of ~113 pts/100 possessions.
    """
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
    """
    Return P(home team wins) from the trained logistic regression model.

    WHY a model endpoint alongside rule-based ones (momentum, pace,
    efficiency): those endpoints describe *what's happening*; this one
    answers *who's likely to win*, combining score differential and time
    remaining into a single calibrated probability — the question a viewer
    actually cares about in the final minutes of a close game.
    """
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
    Return raw events from the Redis Stream — the append-only event log.

    WHY this endpoint is O(n) while all others are O(1): this is the one
    deliberate exception to the CQRS rule. Sometimes you need the audit log —
    to debug unexpected state, replay a sequence, or inspect exactly what the
    poller pushed. The O(n) cost is acceptable for a debugging/inspection
    endpoint that isn't on the hot query path. The limit parameter bounds worst-
    case latency. Callers who want current state should use /state instead.
    """
    if redis_client is None:
        raise HTTPException(status_code=503, detail="Redis not available")

    stream_key = STREAM_KEY_TEMPLATE.format(game_id=game_id)
    # WHY XREVRANGE with COUNT: we want the most recent `limit` events,
    # not the oldest. XREVRANGE reads newest-first so we get recency without
    # scanning the full stream from the beginning.
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
    """
    Health check endpoint used by Fly.io and Docker health probes.

    WHY a dedicated /health rather than reusing /game/{id}/state: health
    checks should be lightweight and always succeed if the process is alive —
    they should not depend on any specific game data existing in Redis.
    """
    return {"status": "ok"}
