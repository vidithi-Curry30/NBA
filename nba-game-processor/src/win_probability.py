"""
Win probability inference: load the trained XGBoost model and score a GameState.

Feature construction must stay in sync with scripts/train_win_probability.py.
14 features including possession (home/away/unknown).
"""

import math
from pathlib import Path

import joblib

_MODEL_PATH = Path(__file__).parent / "models" / "win_probability.joblib"
_artifact = joblib.load(_MODEL_PATH)
_model = _artifact["model"]
_feature_names = _artifact["feature_names"]

GAME_MINUTES = 48.0


def build_features(
    score_diff: int,
    minutes_remaining: float,
    possession: str = "",
) -> list[float]:
    """Must match build_features() in train_win_probability.py exactly."""
    minutes_remaining = max(minutes_remaining, 0.0)
    elapsed = GAME_MINUTES - minutes_remaining
    period = min(int(elapsed / 12) + 1, 4) if elapsed > 0 else 1
    is_clutch = 1.0 if (minutes_remaining <= 5.0 and abs(score_diff) <= 5) else 0.0
    is_late_clutch = 1.0 if (minutes_remaining <= 2.0 and abs(score_diff) <= 3) else 0.0
    interaction = score_diff / math.sqrt(minutes_remaining + 1.0)
    abs_diff = abs(score_diff)
    score_diff_sq = score_diff ** 2
    time_pressure = 1.0 / (minutes_remaining + 0.5)
    lead_security = score_diff * time_pressure
    large_lead = 1.0 if abs_diff >= 15 else 0.0
    game_frac = elapsed / GAME_MINUTES

    if possession == "home":
        home_has_poss = 1.0
        trailing_has_poss = 1.0 if score_diff < 0 else 0.0
    elif possession == "away":
        home_has_poss = 0.0
        trailing_has_poss = 1.0 if score_diff > 0 else 0.0
    else:
        home_has_poss = 0.5
        trailing_has_poss = 0.5

    return [score_diff, minutes_remaining, interaction, period, is_clutch,
            is_late_clutch, abs_diff, score_diff_sq, time_pressure,
            lead_security, large_lead, game_frac,
            home_has_poss, trailing_has_poss]


def predict_win_probability(state: "GameState") -> float:
    """Return P(home team wins) given the current game state."""
    if state.game_status == "final":
        if state.home_score > state.away_score:
            return 1.0
        elif state.away_score > state.home_score:
            return 0.0
        return 0.5

    score_diff = state.home_score - state.away_score
    minutes_remaining = GAME_MINUTES - state.minutes_elapsed
    features = build_features(score_diff, minutes_remaining, state.current_possession)
    raw = float(_model.predict_proba([features])[0][1])
    # Clamp to [0.03, 0.97] — the model was trained on simulated games that
    # never produce true certainty mid-game, so extreme outputs are noise.
    return max(0.03, min(0.97, raw))


def kelly_fraction(p_win: float, odds: float = 1.0) -> float:
    """
    Kelly criterion: optimal fraction of bankroll to wager on home team.

    f* = (b*p - q) / b   where b = decimal odds (net), p = P(win), q = 1-p.

    Default odds=1.0 models an even-money bet (e.g. a spread bet at -110
    approximated as fair). Negative fraction means bet the away team instead;
    zero means no edge.
    """
    q = 1.0 - p_win
    f = (odds * p_win - q) / odds
    return round(f, 4)
