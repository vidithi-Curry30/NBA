"""
Train the win-probability model from real play-by-play data.

Reads data/wp_training_data.csv (produced by scripts/fetch_training_games.py),
fits a logistic regression on (score_diff, minutes_remaining,
score_diff / sqrt(minutes_remaining + 1)), and saves it to
src/models/win_probability.joblib.
"""

import csv
import math
from pathlib import Path

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss
from sklearn.model_selection import train_test_split

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "wp_training_data.csv"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "src" / "models" / "win_probability.joblib"

FEATURE_NAMES = ["score_diff", "minutes_remaining", "score_diff_over_sqrt_time"]


def load_training_data() -> tuple[np.ndarray, np.ndarray]:
    X, y = [], []
    with open(DATA_PATH, newline="") as f:
        for row in csv.DictReader(f):
            score_diff = float(row["score_diff"])
            minutes_remaining = float(row["minutes_remaining"])
            interaction = score_diff / math.sqrt(minutes_remaining + 1.0)
            X.append([score_diff, minutes_remaining, interaction])
            y.append(int(row["home_won"]))
    return np.array(X), np.array(y)


def main() -> None:
    X, y = load_training_data()
    print(f"Loaded {len(X):,} training examples from {DATA_PATH.name}")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    model = LogisticRegression()
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1]

    print(f"\nTest accuracy:  {accuracy_score(y_test, y_pred):.3f}")
    print(f"Test log loss:  {log_loss(y_test, y_proba):.3f}")
    print(f"Test Brier:     {brier_score_loss(y_test, y_proba):.3f}")

    print("\nModel coefficients:")
    for name, coef in zip(FEATURE_NAMES, model.coef_[0]):
        print(f"  {name:<28} {coef:+.4f}")
    print(f"  {'intercept':<28} {model.intercept_[0]:+.4f}")

    joblib.dump({"model": model, "feature_names": FEATURE_NAMES}, OUTPUT_PATH)
    print(f"\nSaved model to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
