"""
Train the win-probability model from real play-by-play data.

Model: XGBoost with isotonic calibration.

Features (13 total when possession is known, 12 when unknown):
  Core signal:
    score_diff, minutes_remaining
  Time-adjusted lead:
    score_diff_over_sqrt_time, time_pressure, lead_security
  Game context:
    period, game_frac, large_lead
  Clutch flags:
    is_clutch (last 5 min, margin<=5), is_late_clutch (last 2 min, margin<=3)
  Magnitude:
    abs_score_diff, score_diff_sq
  Possession (when known):
    home_has_possession: 1=home, 0=away, 0.5=unknown
    trailing_has_possession: 1 if trailing team has ball (comeback opportunity)

Possession handling: the training CSV marks possession as "home"/"away"/"".
For unknown possession we use 0.5 (neutral prior). This means possession
is most informative in clutch situations where it's actually tracked, which
is exactly where it matters most.

Accuracy progression:
  Logistic regression (3 features):       60.0%
  XGBoost (7 features):                   76.8%
  XGBoost (12 features):                  77.7%
  XGBoost (13 features + possession):     ~79%  (after re-fetch with possession)
"""

import csv
from pathlib import Path

import joblib
import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss
from sklearn.model_selection import train_test_split
from xgboost import XGBClassifier

from src.features import FEATURE_NAMES, build_features  # single source of truth

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "wp_training_data.csv"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "src" / "models" / "win_probability.joblib"


def load_training_data() -> tuple[np.ndarray, np.ndarray]:
    X, y = [], []
    has_possession_col = False
    with open(DATA_PATH, newline="") as f:
        reader = csv.DictReader(f)
        has_possession_col = "possession" in (reader.fieldnames or [])
        for row in reader:
            score_diff = float(row["score_diff"])
            minutes_remaining = float(row["minutes_remaining"])
            possession = row.get("possession", "") if has_possession_col else ""
            X.append(build_features(score_diff, minutes_remaining, possession))
            y.append(int(row["home_won"]))

    if has_possession_col:
        known = sum(1 for x in X if x[12] != 0.5)
        print(f"Possession column present — known for {known:,}/{len(X):,} samples ({100*known/len(X):.1f}%)")
    else:
        print("No possession column in CSV — using neutral prior (0.5) for all samples.")
        print("Re-run: python -m scripts.fetch_training_games to get possession labels.")

    return np.array(X), np.array(y)


def main() -> None:
    X, y = load_training_data()
    print(f"Loaded {len(X):,} training examples from {DATA_PATH.name}")
    print(f"Home win rate: {y.mean():.3f}")
    print(f"Features ({len(FEATURE_NAMES)}): {FEATURE_NAMES}\n")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    base_model = XGBClassifier(
        n_estimators=800,
        max_depth=6,
        learning_rate=0.02,
        subsample=0.75,
        colsample_bytree=0.7,
        min_child_weight=5,
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

    print("\nSanity checks (with possession):")
    cases = [
        (0,   48.0, "",     "Tip-off"),
        (0,   24.0, "",     "Tied at halftime"),
        (10,  6.0,  "",     "Up 10, 6 min left"),
        (-10, 6.0,  "",     "Down 10, 6 min left"),
        (3,   2.0,  "home", "Up 3, 2 min left — home has ball"),
        (-3,  2.0,  "home", "Down 3, 2 min left — home has ball"),
        (-3,  2.0,  "away", "Down 3, 2 min left — away has ball"),
        (1,   0.5,  "home", "Up 1, 30s — home has ball"),
        (-1,  0.5,  "home", "Down 1, 30s — home has ball"),
        (-1,  0.5,  "away", "Down 1, 30s — away has ball"),
    ]
    for diff, mins, poss, label in cases:
        feat = build_features(diff, mins, poss)
        prob = model.predict_proba([feat])[0][1]
        print(f"  {label:<45} → {prob:.3f}")

    joblib.dump({"model": model, "feature_names": FEATURE_NAMES}, OUTPUT_PATH)
    print(f"\nSaved model → {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
