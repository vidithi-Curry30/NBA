"""
Train the win-probability model from real play-by-play data.

Switched from logistic regression to XGBoost with richer features.
Key additions:
  - period (1-4): being down 5 in Q1 vs Q4 is qualitatively different
  - is_clutch: last 5 minutes, margin <= 5 (NBA's official clutch definition)
  - abs_score_diff: helps the model learn symmetric behavior around 0
  - score_diff_sq: captures diminishing returns (being up 20 vs 25 barely matters)

XGBoost outperforms logistic regression here because the relationship between
score_diff and win probability is non-linear — it flattens at the extremes and
steepens dramatically in the final minutes. Tree ensembles capture this naturally
without manual feature engineering of the interaction term.
"""

import csv
import math
from pathlib import Path

import joblib
import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss
from sklearn.model_selection import train_test_split
from xgboost import XGBClassifier

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "wp_training_data.csv"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "src" / "models" / "win_probability.joblib"

FEATURE_NAMES = [
    "score_diff",
    "minutes_remaining",
    "score_diff_over_sqrt_time",
    "period",
    "is_clutch",
    "abs_score_diff",
    "score_diff_sq",
]


def _minutes_to_period(minutes_remaining: float) -> int:
    """Derive period (1-4+) from minutes remaining in game."""
    elapsed = 48.0 - minutes_remaining
    if elapsed <= 0:
        return 1
    period = min(int(elapsed / 12) + 1, 4)
    return period


def build_features(score_diff: float, minutes_remaining: float) -> list[float]:
    """Must match win_probability.py exactly — both sides of the train/serve split."""
    minutes_remaining = max(minutes_remaining, 0.0)
    interaction = score_diff / math.sqrt(minutes_remaining + 1.0)
    period = _minutes_to_period(minutes_remaining)
    is_clutch = 1.0 if (minutes_remaining <= 5.0 and abs(score_diff) <= 5) else 0.0
    abs_diff = abs(score_diff)
    score_diff_sq = score_diff ** 2

    return [score_diff, minutes_remaining, interaction, period, is_clutch, abs_diff, score_diff_sq]


def load_training_data() -> tuple[np.ndarray, np.ndarray]:
    X, y = [], []
    with open(DATA_PATH, newline="") as f:
        for row in csv.DictReader(f):
            score_diff = float(row["score_diff"])
            minutes_remaining = float(row["minutes_remaining"])
            X.append(build_features(score_diff, minutes_remaining))
            y.append(int(row["home_won"]))
    return np.array(X), np.array(y)


def main() -> None:
    X, y = load_training_data()
    print(f"Loaded {len(X):,} training examples from {DATA_PATH.name}")
    print(f"Features: {FEATURE_NAMES}\n")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    # XGBoost with isotonic calibration — calibration ensures the output
    # probabilities are accurate (0.7 really means 70% win rate), not just
    # well-ranked. This matters for the win probability bar to be meaningful.
    base_model = XGBClassifier(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        eval_metric="logloss",
        random_state=42,
        verbosity=0,
    )
    model = CalibratedClassifierCV(base_model, method="isotonic", cv=5)
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1]

    print(f"Test accuracy:  {accuracy_score(y_test, y_pred):.3f}")
    print(f"Test log loss:  {log_loss(y_test, y_proba):.3f}")
    print(f"Test Brier:     {brier_score_loss(y_test, y_proba):.3f}")

    # Sanity checks: classic NBA situations
    print("\nSanity checks:")
    cases = [
        (0, 24.0, "Tied at halftime"),
        (10, 6.0, "Up 10, 6 min left"),
        (-10, 6.0, "Down 10, 6 min left"),
        (3, 2.0, "Up 3, 2 min left (clutch)"),
        (-3, 2.0, "Down 3, 2 min left (clutch)"),
        (20, 12.0, "Up 20 at start of Q4"),
        (0, 0.5, "Tied with 30s left"),
    ]
    for diff, mins, label in cases:
        feat = build_features(diff, mins)
        prob = model.predict_proba([feat])[0][1]
        print(f"  {label:<35} → home win prob: {prob:.3f}")

    joblib.dump({"model": model, "feature_names": FEATURE_NAMES}, OUTPUT_PATH)
    print(f"\nSaved model → {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
