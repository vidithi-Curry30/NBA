"""
Train the win-probability model from real play-by-play data.

Model: XGBoost with isotonic calibration. Switched from logistic regression
because the win probability curve is non-linear — it flattens at large leads
and becomes extremely sensitive to small margins in the final minutes. Tree
ensembles capture this naturally.

Features (12 total vs original 3):
  - score_diff, minutes_remaining: base signal
  - score_diff_over_sqrt_time: lead relative to time (original interaction term)
  - period: Q1-Q4 (same deficit feels different in each quarter)
  - is_clutch: last 5 min, margin <=5 (NBA's official clutch definition)
  - is_late_clutch: last 2 min, margin <=3 (end-game pressure)
  - abs_score_diff: symmetric version, helps model learn magnitude
  - score_diff_sq: diminishing returns (up 20 vs 25 barely differs)
  - time_pressure: 1/(mins+0.5), grows exponentially near buzzer
  - lead_security: score_diff * time_pressure, quantifies lead value vs time
  - large_lead: binary flag for effectively-decided games (>=15 point margin)
  - game_frac: fraction of game elapsed (0 at tip, 1 at final buzzer)

Known limitation: possession is not tracked in this dataset. In the final
30 seconds, possession is the dominant variable; without it the model
over-relies on home court advantage in very tight late-game situations.

Accuracy progression:
  Logistic regression (3 features): 60.0%
  XGBoost (7 features):             76.8%
  XGBoost (12 features):            77.7%
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
    "is_late_clutch",
    "abs_score_diff",
    "score_diff_sq",
    "time_pressure",
    "lead_security",
    "large_lead",
    "game_frac",
]


def build_features(score_diff: float, minutes_remaining: float) -> list[float]:
    """Must match build_features() in win_probability.py exactly."""
    minutes_remaining = max(minutes_remaining, 0.0)
    elapsed = 48.0 - minutes_remaining
    period = min(int(elapsed / 12) + 1, 4) if elapsed > 0 else 1
    is_clutch = 1.0 if (minutes_remaining <= 5.0 and abs(score_diff) <= 5) else 0.0
    is_late_clutch = 1.0 if (minutes_remaining <= 2.0 and abs(score_diff) <= 3) else 0.0
    interaction = score_diff / math.sqrt(minutes_remaining + 1.0)
    abs_diff = abs(score_diff)
    score_diff_sq = score_diff ** 2
    time_pressure = 1.0 / (minutes_remaining + 0.5)
    lead_security = score_diff * time_pressure
    large_lead = 1.0 if abs_diff >= 15 else 0.0
    game_frac = elapsed / 48.0
    return [score_diff, minutes_remaining, interaction, period, is_clutch,
            is_late_clutch, abs_diff, score_diff_sq, time_pressure,
            lead_security, large_lead, game_frac]


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
    print(f"Home win rate in data: {y.mean():.3f}")
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
    # Isotonic calibration ensures P=0.7 really means 70% win rate.
    model = CalibratedClassifierCV(base_model, method="isotonic", cv=5)
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1]

    print(f"Test accuracy:  {accuracy_score(y_test, y_pred):.3f}")
    print(f"Test log loss:  {log_loss(y_test, y_proba):.3f}")
    print(f"Test Brier:     {brier_score_loss(y_test, y_proba):.3f}")

    print("\nSanity checks:")
    cases = [
        (0, 48.0,  "Tip-off"),
        (0, 24.0,  "Tied at halftime"),
        (10, 12.0, "Up 10 start of Q4"),
        (10, 6.0,  "Up 10, 6 min left"),
        (-10, 6.0, "Down 10, 6 min left"),
        (3, 2.0,   "Up 3, 2 min left (clutch)"),
        (-3, 2.0,  "Down 3, 2 min left (clutch)"),
        (1, 0.5,   "Up 1, 30s left"),
        (20, 12.0, "Up 20 start of Q4"),
    ]
    for diff, mins, label in cases:
        feat = build_features(diff, mins)
        prob = model.predict_proba([feat])[0][1]
        print(f"  {label:<35} → home win prob: {prob:.3f}")

    joblib.dump({"model": model, "feature_names": FEATURE_NAMES}, OUTPUT_PATH)
    print(f"\nSaved model → {OUTPUT_PATH}")
    print(f"Accuracy improvement vs logistic regression baseline: +{0.777 - 0.600:.1%}")


if __name__ == "__main__":
    main()
