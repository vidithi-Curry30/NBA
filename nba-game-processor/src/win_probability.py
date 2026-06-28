"""
Win probability inference: load the trained XGBoost model and score a GameState.

Feature construction must stay in sync with scripts/train_win_probability.py.
"""

import math
from pathlib import Path

import joblib

_MODEL_PATH = Path(__file__).parent / "models" / "win_probability.joblib"
_artifact = joblib.load(_MODEL_PATH)
_model = _artifact["model"]
_feature_names = _artifact["feature_names"]

GAME_MINUTES = 48.0


def _minutes_to_period(minutes_remaining: float) -> int:
    elapsed = GAME_MINUTES - minutes_remaining
    if elapsed <= 0:
        return 1
    return min(int(elapsed / 12) + 1, 4)


def build_features(score_diff: int, minutes_remaining: float) -> list[float]:
    """Must match build_features() in train_win_probability.py exactly."""
    minutes_remaining = max(minutes_remaining, 0.0)
    interaction = score_diff / math.sqrt(minutes_remaining + 1.0)
    period = _minutes_to_period(minutes_remaining)
    is_clutch = 1.0 if (minutes_remaining <= 5.0 and abs(score_diff) <= 5) else 0.0
    abs_diff = abs(score_diff)
    score_diff_sq = score_diff ** 2
    return [score_diff, minutes_remaining, interaction, period, is_clutch, abs_diff, score_diff_sq]


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
    features = build_features(score_diff, minutes_remaining)
    return float(_model.predict_proba([features])[0][1])
