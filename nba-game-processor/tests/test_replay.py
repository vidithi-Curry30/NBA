"""
Tests for replay.py: verify event ordering, schema normalization, and
speed-multiplier timing without hitting the NBA CDN or Redis.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from src.replay import _normalize_cdn_event, _run_replay


# ---------------------------------------------------------------------------
# Schema normalization tests
# ---------------------------------------------------------------------------

def test_normalize_score_event():
    """A made 2pt action maps to event_type='score'."""
    play = {
        "actionType": "2pt",
        "shotResult": "Made",
        "clock": "PT10M30S",
        "period": "1",
        "scoreHome": "2",
        "scoreAway": "0",
        "playerNameI": "J. Tatum",
        "teamTricode": "BOS",
        "actionNumber": "5",
        "description": "Tatum 2PT Dunk",
    }
    event = _normalize_cdn_event("0042300401", play, "BOS", "DAL")
    assert event["event_type"] == "score"
    assert event["game_id"] == "0042300401"
    assert event["home_score"] == "2"
    assert event["team"] == "BOS"
    assert event["home_team"] == "BOS"
    assert event["away_team"] == "DAL"


def test_normalize_missed_shot():
    play = {
        "actionType": "3pt",
        "shotResult": "Missed",
        "clock": "PT07M15S",
        "period": "2",
        "scoreHome": "",
        "scoreAway": "",
        "playerNameI": "L. Doncic",
        "teamTricode": "DAL",
        "actionNumber": "42",
        "description": "Doncic 3PT missed",
    }
    event = _normalize_cdn_event("0042300401", play, "BOS", "DAL")
    assert event["event_type"] == "missed shot"


def test_normalize_turnover_event():
    play = {
        "actionType": "turnover",
        "clock": "PT07M15S",
        "period": "2",
        "scoreHome": "",
        "scoreAway": "",
        "playerNameI": "L. Doncic",
        "teamTricode": "DAL",
        "actionNumber": "42",
        "description": "Doncic Turnover",
    }
    event = _normalize_cdn_event("0042300401", play, "BOS", "DAL")
    assert event["event_type"] == "turnover"
    assert "Doncic" in event["description"]


def test_normalize_substitution_event():
    play = {
        "actionType": "substitution",
        "subType": "in",
        "clock": "PT06M00S",
        "period": "1",
        "scoreHome": "",
        "scoreAway": "",
        "playerNameI": "J. Holiday",
        "teamTricode": "BOS",
        "actionNumber": "15",
        "description": "Holiday in for White",
    }
    event = _normalize_cdn_event("0042300401", play, "BOS", "DAL")
    assert event["event_type"] == "substitution"
    assert event["sub_type"] == "in"


def test_normalize_foul_event():
    play = {
        "actionType": "foul",
        "clock": "PT05M00S",
        "period": "2",
        "scoreHome": "",
        "scoreAway": "",
        "playerNameI": "J. Tatum",
        "teamTricode": "BOS",
        "actionNumber": "20",
        "description": "Tatum Personal Foul",
    }
    event = _normalize_cdn_event("0042300401", play, "BOS", "DAL")
    assert event["event_type"] == "foul"
    assert event["player"] == "J. Tatum"


def test_normalize_preserves_action_number():
    play = {
        "actionType": "2pt",
        "shotResult": "Made",
        "clock": "PT05M00S",
        "period": "3",
        "scoreHome": "60",
        "scoreAway": "55",
        "playerNameI": "J. Brown",
        "teamTricode": "BOS",
        "actionNumber": "99",
        "description": "Brown Layup",
    }
    event = _normalize_cdn_event("0042300401", play, "BOS", "DAL")
    assert event["action_number"] == "99"


# ---------------------------------------------------------------------------
# Event ordering and stream push tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_replay_pushes_events_in_order():
    """Events are pushed to the stream in play order (actionNumber ascending)."""
    actions = [
        {"actionType": "period", "clock": "PT12M00S", "period": "1",
         "scoreHome": "", "scoreAway": "", "playerNameI": "", "teamTricode": "",
         "actionNumber": "1", "description": "Start Period 1"},
        {"actionType": "2pt", "shotResult": "Made", "clock": "PT11M45S", "period": "1",
         "scoreHome": "2", "scoreAway": "0", "playerNameI": "J. Tatum",
         "teamTricode": "BOS", "actionNumber": "2", "description": "Tatum 2PT"},
        {"actionType": "turnover", "clock": "PT11M30S", "period": "1",
         "scoreHome": "", "scoreAway": "", "playerNameI": "L. Doncic",
         "teamTricode": "DAL", "actionNumber": "3", "description": "Doncic Turnover"},
    ]

    pushed_action_numbers = []

    async def capture_push(redis_client, game_id, event):
        pushed_action_numbers.append(event["action_number"])

    mock_redis = AsyncMock()
    mock_redis.aclose = AsyncMock()

    with patch("src.replay._fetch_play_by_play", return_value=(actions, "BOS", "DAL")), \
         patch("src.replay.aioredis.from_url", return_value=mock_redis), \
         patch("src.replay._push_event", side_effect=capture_push):
        await _run_replay("0042300401", speed=1000.0)

    assert pushed_action_numbers == ["1", "2", "3"]


@pytest.mark.asyncio
async def test_replay_speed_flag_scales_sleep():
    """At speed=10, the sleep between events is 1/10 of the real-time gap."""
    actions = [
        {"actionType": "period", "clock": "PT12M00S", "period": "1",
         "scoreHome": "", "scoreAway": "", "playerNameI": "", "teamTricode": "",
         "actionNumber": "1", "description": "Start"},
        {"actionType": "2pt", "shotResult": "Made", "clock": "PT11M00S", "period": "1",
         "scoreHome": "2", "scoreAway": "0", "playerNameI": "J. Tatum",
         "teamTricode": "BOS", "actionNumber": "2", "description": "Tatum 2PT"},
    ]

    mock_redis = AsyncMock()
    mock_redis.aclose = AsyncMock()

    sleep_calls = []

    async def mock_sleep(duration):
        sleep_calls.append(duration)

    with patch("src.replay._fetch_play_by_play", return_value=(actions, "BOS", "DAL")), \
         patch("src.replay.aioredis.from_url", return_value=mock_redis), \
         patch("src.replay._push_event", new_callable=AsyncMock), \
         patch("asyncio.sleep", side_effect=mock_sleep):
        await _run_replay("0042300401", speed=10.0)

    # Gap between PT12M00S and PT11M00S is 60 seconds; at speed=10 → 6s sleep.
    assert len(sleep_calls) >= 1
    assert abs(sleep_calls[0] - 6.0) < 0.1
