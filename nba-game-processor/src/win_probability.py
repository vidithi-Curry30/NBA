"""
Win probability inference: load a trained model and score live GameState.

This module is the "serve" half of a train/serve split — scripts/
train_win_probability.py is "train". Keeping them separate means the model
can be retrained and swapped (new joblib file) without touching the API or
processor code, and the inference path here stays a pure, fast function.
"""

import math
from pathlib import Path

import joblib

# WHY load at module import time, not per-request: joblib.load reads from
# disk and deserializes a scikit-learn object — doing this on every API call
# would dominate the sub-10ms latency budget. Loading once at process startup
# means inference is just a NumPy dot product plus a sigmoid, on the order of
# microseconds.
_MODEL_PATH = Path(__file__).parent / "models" / "win_probability.joblib"
_artifact = joblib.load(_MODEL_PATH)
_model = _artifact["model"]
_feature_names = _artifact["feature_names"]

GAME_MINUTES = 48.0


def _build_features(score_diff: int, minutes_remaining: float) -> list[float]:
    """
    Build the 3-feature vector the model was trained on.

    WHY this must exactly mirror scripts/train_win_probability.py's feature
    construction: a model is only as correct as the consistency between
    training-time and serving-time feature engineering. A mismatch here
    (a classic "training/serving skew" bug) would silently produce wrong
    probabilities without any error — the model would happily score
    nonsense features.
    """
    minutes_remaining = max(minutes_remaining, 0.0)
    interaction = score_diff / math.sqrt(minutes_remaining + 1.0)
    return [score_diff, minutes_remaining, interaction]


def predict_win_probability(state: "GameState") -> float:
    """
    Return P(home team wins) given the current game state.

    WHY short-circuit on game_status == "final": once the game has ended,
    the outcome is a known fact, not a probability — returning the model's
    estimate (which would be close to but not exactly 0 or 1) would be
    actively misleading. A finished game is 100%/0%, full stop.
    """
    if state.game_status == "final":
        if state.home_score > state.away_score:
            return 1.0
        elif state.away_score > state.home_score:
            return 0.0
        # WHY 0.5 on a tie at "final": shouldn't occur in real NBA data
        # (games can't end tied), but guards against malformed state rather
        # than returning a misleadingly precise model output.
        return 0.5

    score_diff = state.home_score - state.away_score
    minutes_remaining = GAME_MINUTES - state.minutes_elapsed

    features = _build_features(score_diff, minutes_remaining)
    # WHY [features] then [0][1]: scikit-learn's predict_proba expects a 2D
    # array (batch of samples) and returns probabilities for each class.
    # Index [0] selects our single sample; [1] selects P(class=1) = P(home win).
    probability = _model.predict_proba([features])[0][1]
    return float(probability)
