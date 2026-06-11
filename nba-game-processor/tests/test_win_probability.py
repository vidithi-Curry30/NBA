"""
Unit tests for the win-probability inference module.

Pure Python/NumPy — no Redis, no network. Verifies the loaded model produces
sane, well-behaved probabilities and that the "final" short-circuit is exact.
"""

from src.state import GameState
from src.win_probability import predict_win_probability


def make_state(**overrides) -> GameState:
    base = dict(game_id="0042300401", home_team="BOS", away_team="DAL")
    base.update(overrides)
    return GameState(**base)


def test_probability_is_within_unit_interval():
    state = make_state(home_score=50, away_score=48, minutes_elapsed=24.0)
    prob = predict_win_probability(state)
    assert 0.0 <= prob <= 1.0


def test_tied_game_at_tipoff_is_close_to_half():
    """At 0-0 with 48 minutes left, the model should be near 50/50 (home edge only)."""
    state = make_state(home_score=0, away_score=0, minutes_elapsed=0.0)
    prob = predict_win_probability(state)
    assert 0.4 < prob < 0.6


def test_larger_lead_increases_win_probability():
    """A bigger lead with the same time remaining should raise P(home wins)."""
    small_lead = make_state(home_score=52, away_score=50, minutes_elapsed=24.0)
    big_lead = make_state(home_score=70, away_score=50, minutes_elapsed=24.0)
    assert predict_win_probability(big_lead) > predict_win_probability(small_lead)


def test_same_lead_late_in_game_is_more_decisive():
    """A fixed lead matters more with less time remaining."""
    early = make_state(home_score=60, away_score=55, minutes_elapsed=10.0)
    late = make_state(home_score=60, away_score=55, minutes_elapsed=46.0)
    assert predict_win_probability(late) > predict_win_probability(early)


def test_final_game_home_win_returns_one():
    state = make_state(home_score=110, away_score=100, minutes_elapsed=48.0,
                        game_status="final")
    assert predict_win_probability(state) == 1.0


def test_final_game_away_win_returns_zero():
    state = make_state(home_score=95, away_score=110, minutes_elapsed=48.0,
                        game_status="final")
    assert predict_win_probability(state) == 0.0


def test_final_tied_game_returns_half():
    """Shouldn't occur in real NBA data, but guards against malformed state."""
    state = make_state(home_score=100, away_score=100, minutes_elapsed=48.0,
                        game_status="final")
    assert predict_win_probability(state) == 0.5


def test_minutes_remaining_clamped_at_zero():
    """minutes_elapsed > 48 (e.g. OT) shouldn't crash or produce a negative sqrt."""
    state = make_state(home_score=110, away_score=108, minutes_elapsed=53.0)
    prob = predict_win_probability(state)
    assert 0.0 <= prob <= 1.0
