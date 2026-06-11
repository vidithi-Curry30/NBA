"""
Integration tests for the FastAPI endpoints.

All Redis calls are mocked — no Redis instance required. Tests verify that
each endpoint correctly reads from the mocked Redis, computes derived metrics,
and returns the right status codes and shapes.
"""

import json
from datetime import datetime
from unittest.mock import AsyncMock, patch

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
        home_possessions=44,
        away_possessions=41,
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


def _make_mock_redis(
    hget_return_value: str | None,
    xrevrange_return_value: list | None = None,
) -> AsyncMock:
    """
    Build a fully-mocked async Redis client.

    WHY mock at the aioredis.from_url level: the lifespan context creates the
    redis_client via from_url on startup. Patching from_url intercepts creation
    before the module-level global is set, ensuring all endpoint calls use the
    mock rather than attempting a real TCP connection.
    """
    mock_redis = AsyncMock()
    mock_redis.ping = AsyncMock(return_value=True)
    mock_redis.hget = AsyncMock(return_value=hget_return_value)
    mock_redis.xrevrange = AsyncMock(return_value=xrevrange_return_value or [])
    mock_redis.aclose = AsyncMock()
    return mock_redis


# ---------------------------------------------------------------------------
# /state
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
    """/state response includes every field defined in GameState."""
    mock_redis = _make_mock_redis(_make_state_json())
    with patch("src.api.aioredis.from_url", return_value=mock_redis):
        with TestClient(app) as client:
            response = client.get("/game/0042300401/state")
    data = response.json()
    required = [
        "game_id", "home_team", "away_team", "home_score", "away_score",
        "period", "clock", "home_possessions", "away_possessions",
        "pace", "last_10_possessions", "updated_at",
    ]
    for field in required:
        assert field in data, f"Missing field: {field}"


# ---------------------------------------------------------------------------
# /momentum
# ---------------------------------------------------------------------------

def test_momentum_correct_score_for_known_possessions():
    """
    momentum_score = home_scoring - away_scoring over last 10 possessions.
    8 home vs 1 away (n=9): z = (8 - 4.5) / (sqrt(9)/2) = 3.5/1.5 = 2.33 > 1.5
    -> flagged as a home run.
    """
    possessions = [
        "home_score", "home_score", "home_score", "home_score", "home_score",
        "home_score", "home_score", "home_score", "away_score", "turnover",
    ]
    mock_redis = _make_mock_redis(_make_state_json({"last_10_possessions": possessions}))
    with patch("src.api.aioredis.from_url", return_value=mock_redis):
        with TestClient(app) as client:
            response = client.get("/game/0042300401/momentum")
    assert response.status_code == 200
    data = response.json()
    assert data["momentum_score"] == 7
    assert data["z_score"] > 1.5
    assert "BOS" in data["interpretation"]


def test_momentum_negative_score_when_away_on_run():
    """
    momentum_score is negative when away team is on a scoring run.
    8 away vs 1 home (n=9): z = (1 - 4.5) / (sqrt(9)/2) = -3.5/1.5 = -2.33 < -1.5
    -> flagged as an away run.
    """
    possessions = [
        "away_score", "away_score", "away_score", "away_score", "away_score",
        "away_score", "away_score", "away_score", "home_score", "turnover",
    ]
    mock_redis = _make_mock_redis(_make_state_json({"last_10_possessions": possessions}))
    with patch("src.api.aioredis.from_url", return_value=mock_redis):
        with TestClient(app) as client:
            response = client.get("/game/0042300401/momentum")
    data = response.json()
    assert data["momentum_score"] == -7
    assert data["z_score"] < -1.5
    assert "DAL" in data["interpretation"]


def test_momentum_returns_404_when_game_missing():
    """/momentum propagates 404 when the game doesn't exist in Redis."""
    mock_redis = _make_mock_redis(None)
    with patch("src.api.aioredis.from_url", return_value=mock_redis):
        with TestClient(app) as client:
            response = client.get("/game/9999999999/momentum")
    assert response.status_code == 404


def test_momentum_contested_within_z_threshold():
    """
    A 5-4 split (n=9): z = (5 - 4.5) / (sqrt(9)/2) = 0.5/1.5 = 0.33,
    well within +-1.5 -> "Contested".
    """
    possessions = [
        "home_score", "away_score", "home_score", "away_score", "home_score",
        "away_score", "home_score", "away_score", "home_score", "turnover",
    ]
    mock_redis = _make_mock_redis(_make_state_json({"last_10_possessions": possessions}))
    with patch("src.api.aioredis.from_url", return_value=mock_redis):
        with TestClient(app) as client:
            response = client.get("/game/0042300401/momentum")
    data = response.json()
    assert abs(data["z_score"]) < 1.5
    assert data["interpretation"] == "Contested"


def test_momentum_zero_scoring_possessions_is_neutral():
    """If the last 10 possessions are all turnovers, z_score is 0 (no evidence)."""
    possessions = ["turnover"] * 10
    mock_redis = _make_mock_redis(_make_state_json({"last_10_possessions": possessions}))
    with patch("src.api.aioredis.from_url", return_value=mock_redis):
        with TestClient(app) as client:
            response = client.get("/game/0042300401/momentum")
    data = response.json()
    assert data["z_score"] == 0.0
    assert data["interpretation"] == "Contested"


# ---------------------------------------------------------------------------
# /pace
# ---------------------------------------------------------------------------

def test_pace_returns_correct_differential():
    """pace_differential = current_pace - league_average (100.0)."""
    mock_redis = _make_mock_redis(_make_state_json())
    with patch("src.api.aioredis.from_url", return_value=mock_redis):
        with TestClient(app) as client:
            response = client.get("/game/0042300401/pace")
    assert response.status_code == 200
    data = response.json()
    assert data["league_average"] == 100.0
    assert abs(data["pace_differential"] - (data["pace"] - 100.0)) < 0.01


def test_pace_returns_404_when_game_missing():
    mock_redis = _make_mock_redis(None)
    with patch("src.api.aioredis.from_url", return_value=mock_redis):
        with TestClient(app) as client:
            response = client.get("/game/9999999999/pace")
    assert response.status_code == 404


def test_pace_high_pace_interpretation():
    """Pace > 105 returns a high-pace interpretation string."""
    mock_redis = _make_mock_redis(_make_state_json({"pace": 115.0}))
    with patch("src.api.aioredis.from_url", return_value=mock_redis):
        with TestClient(app) as client:
            response = client.get("/game/0042300401/pace")
    data = response.json()
    assert "high" in data["interpretation"].lower()


# ---------------------------------------------------------------------------
# /efficiency
# ---------------------------------------------------------------------------

def test_efficiency_returns_both_team_ratings():
    """/efficiency returns offensive ratings for both teams."""
    # home: 86 pts / 44 possessions * 100 = ~195.5 ORTG (unrealistic mid-game)
    mock_redis = _make_mock_redis(_make_state_json())
    with patch("src.api.aioredis.from_url", return_value=mock_redis):
        with TestClient(app) as client:
            response = client.get("/game/0042300401/efficiency")
    assert response.status_code == 200
    data = response.json()
    assert "home_offensive_rating" in data
    assert "away_offensive_rating" in data
    assert "home_ortg_vs_average" in data
    assert "away_ortg_vs_average" in data
    assert data["home_team"] == "BOS"
    assert data["away_team"] == "DAL"


def test_efficiency_ortg_formula():
    """Offensive rating = (score / possessions) * 100."""
    # home: 50 pts / 50 possessions = 100.0 ORTG
    state_json = _make_state_json({
        "home_score": 50, "away_score": 40,
        "home_possessions": 50, "away_possessions": 50,
    })
    mock_redis = _make_mock_redis(state_json)
    with patch("src.api.aioredis.from_url", return_value=mock_redis):
        with TestClient(app) as client:
            response = client.get("/game/0042300401/efficiency")
    data = response.json()
    assert abs(data["home_offensive_rating"] - 100.0) < 0.1
    assert abs(data["away_offensive_rating"] - 80.0) < 0.1


def test_efficiency_returns_404_when_game_missing():
    mock_redis = _make_mock_redis(None)
    with patch("src.api.aioredis.from_url", return_value=mock_redis):
        with TestClient(app) as client:
            response = client.get("/game/9999999999/efficiency")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# /win-probability
# ---------------------------------------------------------------------------

def test_win_probability_returns_200_and_valid_shape():
    mock_redis = _make_mock_redis(_make_state_json())
    with patch("src.api.aioredis.from_url", return_value=mock_redis):
        with TestClient(app) as client:
            response = client.get("/game/0042300401/win-probability")
    assert response.status_code == 200
    data = response.json()
    assert data["home_team"] == "BOS"
    assert data["away_team"] == "DAL"
    assert 0.0 <= data["home_win_probability"] <= 1.0
    assert abs(data["home_win_probability"] + data["away_win_probability"] - 1.0) < 1e-6
    assert data["is_final"] is False


def test_win_probability_final_game_is_deterministic():
    state_json = _make_state_json({
        "home_score": 110, "away_score": 100, "game_status": "final",
    })
    mock_redis = _make_mock_redis(state_json)
    with patch("src.api.aioredis.from_url", return_value=mock_redis):
        with TestClient(app) as client:
            response = client.get("/game/0042300401/win-probability")
    data = response.json()
    assert data["home_win_probability"] == 1.0
    assert data["away_win_probability"] == 0.0
    assert data["is_final"] is True


def test_win_probability_returns_404_when_game_missing():
    mock_redis = _make_mock_redis(None)
    with patch("src.api.aioredis.from_url", return_value=mock_redis):
        with TestClient(app) as client:
            response = client.get("/game/9999999999/win-probability")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# /events
# ---------------------------------------------------------------------------

def test_events_returns_stream_entries():
    """/events returns the raw stream entries from XREVRANGE."""
    fake_entries = [
        ("1717704000000-0", {"event_type": "score", "home_score": "3", "away_score": "0",
                             "period": "1", "clock": "11:00", "game_id": "0042300401"}),
        ("1717703990000-0", {"event_type": "period start", "period": "1", "clock": "12:00",
                             "game_id": "0042300401"}),
    ]
    mock_redis = _make_mock_redis(
        hget_return_value=_make_state_json(),
        xrevrange_return_value=fake_entries,
    )
    with patch("src.api.aioredis.from_url", return_value=mock_redis):
        with TestClient(app) as client:
            response = client.get("/game/0042300401/events")
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 2
    assert data["events"][0]["stream_id"] == "1717704000000-0"
    assert data["events"][0]["fields"]["event_type"] == "score"


def test_events_returns_404_when_stream_empty():
    """/events returns 404 when no events exist for the game."""
    mock_redis = _make_mock_redis(hget_return_value=None, xrevrange_return_value=[])
    with patch("src.api.aioredis.from_url", return_value=mock_redis):
        with TestClient(app) as client:
            response = client.get("/game/9999999999/events")
    assert response.status_code == 404


def test_events_limit_parameter_is_passed_to_redis():
    """`limit` query parameter is forwarded to XREVRANGE count."""
    fake_entries = [
        ("1717704000000-0", {"event_type": "score", "game_id": "0042300401"}),
    ]
    mock_redis = _make_mock_redis(
        hget_return_value=_make_state_json(),
        xrevrange_return_value=fake_entries,
    )
    with patch("src.api.aioredis.from_url", return_value=mock_redis):
        with TestClient(app) as client:
            response = client.get("/game/0042300401/events?limit=10")
    assert response.status_code == 200
    mock_redis.xrevrange.assert_called_once()
    call_kwargs = mock_redis.xrevrange.call_args
    assert call_kwargs.kwargs.get("count") == 10 or 10 in call_kwargs.args


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

def test_health_returns_ok():
    """/health always returns 200 with status ok — no Redis dependency."""
    mock_redis = _make_mock_redis(None)
    with patch("src.api.aioredis.from_url", return_value=mock_redis):
        with TestClient(app) as client:
            response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
