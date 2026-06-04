"""
Unit tests for NBA efficiency metric calculations.

All tests are pure Python — no Redis, no async, no network.
"""

import pytest
from src.metrics import (
    LEAGUE_AVERAGE_OFFENSIVE_RATING,
    LEAGUE_AVERAGE_PACE,
    compute_efficiency,
)
from src.state import GameState


def make_state(home_score: int, away_score: int,
               home_poss: int, away_poss: int,
               pace: float = 0.0) -> GameState:
    """Build a GameState with known values for metric testing."""
    return GameState(
        game_id="0042300401",
        home_team="BOS",
        away_team="DAL",
        home_score=home_score,
        away_score=away_score,
        home_possessions=home_poss,
        away_possessions=away_poss,
        pace=pace,
    )


def test_offensive_rating_formula():
    """ORTG = (points / possessions) * 100."""
    state = make_state(50, 40, 50, 50)
    snap = compute_efficiency(state)
    assert abs(snap.home_offensive_rating - 100.0) < 0.1
    assert abs(snap.away_offensive_rating - 80.0) < 0.1


def test_offensive_rating_differential_vs_average():
    """ortg_vs_average = ortg - league_average (~113)."""
    state = make_state(113, 113, 100, 100)
    snap = compute_efficiency(state)
    assert abs(snap.home_ortg_vs_average - 0.0) < 0.1
    assert abs(snap.away_ortg_vs_average - 0.0) < 0.1


def test_offensive_rating_above_average():
    """A team scoring >113 per 100 possessions has positive ortg_vs_average."""
    state = make_state(130, 90, 100, 100)
    snap = compute_efficiency(state)
    assert snap.home_ortg_vs_average > 0
    assert snap.away_ortg_vs_average < 0


def test_no_division_by_zero_on_game_start():
    """compute_efficiency handles zero possessions without raising."""
    state = make_state(0, 0, 0, 0)
    snap = compute_efficiency(state)
    # max(..., 1) guard makes 0 possessions map to 1 for the denominator.
    assert snap.home_offensive_rating == 0.0
    assert snap.away_offensive_rating == 0.0


def test_pace_differential():
    """pace_vs_average = pace - 100 (league average)."""
    state = make_state(0, 0, 0, 0, pace=110.0)
    snap = compute_efficiency(state)
    assert abs(snap.pace_vs_average - 10.0) < 0.01


def test_pace_below_average():
    """Slow game produces negative pace_vs_average."""
    state = make_state(0, 0, 0, 0, pace=88.0)
    snap = compute_efficiency(state)
    assert snap.pace_vs_average < 0
