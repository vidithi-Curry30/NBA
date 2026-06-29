"""
Shared feature construction for the win-probability model.

Both the training script (scripts/train_win_probability.py) and the
inference module (src/win_probability.py) import from here so the feature
vector is guaranteed to stay in sync — a single source of truth.
"""

import math

GAME_MINUTES = 48.0

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
    "home_has_possession",
    "trailing_has_possession",
]


def build_features(
    score_diff: float,
    minutes_remaining: float,
    possession: str = "",
) -> list[float]:
    """
    Build the 14-element feature vector for a game snapshot.

    Args:
        score_diff: home_score - away_score (positive = home leading).
        minutes_remaining: minutes left in regulation (or overtime).
        possession: "home", "away", or "" (unknown).

    Returns:
        List of floats in FEATURE_NAMES order.
    """
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

    # Possession: 1.0 = home has ball, 0.0 = away has ball, 0.5 = unknown.
    # trailing_has_possession flags a comeback opportunity: trailing team holds
    # the ball, which compresses win probability toward 50%.
    if possession == "home":
        home_has_poss = 1.0
        trailing_has_poss = 1.0 if score_diff < 0 else 0.0
    elif possession == "away":
        home_has_poss = 0.0
        trailing_has_poss = 1.0 if score_diff > 0 else 0.0
    else:
        home_has_poss = 0.5   # neutral prior when unknown
        trailing_has_poss = 0.5

    return [score_diff, minutes_remaining, interaction, period, is_clutch,
            is_late_clutch, abs_diff, score_diff_sq, time_pressure,
            lead_security, large_lead, game_frac,
            home_has_poss, trailing_has_poss]
