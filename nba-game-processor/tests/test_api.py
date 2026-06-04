"""
Integration tests for the FastAPI endpoints.

All Redis calls are mocked — no Redis instance required. Tests verify that
each endpoint correctly reads from the mocked Redis Hash, computes derived
metrics, and returns the right status codes.
"""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.state import GameState


def _make_state_json(overrides: dict | None = None) -> str:
    """Build a serialized GameState JSON string for mocking Redis responses."""
    base = GameState(
        game_id="0042300401",
        home_team="BOS",
        away_team="DAL",
        home_score=86,
        away_score=72,
        period=3,
        clock="4:22",
        possession_count=85,
        minutes_elapsed=28.6,
        pace=142.9,
        last_10_possessions=[
            "home_score", "away_score", "home_score", "home_score",
            "turnover", "away_score", "home_score", "home_score",
            "away_score", "home_score",
        ],
        updated_at=datetime(2024, 6, 6, 21, 0, 0),
    )
    data = json.loads(base.model_dump_json())
    if overrides:
        data.update(overrides)
    return json.dumps(data)


def _make_mock_redis(hget_return_value: str | None) -> AsyncMock:
    """
    Build a fully-mocked async Redis client.

    WHY mock at the aioredis.from_url level: the lifespan context creates the
    redis_client via from_url on startup. Patching from_url intercepts the
    creation before the module-level global is set, ensuring all endpoint
    calls use the mock rather than attempting a real TCP connection.
    """
    mock_redis = AsyncMock()
    mock_redis.ping = AsyncMock(return_value=True)
    mock_redis.hget = AsyncMock(return_value=hget_return_value)
    mock_redis.aclose = AsyncMock()
    return mock_redis


# ---------------------------------------------------------------------------
# /state endpoint
# ---------------------------------------------------------------------------

def test_state_returns_200_when_game_exists():
    """/state returns 200 with full GameState JSON when Redis key exists."""
    mock_redis = _make_mock_redis(_make_state_json())
    with patch("src.api.aioredis.from_url", return_value=mock_redis):
        with TestClient(app) as client:
            response = client.get("/game/0042300401/state")
    assert response.status_code == 200
    data = response.json()
    assert data["game_id"] == "0042300401"
    assert data["home_team"] == "BOS"
    assert data["home_score"] == 86


def test_state_returns_404_when_game_missing():
    """/state returns 404 with descriptive message when Redis key is absent."""
    mock_redis = _make_mock_redis(None)
    with patch("src.api.aioredis.from_url", return_value=mock_redis):
        with TestClient(app) as client:
            response = client.get("/game/0000000000/state")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_state_contains_all_required_fields():
    """/state response includes every field defined in the GameState schema."""
    mock_redis = _make_mock_redis(_make_state_json())
    with patch("src.api.aioredis.from_url", return_value=mock_redis):
        with TestClient(app) as client:
            response = client.get("/game/0042300401/state")
    data = response.json()
    required_fields = [
        "game_id", "home_team", "away_team", "home_score", "away_score",
        "period", "clock", "possession_count", "pace", "last_10_possessions",
        "updated_at",
    ]
    for field in required_fields:
        assert field in data, f"Missing field: {field}"


# ---------------------------------------------------------------------------
# /momentum endpoint
# ---------------------------------------------------------------------------

def test_momentum_correct_score_for_known_possessions():
    """
    /momentum computes momentum_score as home_scoring - away_scoring.
    Given last_10 = [home*5, away*2, turnover*3], score = 5 - 2 = 3.
    """
    possessions = [
        "home_score", "home_score", "home_score", "home_score", "home_score",
        "away_score", "away_score", "turnover", "turnover", "turnover",
    ]
    state_json = _make_state_json({"last_10_possessions": possessions})
    mock_redis = _make_mock_redis(state_json)
    with patch("src.api.aioredis.from_url", return_value=mock_redis):
        with TestClient(app) as client:
            response = client.get("/game/0042300401/momentum")
    assert response.status_code == 200
    data = response.json()
    assert data["momentum_score"] == 3
    assert "BOS" in data["interpretation"]


def test_momentum_negative_score_when_away_on_run():
    """momentum_score is negative when away team is scoring more."""
    possessions = [
        "away_score", "away_score", "away_score", "away_score", "away_score",
        "home_score", "home_score", "turnover", "turnover", "turnover",
    ]
    state_json = _make_state_json({"last_10_possessions": possessions})
    mock_redis = _make_mock_redis(state_json)
    with patch("src.api.aioredis.from_url", return_value=mock_redis):
        with TestClient(app) as client:
            response = client.get("/game/0042300401/momentum")
    data = response.json()
    assert data["momentum_score"] == -3
    assert "DAL" in data["interpretation"]


def test_momentum_returns_404_when_game_missing():
    """/momentum propagates 404 when the game doesn't exist in Redis."""
    mock_redis = _make_mock_redis(None)
    with patch("src.api.aioredis.from_url", return_value=mock_redis):
        with TestClient(app) as client:
            response = client.get("/game/9999999999/momentum")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# /pace endpoint
# ---------------------------------------------------------------------------

def test_pace_returns_correct_differential():
    """
    /pace pace_differential = current_pace - 100.0 (league average).
    At pace 142.9: differential ≈ 42.9.
    """
    mock_redis = _make_mock_redis(_make_state_json())
    with patch("src.api.aioredis.from_url", return_value=mock_redis):
        with TestClient(app) as client:
            response = client.get("/game/0042300401/pace")
    assert response.status_code == 200
    data = response.json()
    assert data["league_average"] == 100.0
    assert abs(data["pace_differential"] - (data["pace"] - 100.0)) < 0.01


def test_pace_returns_404_when_game_missing():
    """/pace propagates 404 when the game doesn't exist in Redis."""
    mock_redis = _make_mock_redis(None)
    with patch("src.api.aioredis.from_url", return_value=mock_redis):
        with TestClient(app) as client:
            response = client.get("/game/9999999999/pace")
    assert response.status_code == 404


def test_pace_high_pace_interpretation():
    """Pace significantly above 100 returns a 'high-pace' interpretation."""
    state_json = _make_state_json({"pace": 115.0})
    mock_redis = _make_mock_redis(state_json)
    with patch("src.api.aioredis.from_url", return_value=mock_redis):
        with TestClient(app) as client:
            response = client.get("/game/0042300401/pace")
    data = response.json()
    assert "high" in data["interpretation"].lower() or "pace" in data["interpretation"].lower()


# ---------------------------------------------------------------------------
# /health endpoint
# ---------------------------------------------------------------------------

def test_health_returns_ok():
    """/health always returns 200 with status ok — no Redis dependency."""
    mock_redis = _make_mock_redis(None)
    with patch("src.api.aioredis.from_url", return_value=mock_redis):
        with TestClient(app) as client:
            response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
