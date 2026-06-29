"""
Win probability inference: load the trained XGBoost model and score a GameState.

Feature construction lives in src/features.py, shared with the training
script to guarantee the vector stays in sync.
"""

from pathlib import Path

import joblib

from src.features import GAME_MINUTES, build_features  # noqa: F401 — re-exported for callers

_MODEL_PATH = Path(__file__).parent / "models" / "win_probability.joblib"
_artifact = joblib.load(_MODEL_PATH)
_model = _artifact["model"]
_feature_names = _artifact["feature_names"]


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
    return float(_model.predict_proba([features])[0][1])
