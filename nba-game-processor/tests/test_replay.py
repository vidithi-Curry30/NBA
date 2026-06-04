"""
Tests for replay.py: verify event ordering, schema normalization, and
speed-multiplier timing without hitting nba_api or Redis.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from src.replay import _normalize_historical_event, _run_replay


# ---------------------------------------------------------------------------
# Schema normalization tests
# ---------------------------------------------------------------------------

def test_normalize_score_event():
    """A made-shot row maps to event_type='score' with home/away scores split."""
    row = {
        "EVENTMSGTYPE": "1",
        "PCTIMESTRING": "10:30",
        "PERIOD": "1",
        "SCORE": "3 - 2",
        "HOMEDESCRIPTION": "Tatum 3PT Jump Shot (3 PTS)",
        "PLAYER1_NAME": "Jayson Tatum",
        "PLAYER1_TEAM_ABBREVIATION": "BOS",
        "EVENTNUM": "5",
    }
    event = _normalize_historical_event("0042300401", row)
    assert event["event_type"] == "score"
    assert event["game_id"] == "0042300401"
    assert event["clock"] == "10:30"
    assert event["period"] == "1"
    assert event["team"] == "BOS"


def test_normalize_turnover_event():
    """A turnover row maps to event_type='turnover'."""
    row = {
        "EVENTMSGTYPE": "5",
        "PCTIMESTRING": "7:15",
        "PERIOD": "2",
        "SCORE": None,
        "HOMEDESCRIPTION": None,
        "VISITORDESCRIPTION": "Doncic Turnover (P1.T3)",
        "PLAYER1_NAME": "Luka Doncic",
        "PLAYER1_TEAM_ABBREVIATION": "DAL",
        "EVENTNUM": "42",
    }
    event = _normalize_historical_event("0042300401", row)
    assert event["event_type"] == "turnover"
    assert "Doncic" in event["description"]


def test_normalize_substitution_event():
    """A substitution row maps to event_type='substitution'."""
    row = {
        "EVENTMSGTYPE": "8",
        "PCTIMESTRING": "6:00",
        "PERIOD": "1",
        "SCORE": None,
        "HOMEDESCRIPTION": "SUB: Holiday FOR White",
        "PLAYER1_NAME": "Derrick White",
        "PLAYER2_NAME": "Jrue Holiday",
        "PLAYER1_TEAM_ABBREVIATION": "BOS",
        "EVENTNUM": "15",
    }
    event = _normalize_historical_event("0042300401", row)
    assert event["event_type"] == "substitution"


def test_normalize_preserves_action_number():
    """action_number in the normalized event matches EVENTNUM from the row."""
    row = {
        "EVENTMSGTYPE": "1",
        "PCTIMESTRING": "5:00",
        "PERIOD": "3",
        "SCORE": "60 - 55",
        "HOMEDESCRIPTION": "Brown Layup (2 PTS)",
        "PLAYER1_NAME": "Jaylen Brown",
        "PLAYER1_TEAM_ABBREVIATION": "BOS",
        "EVENTNUM": "99",
    }
    event = _normalize_historical_event("0042300401", row)
    assert event["action_number"] == "99"


def test_normalize_score_parsing():
    """SCORE field '55 - 60' is split into away=55, home=60 correctly."""
    row = {
        "EVENTMSGTYPE": "1",
        "PCTIMESTRING": "3:00",
        "PERIOD": "3",
        "SCORE": "55 - 60",
        "HOMEDESCRIPTION": "Tatum 2PT Dunk (2 PTS)",
        "PLAYER1_NAME": "Jayson Tatum",
        "PLAYER1_TEAM_ABBREVIATION": "BOS",
        "EVENTNUM": "10",
    }
    event = _normalize_historical_event("0042300401", row)
    # nba_api SCORE format: "away - home"
    assert event["away_score"] == "55"
    assert event["home_score"] == "60"


# ---------------------------------------------------------------------------
# Event ordering and stream push tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_replay_pushes_events_in_order():
    """
    Events must be pushed to the stream in the same order they appear in
    the play-by-play response — action_number ascending.
    """
    rows = [
        {
            "EVENTMSGTYPE": "12", "PCTIMESTRING": "12:00", "PERIOD": "1",
            "SCORE": None, "HOMEDESCRIPTION": "Start Period 1",
            "VISITORDESCRIPTION": None, "PLAYER1_NAME": "",
            "PLAYER2_NAME": "", "PLAYER1_TEAM_ABBREVIATION": "", "EVENTNUM": "1",
        },
        {
            "EVENTMSGTYPE": "1", "PCTIMESTRING": "11:45", "PERIOD": "1",
            "SCORE": "0 - 2", "HOMEDESCRIPTION": "Tatum 2PT (2 PTS)",
            "VISITORDESCRIPTION": None, "PLAYER1_NAME": "Jayson Tatum",
            "PLAYER2_NAME": "", "PLAYER1_TEAM_ABBREVIATION": "BOS", "EVENTNUM": "2",
        },
        {
            "EVENTMSGTYPE": "5", "PCTIMESTRING": "11:30", "PERIOD": "1",
            "SCORE": None, "HOMEDESCRIPTION": None,
            "VISITORDESCRIPTION": "Doncic Turnover", "PLAYER1_NAME": "Luka Doncic",
            "PLAYER2_NAME": "", "PLAYER1_TEAM_ABBREVIATION": "DAL", "EVENTNUM": "3",
        },
    ]

    mock_df = MagicMock()
    mock_df.to_dict.return_value = rows

    pushed_action_numbers = []

    async def capture_push(redis_client, game_id, event):
        pushed_action_numbers.append(event["action_number"])

    mock_pbp = MagicMock()
    mock_pbp.get_data_frames.return_value = [mock_df]

    mock_redis = AsyncMock()
    mock_redis.xadd = AsyncMock()
    mock_redis.aclose = AsyncMock()

    with patch("src.replay.playbyplayv2.PlayByPlayV2", return_value=mock_pbp), \
         patch("src.replay.aioredis.from_url", return_value=mock_redis), \
         patch("src.replay._push_event", side_effect=capture_push):
        await _run_replay("0042300401", speed=1000.0)

    assert pushed_action_numbers == ["1", "2", "3"]


@pytest.mark.asyncio
async def test_replay_speed_flag_scales_sleep():
    """
    At speed=10, the sleep between events should be ~1/10 of the real-time gap.
    We verify asyncio.sleep is called with a value scaled by the speed factor.
    """
    rows = [
        {
            "EVENTMSGTYPE": "12", "PCTIMESTRING": "12:00", "PERIOD": "1",
            "SCORE": None, "HOMEDESCRIPTION": "Start",
            "VISITORDESCRIPTION": None, "PLAYER1_NAME": "",
            "PLAYER2_NAME": "", "PLAYER1_TEAM_ABBREVIATION": "", "EVENTNUM": "1",
        },
        {
            "EVENTMSGTYPE": "1", "PCTIMESTRING": "11:00", "PERIOD": "1",
            "SCORE": "0 - 2", "HOMEDESCRIPTION": "Tatum 2PT",
            "VISITORDESCRIPTION": None, "PLAYER1_NAME": "Jayson Tatum",
            "PLAYER2_NAME": "", "PLAYER1_TEAM_ABBREVIATION": "BOS", "EVENTNUM": "2",
        },
    ]

    mock_df = MagicMock()
    mock_df.to_dict.return_value = rows

    mock_pbp = MagicMock()
    mock_pbp.get_data_frames.return_value = [mock_df]

    mock_redis = AsyncMock()
    mock_redis.xadd = AsyncMock()
    mock_redis.aclose = AsyncMock()

    sleep_calls = []

    async def mock_sleep(duration):
        sleep_calls.append(duration)

    with patch("src.replay.playbyplayv2.PlayByPlayV2", return_value=mock_pbp), \
         patch("src.replay.aioredis.from_url", return_value=mock_redis), \
         patch("src.replay._push_event", new_callable=AsyncMock), \
         patch("asyncio.sleep", side_effect=mock_sleep):
        await _run_replay("0042300401", speed=10.0)

    # Gap between 12:00 and 11:00 is 60 seconds; at speed=10 → 6s sleep.
    assert len(sleep_calls) >= 1
    assert abs(sleep_calls[0] - 6.0) < 0.1
