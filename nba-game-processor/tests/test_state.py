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
    return GameState(game_id=game_id, home_team="BOS", away_team="DAL")


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
    An event carrying the current score without changing it should not
    create a duplicate possession entry.
    """
    state = make_base_state()
    state.home_score = 5
    state.away_score = 3
    initial_home = state.home_possessions
    state.update({
        "event_type": "score",
        "home_score": "5",
        "away_score": "3",
        "period": "1",
        "clock": "9:00",
    })
    assert state.home_possessions == initial_home


# ---------------------------------------------------------------------------
# Possession count tests — now per-team
# ---------------------------------------------------------------------------

def test_home_possession_increments_on_home_score():
    """Home scoring play increments home_possessions, not away_possessions."""
    state = make_base_state()
    state.update({"event_type": "score", "home_score": "2", "away_score": "0",
                  "period": "1", "clock": "11:00"})
    assert state.home_possessions == 1
    assert state.away_possessions == 0


def test_away_possession_increments_on_away_score():
    """Away scoring play increments away_possessions, not home_possessions."""
    state = make_base_state()
    state.home_score = 2
    state.update({"event_type": "score", "home_score": "2", "away_score": "3",
                  "period": "1", "clock": "10:30"})
    assert state.away_possessions == 1
    assert state.home_possessions == 0


def test_possession_count_property_is_sum():
    """possession_count property equals home + away possessions."""
    state = make_base_state()
    state.update({"event_type": "score", "home_score": "2", "away_score": "0",
                  "period": "1", "clock": "11:00"})
    state.home_score = 2
    state.update({"event_type": "score", "home_score": "2", "away_score": "3",
                  "period": "1", "clock": "10:30"})
    assert state.possession_count == 2
    assert state.possession_count == state.home_possessions + state.away_possessions


def test_turnover_increments_correct_team_possession():
    """Turnover by DAL (away) increments away_possessions."""
    state = make_base_state()
    state.update({
        "event_type": "turnover",
        "team": "DAL",
        "period": "1",
        "clock": "10:00",
    })
    assert state.away_possessions == 1
    assert state.home_possessions == 0
    assert state.last_10_possessions[-1] == "turnover"


# ---------------------------------------------------------------------------
# Momentum / last_10_possessions tests
# ---------------------------------------------------------------------------

def test_last_10_possessions_max_length():
    """last_10_possessions never exceeds 10 entries regardless of events."""
    state = make_base_state()
    current_home = 0
    for _ in range(15):
        current_home += 2
        state.update({
            "event_type": "score",
            "home_score": str(current_home),
            "away_score": "0",
            "period": "1",
            "clock": "11:00",
        })
    assert len(state.last_10_possessions) == 10


def test_last_10_possessions_correct_outcomes():
    """Possession outcomes are recorded in the correct order."""
    state = make_base_state()
    state.update({"event_type": "score", "home_score": "2", "away_score": "0",
                  "period": "1", "clock": "11:00"})
    state.update({"event_type": "turnover", "team": "DAL", "period": "1", "clock": "10:30"})
    state.home_score = 2
    state.update({"event_type": "score", "home_score": "2", "away_score": "3",
                  "period": "1", "clock": "10:00"})
    assert list(state.last_10_possessions) == ["home_score", "turnover", "away_score"]


def test_last_10_possessions_rolling_window():
    """Oldest entries are dropped when the list exceeds 10."""
    state = make_base_state()
    current_home = 0
    for _ in range(11):
        current_home += 2
        state.update({
            "event_type": "score",
            "home_score": str(current_home),
            "away_score": "0",
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
    Given known per-team possessions and minutes_elapsed, verify pace formula.

    Formula: ((home + away) / 2 / minutes_elapsed) * 48
    At 10 home + 10 away possessions in 10 minutes: pace = (10/10)*48 = 48.
    Divide by 2 because pace is per-team per 48 min, not combined possessions.
    """
    state = make_base_state()
    state.home_possessions = 10
    state.away_possessions = 10
    state.minutes_elapsed = 10.0
    state._update_pace()
    assert abs(state.pace - 48.0) < 0.01


def test_pace_per_team_not_combined():
    """
    If home has 20 possessions and away has 0, pace should reflect 10 per team
    average — not 20 combined. This verifies the /2 in the formula.
    """
    state = make_base_state()
    state.home_possessions = 20
    state.away_possessions = 0
    state.minutes_elapsed = 10.0
    state._update_pace()
    # (20 + 0) / 2 / 10 * 48 = 48.0
    assert abs(state.pace - 48.0) < 0.01


def test_pace_zero_at_game_start():
    """Pace is 0.0 before any time has elapsed (avoids division by zero)."""
    state = make_base_state()
    assert state.pace == 0.0


def test_pace_updates_after_event():
    """Pace is recalculated and non-zero after a scoring event."""
    state = make_base_state()
    state.update({
        "event_type": "score",
        "home_score": "2",
        "away_score": "0",
        "period": "1",
        "clock": "11:00",
    })
    assert state.pace > 0


# ---------------------------------------------------------------------------
# Period change tests
# ---------------------------------------------------------------------------

def test_period_change_updates_period():
    """Period counter updates on a 'period start' event."""
    state = make_base_state()
    state.update({"event_type": "period start", "period": "2", "clock": "12:00"})
    assert state.period == 2


def test_period_change_resets_clock():
    """Clock resets to 12:00 on a regulation period start."""
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
# Substitution tests — live API pattern (sub_type = in/out)
# ---------------------------------------------------------------------------

def test_substitution_in_adds_player():
    """A sub_type='in' event appends the player to the correct team roster."""
    state = make_base_state()
    state.home_players_on_court = ["p1", "p2", "p3", "p4", "p5"]
    state.update({
        "event_type": "substitution",
        "team": "BOS",
        "sub_type": "in",
        "player": "p6",
        "period": "1",
        "clock": "8:00",
    })
    assert "p6" in state.home_players_on_court


def test_substitution_out_removes_player():
    """A sub_type='out' event removes the player from the correct team roster."""
    state = make_base_state()
    state.home_players_on_court = ["p1", "p2", "p3", "p4", "p5"]
    state.update({
        "event_type": "substitution",
        "team": "BOS",
        "sub_type": "out",
        "player": "p3",
        "period": "1",
        "clock": "8:00",
    })
    assert "p3" not in state.home_players_on_court
    assert len(state.home_players_on_court) == 4


# ---------------------------------------------------------------------------
# Substitution tests — historical API pattern (player_in / player_out)
# ---------------------------------------------------------------------------

def test_substitution_historical_swaps_home_player():
    """Historical combined substitution event swaps the correct players."""
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


def test_substitution_historical_swaps_away_player():
    """Away-team historical substitution updates away roster only."""
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
    assert state.home_players_on_court == []


def test_substitution_unknown_team_is_noop():
    """A substitution with an unrecognized team abbreviation is a no-op."""
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


# ---------------------------------------------------------------------------
# Foul tracking tests
# ---------------------------------------------------------------------------

def test_foul_increments_player_count():
    state = make_base_state()
    for _ in range(3):
        state.update({"event_type": "foul", "player": "Tatum", "period": "2", "clock": "8:00"})
    assert state.player_fouls["Tatum"] == 3


def test_foul_trouble_before_q4():
    """4 fouls before Q4 = foul trouble."""
    state = make_base_state()
    state.period = 3
    state.player_fouls["Tatum"] = 4
    trouble = state.foul_trouble_players()
    assert "Tatum" in trouble


def test_no_foul_trouble_in_q4_at_four():
    """4 fouls in Q4 is not trouble — only 5+ triggers it."""
    state = make_base_state()
    state.period = 4
    state.player_fouls["Tatum"] = 4
    trouble = state.foul_trouble_players()
    assert "Tatum" not in trouble


def test_fouled_out_at_six():
    state = make_base_state()
    state.period = 4
    state.player_fouls["Tatum"] = 6
    trouble = state.foul_trouble_players()
    assert "Tatum" in trouble


def test_foul_event_ignored_without_player():
    """Foul events with no player name don't crash or add empty key."""
    state = make_base_state()
    state.update({"event_type": "foul", "player": "", "period": "1", "clock": "10:00"})
    assert state.player_fouls == {}
