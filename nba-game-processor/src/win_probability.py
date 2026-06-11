"""
Win probability inference: load the trained model and score a live GameState.

scripts/train_win_probability.py is the train side; this is serve. They're
kept separate so the model can be retrained and swapped (new joblib file)
without touching the API or processor.
"""

import math
from pathlib import Path

import joblib

_MODEL_PATH = Path(__file__).parent / "models" / "win_probability.joblib"
_artifact = joblib.load(_MODEL_PATH)
_model = _artifact["model"]
_feature_names = _artifact["feature_names"]

GAME_MINUTES = 48.0


def _build_features(score_diff: int, minutes_remaining: float) -> list[float]:
    """Must match the feature construction in train_win_probability.py exactly."""
    minutes_remaining = max(minutes_remaining, 0.0)
    interaction = score_diff / math.sqrt(minutes_remaining + 1.0)
    return [score_diff, minutes_remaining, interaction]


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

    features = _build_features(score_diff, minutes_remaining)
    probability = _model.predict_proba([features])[0][1]
    return float(probability)
