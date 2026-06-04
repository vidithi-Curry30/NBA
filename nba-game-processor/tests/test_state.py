"""
Unit tests for GameState event processing.

All tests are pure Python — no Redis, no nba_api, no network. This is
intentional: state logic should be testable without any infrastructure,
which is why it lives on the model rather than in processor.py.
"""

import pytest
from src.state import GameState


def make_base_state(game_id: str = "0042300401") -> GameState:
    """Return a fresh GameState with team names pre-set for test convenience."""
    state = GameState(game_id=game_id, home_team="BOS", away_team="DAL")
    return state


# ---------------------------------------------------------------------------
# Score update tests
# ---------------------------------------------------------------------------

def test_home_score_update():
    """Home score increments and possession is recorded as 'home_score'."""
    state = make_base_state()
    state.update({
        "event_type": "score",
        "home_score": "3",
        "away_score": "0",
        "period": "1",
        "clock": "11:00",
    })
    assert state.home_score == 3
    assert state.away_score == 0
    assert "home_score" in state.last_10_possessions


def test_away_score_update():
    """Away score increments and possession is recorded as 'away_score'."""
    state = make_base_state()
    state.home_score = 3
    state.update({
        "event_type": "score",
        "home_score": "3",
        "away_score": "2",
        "period": "1",
        "clock": "10:30",
    })
    assert state.away_score == 2
    assert "away_score" in state.last_10_possessions


def test_score_unchanged_event_does_not_record_possession():
    """
    An event that carries the current score without changing it should not
    create a duplicate possession entry.
    """
    state = make_base_state()
    state.home_score = 5
    state.away_score = 3
    initial_possessions = len(state.last_10_possessions)
    state.update({
        "event_type": "score",
        "home_score": "5",
        "away_score": "3",
        "period": "1",
        "clock": "9:00",
    })
    assert len(state.last_10_possessions) == initial_possessions


# ---------------------------------------------------------------------------
# Possession count tests
# ---------------------------------------------------------------------------

def test_possession_count_increments_on_score():
    """Each scoring play increments possession_count by exactly 1."""
    state = make_base_state()
    assert state.possession_count == 0
    state.update({"event_type": "score", "home_score": "2", "away_score": "0",
                  "period": "1", "clock": "11:00"})
    assert state.possession_count == 1


def test_possession_count_increments_on_turnover():
    """Turnovers count as possessions for pace even though no points score."""
    state = make_base_state()
    state.update({"event_type": "turnover", "period": "1", "clock": "10:00"})
    assert state.possession_count == 1
    assert state.last_10_possessions[-1] == "turnover"


def test_possession_count_increments_multiple():
    """Cumulative possession count across mixed event types."""
    state = make_base_state()
    state.update({"event_type": "score", "home_score": "2", "away_score": "0",
                  "period": "1", "clock": "11:00"})
    state.update({"event_type": "turnover", "period": "1", "clock": "10:30"})
    state.update({"event_type": "score", "home_score": "2", "away_score": "3",
                  "period": "1", "clock": "10:00"})
    assert state.possession_count == 3


# ---------------------------------------------------------------------------
# Momentum / last_10_possessions tests
# ---------------------------------------------------------------------------

def test_last_10_possessions_max_length():
    """last_10_possessions never exceeds 10 entries regardless of events."""
    state = make_base_state()
    current_home = 0
    current_away = 0
    for i in range(15):
        current_home += 2
        state.update({
            "event_type": "score",
            "home_score": str(current_home),
            "away_score": str(current_away),
            "period": "1",
            "clock": "11:00",
        })
    assert len(state.last_10_possessions) == 10


def test_last_10_possessions_correct_outcomes():
    """Possession outcomes are recorded in the correct order."""
    state = make_base_state()
    state.update({"event_type": "score", "home_score": "2", "away_score": "0",
                  "period": "1", "clock": "11:00"})
    state.update({"event_type": "turnover", "period": "1", "clock": "10:30"})
    state.update({"event_type": "score", "home_score": "2", "away_score": "3",
                  "period": "1", "clock": "10:00"})
    assert state.last_10_possessions == ["home_score", "turnover", "away_score"]


def test_last_10_possessions_rolling_window():
    """Oldest entries are dropped when the list exceeds 10."""
    state = make_base_state()
    current_home = 0
    current_away = 0
    # Push 11 home scores
    for i in range(11):
        current_home += 2
        state.update({
            "event_type": "score",
            "home_score": str(current_home),
            "away_score": str(current_away),
            "period": "1",
            "clock": "11:00",
        })
    assert len(state.last_10_possessions) == 10
    assert all(p == "home_score" for p in state.last_10_possessions)


# ---------------------------------------------------------------------------
# Pace calculation tests
# ---------------------------------------------------------------------------

def test_pace_formula():
    """
    Given known possession_count and minutes_elapsed, pace = (count/min)*48.
    At 10 possessions in 5 minutes: pace = (10/5)*48 = 96.0
    """
    state = make_base_state()
    state.possession_count = 10
    state.minutes_elapsed = 5.0
    state._update_pace()
    assert abs(state.pace - 96.0) < 0.01


def test_pace_zero_at_game_start():
    """Pace is 0.0 before any time has elapsed (avoids division by zero)."""
    state = make_base_state()
    assert state.pace == 0.0


def test_pace_updates_after_event():
    """Pace is recalculated after each event that advances the game clock."""
    state = make_base_state()
    state.update({
        "event_type": "score",
        "home_score": "2",
        "away_score": "0",
        "period": "1",
        "clock": "11:00",   # 1 minute elapsed in period 1
    })
    # 1 possession in 1 minute → pace = (1/1)*48 = 48
    assert state.pace > 0


# ---------------------------------------------------------------------------
# Period change tests
# ---------------------------------------------------------------------------

def test_period_change_updates_period():
    """Period counter increments on a 'period start' event."""
    state = make_base_state()
    state.update({"event_type": "period start", "period": "2", "clock": "12:00"})
    assert state.period == 2


def test_period_change_resets_clock():
    """Clock resets to 12:00 (or 5:00 for OT) on a period start event."""
    state = make_base_state()
    state.update({"event_type": "period start", "period": "2", "clock": "12:00"})
    assert state.clock == "12:00"


def test_ot_period_clock_resets_to_five_minutes():
    """Overtime period start resets clock to 5:00."""
    state = make_base_state()
    state.update({"event_type": "period start", "period": "5", "clock": "5:00"})
    assert state.period == 5
    assert state.clock == "5:00"


# ---------------------------------------------------------------------------
# Substitution tests
# ---------------------------------------------------------------------------

def test_substitution_swaps_home_player():
    """A home-team substitution removes the outgoing player and adds the incoming."""
    state = make_base_state()
    state.home_players_on_court = ["p1", "p2", "p3", "p4", "p5"]
    state.update({
        "event_type": "substitution",
        "team": "BOS",
        "player_in": "p6",
        "player_out": "p1",
        "period": "1",
        "clock": "8:00",
    })
    assert "p1" not in state.home_players_on_court
    assert "p6" in state.home_players_on_court


def test_substitution_swaps_away_player():
    """An away-team substitution updates the away roster, not the home roster."""
    state = make_base_state()
    state.away_players_on_court = ["a1", "a2", "a3", "a4", "a5"]
    state.update({
        "event_type": "substitution",
        "team": "DAL",
        "player_in": "a6",
        "player_out": "a2",
        "period": "1",
        "clock": "7:00",
    })
    assert "a2" not in state.away_players_on_court
    assert "a6" in state.away_players_on_court
    # Home roster must be untouched
    assert state.home_players_on_court == []


def test_substitution_unknown_team_is_ignored():
    """A substitution event with an unrecognized team abbreviation is a no-op."""
    state = make_base_state()
    state.home_players_on_court = ["p1", "p2", "p3", "p4", "p5"]
    state.update({
        "event_type": "substitution",
        "team": "XYZ",
        "player_in": "p6",
        "player_out": "p1",
        "period": "1",
        "clock": "6:00",
    })
    assert state.home_players_on_court == ["p1", "p2", "p3", "p4", "p5"]
