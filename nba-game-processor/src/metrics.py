"""
NBA efficiency metrics derived from GameState.

These are pure functions — no Redis, no async, no side effects. Keeping them
separate from state.py means they can be reused across endpoints and tested
with plain GameState instances. The API calls these functions after reading
the materialized state from Redis.
"""

from dataclasses import dataclass

from src.state import GameState

# 2023-24 NBA league averages. Source: Basketball Reference.
# WHY constants here rather than fetching live: these change slowly (one
# season = one update). Hardcoding avoids an additional API dependency and
# makes the differential interpretation deterministic and testable.
LEAGUE_AVERAGE_PACE = 100.0            # possessions per team per 48 min
LEAGUE_AVERAGE_OFFENSIVE_RATING = 113.0  # points per 100 possessions


@dataclass
class EfficiencySnapshot:
    """
    Efficiency metrics for both teams at a point in time.

    WHY a dataclass and not a Pydantic model: these values are computed on the
    fly from GameState and never serialized independently — they're always
    embedded in an API response Pydantic model. Dataclass is lighter weight
    and signals "computed view" rather than "persistent schema".
    """
    home_offensive_rating: float
    away_offensive_rating: float
    # WHY differentials: raw offensive rating is hard to interpret without
    # context. +/- vs. league average immediately tells you if a team is
    # above or below average — the same intuition as z-scores in statistics.
    home_ortg_vs_average: float
    away_ortg_vs_average: float
    pace: float
    pace_vs_average: float


def compute_efficiency(state: GameState) -> EfficiencySnapshot:
    """
    Compute in-game offensive ratings for both teams from current GameState.

    WHY offensive rating over raw score: points per 100 possessions normalizes
    for pace — a team scoring 120 in 120 possessions (ORTG 100) is far less
    efficient than one scoring 110 in 90 possessions (ORTG 122). This is the
    primary lens NBA front offices use to evaluate offense. Ref: Dean Oliver,
    "Basketball on Paper" (2004), the foundational text for NBA analytics.
    """
    # WHY max(..., 1): avoid division by zero in the first few events before
    # any possessions have been recorded for a team.
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
