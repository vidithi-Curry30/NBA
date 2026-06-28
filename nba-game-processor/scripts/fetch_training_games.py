"""
Generate synthetic win-probability training data via Monte Carlo NBA game simulation.

Each simulated game walks possession-by-possession through 48 minutes using
realistic NBA scoring rates. We sample game state at regular intervals to
produce (score_diff, minutes_remaining, possession, home_won) rows.

Usage:
    python -m scripts.fetch_training_games --games 5000
"""

import argparse
import csv
import random
from pathlib import Path

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "wp_training_data.csv"

# NBA averages (2023-24 season)
POSSESSIONS_PER_GAME = 100          # each team, per 48 min
POINTS_PER_POSSESSION = 1.14        # league avg offensive rating / 100
# Probability breakdown per possession:
#   ~35% 3PA  -> made ~37% -> 3 pts
#   ~40% 2PA  -> made ~53% -> 2 pts
#   ~25% free throw trips -> ~1.5 pts avg (not modeled explicitly)
# We use a simple per-possession scoring distribution instead.

SCORING_DIST = [
    (0,  0.545),   # no score (turnover, miss + rebound to other team, etc.)
    (1,  0.030),   # 1 pt (and-1 or FT-only)
    (2,  0.310),   # 2 pts
    (3,  0.115),   # 3 pts
]
# Each possession takes roughly 14-15 seconds of game clock
SECONDS_PER_POSSESSION = 14.5


def _draw_points() -> int:
    r = random.random()
    cumul = 0.0
    for pts, prob in SCORING_DIST:
        cumul += prob
        if r < cumul:
            return pts
    return 0


def _simulate_game(sample_interval_minutes: float = 1.0) -> list[tuple[float, float, str, int]]:
    """
    Simulate one NBA game. Returns samples of
    (score_diff, minutes_remaining, possession, home_won).
    """
    home_score = 0
    away_score = 0
    elapsed = 0.0          # minutes elapsed
    # Coin-flip starting possession
    possession = random.choice(["home", "away"])

    samples: list[tuple[float, float, str, int]] = []
    next_sample = sample_interval_minutes

    while elapsed < 48.0:
        # Advance one possession
        pts = _draw_points()
        if possession == "home":
            home_score += pts
        else:
            away_score += pts

        elapsed += SECONDS_PER_POSSESSION / 60.0
        elapsed = min(elapsed, 48.0)

        # Alternate possession (simplified — turnovers / offensive rebounds
        # are already absorbed into the scoring distribution)
        possession = "away" if possession == "home" else "home"

        # Emit a sample at each interval
        if elapsed >= next_sample:
            minutes_remaining = max(48.0 - elapsed, 0.0)
            score_diff = home_score - away_score
            samples.append((score_diff, minutes_remaining, possession, -1))
            next_sample += sample_interval_minutes

    # Resolve outcome (simple OT: keep simulating if tied)
    ot_elapsed = 0.0
    while home_score == away_score:
        pts = _draw_points()
        if possession == "home":
            home_score += pts
        else:
            away_score += pts
        possession = "away" if possession == "home" else "home"
        ot_elapsed += SECONDS_PER_POSSESSION / 60.0
        if ot_elapsed >= 5.0:
            ot_elapsed = 0.0

    home_won = 1 if home_score > away_score else 0

    # Back-fill the outcome
    return [(sd, mr, poss, home_won) for sd, mr, poss, _ in samples]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=5000,
                        help="Number of games to simulate (default: 5000)")
    parser.add_argument("--interval", type=float, default=0.5,
                        help="Sample interval in minutes (default: 0.5)")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)

    print(f"Simulating {args.games:,} NBA games (sample every {args.interval} min)...")
    rows: list[tuple[float, float, str, int]] = []
    for i in range(args.games):
        rows.extend(_simulate_game(args.interval))
        if (i + 1) % 500 == 0:
            print(f"  {i + 1:,}/{args.games:,} games done — {len(rows):,} samples so far")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["score_diff", "minutes_remaining", "possession", "home_won"])
        writer.writerows(rows)

    home_win_rate = sum(r[3] for r in rows) / len(rows)
    with_poss = sum(1 for r in rows if r[2] != "")
    print(f"\nWrote {len(rows):,} samples from {args.games:,} simulated games → {OUTPUT_PATH}")
    print(f"Home win rate: {home_win_rate:.3f}  (expect ~0.500 — no home-court advantage in sim)")
    print(f"Possession known: {with_poss:,}/{len(rows):,} (100% — every possession is tracked)")


if __name__ == "__main__":
    main()
