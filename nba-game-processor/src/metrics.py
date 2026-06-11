"""
NBA efficiency metrics derived from GameState.

Pure functions, no Redis or async, so they can be unit tested with plain
GameState instances.
"""

from dataclasses import dataclass

from src.state import GameState

# 2023-24 NBA league averages (Basketball Reference).
LEAGUE_AVERAGE_PACE = 100.0              # possessions per team per 48 min
LEAGUE_AVERAGE_OFFENSIVE_RATING = 113.0  # points per 100 possessions


@dataclass
class EfficiencySnapshot:
    home_offensive_rating: float
    away_offensive_rating: float
    home_ortg_vs_average: float
    away_ortg_vs_average: float
    pace: float
    pace_vs_average: float


def compute_efficiency(state: GameState) -> EfficiencySnapshot:
    """Offensive rating = points per 100 possessions, for both teams."""
    # max(..., 1) avoids dividing by zero before either team has a
    # recorded possession.
    home_poss = max(state.home_possessions, 1)
    away_poss = max(state.away_possessions, 1)

    home_ortg = round((state.home_score / home_poss) * 100, 1)
    away_ortg = round((state.away_score / away_poss) * 100, 1)

    return EfficiencySnapshot(
        home_offensive_rating=home_ortg,
        away_offensive_rating=away_ortg,
        home_ortg_vs_average=round(home_ortg - LEAGUE_AVERAGE_OFFENSIVE_RATING, 1),
        away_ortg_vs_average=round(away_ortg - LEAGUE_AVERAGE_OFFENSIVE_RATING, 1),
        pace=round(state.pace, 2),
        pace_vs_average=round(state.pace - LEAGUE_AVERAGE_PACE, 2),
    )
