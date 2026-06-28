"""
Download real play-by-play data for a sample of completed NBA games and
write it to data/wp_training_data.csv for scripts/train_win_probability.py.

Usage:
    python -m scripts.fetch_training_games --games 150
"""

import argparse
import csv
import json
import re
import time
import urllib.request
from pathlib import Path

NBA_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.nba.com/"}
SCHEDULE_URL = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json"
PLAYBYPLAY_URL = "https://cdn.nba.com/static/json/liveData/playbyplay/playbyplay_{game_id}.json"

GAME_MINUTES = 48.0
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "wp_training_data.csv"


def _fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=NBA_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _final_game_ids(max_games: int) -> list[str]:
    schedule = _fetch_json(SCHEDULE_URL)
    game_ids = []
    for game_date in schedule["leagueSchedule"]["gameDates"]:
        for game in game_date["games"]:
            if game["gameStatus"] == 3 and game["gameId"].startswith("002"):
                game_ids.append(game["gameId"])

    if len(game_ids) <= max_games:
        return game_ids

    step = len(game_ids) / max_games
    return [game_ids[int(i * step)] for i in range(max_games)]


def _clock_to_minutes_remaining(period: int, clock: str) -> float:
    """Mirror src/state.py's clock parsing to match serving-time features."""
    match = re.match(r"PT(\d+)M([\d.]+)S", clock)
    if not match:
        return max(GAME_MINUTES - (period - 1) * 12.0, 0.0)
    minutes_left = float(match.group(1))
    seconds_left = float(match.group(2))

    if period <= 4:
        elapsed = (period - 1) * 12.0 + (12.0 - minutes_left - seconds_left / 60.0)
    else:
        ot_number = period - 4
        elapsed = 48.0 + (ot_number - 1) * 5.0 + (5.0 - minutes_left - seconds_left / 60.0)

    return max(GAME_MINUTES - elapsed, 0.0)


def _infer_possession(action: dict, last_shooting_team: str, home_team: str) -> str:
    """
    Derive current possession from a single play-by-play action.
    Returns "home", "away", or "" (unknown).
    """
    action_type = str(action.get("actionType", "")).lower()
    shot_result = str(action.get("shotResult", "")).lower()
    team = str(action.get("teamTricode", ""))
    sub_type = str(action.get("subType", "")).lower()

    if action_type in ("2pt", "3pt", "freethrow") and shot_result == "made":
        # Made basket: other team inbounds next
        return "away" if team == home_team else "home"
    elif action_type in ("2pt", "3pt", "freethrow") and shot_result == "missed":
        return ""  # possession unknown until rebound
    elif action_type == "rebound":
        return "home" if team == home_team else "away"
    elif action_type == "turnover":
        # Turnover: other team gets it
        return "away" if team == home_team else "home"
    return ""  # default: don't change possession


def _extract_samples(playbyplay: dict) -> list[tuple[int, float, str, int]]:
    game = playbyplay["game"]
    actions = game.get("actions", [])
    if not actions:
        return []

    # Determine home team from the game object
    home_team = game.get("homeTeam", {}).get("teamTricode", "")

    # Find last action with a score to determine winner
    final_home, final_away = 0, 0
    for action in reversed(actions):
        sh = action.get("scoreHome")
        sa = action.get("scoreAway")
        if sh is not None and sa is not None:
            try:
                final_home, final_away = int(sh), int(sa)
                break
            except (ValueError, TypeError):
                continue
    if final_home == final_away:
        return []
    home_won = 1 if final_home > final_away else 0

    # Walk all events to maintain possession state, sample every 3rd
    current_possession = ""
    last_shooting_team = ""
    samples = []

    for i, action in enumerate(actions):
        action_type = str(action.get("actionType", "")).lower()
        shot_result = str(action.get("shotResult", "")).lower()
        team = str(action.get("teamTricode", ""))
        sub_type = str(action.get("subType", "")).lower()

        # Update possession state
        if action_type in ("2pt", "3pt", "freethrow") and shot_result == "made":
            current_possession = "away" if team == home_team else "home"
            last_shooting_team = team
        elif action_type in ("2pt", "3pt", "freethrow") and shot_result == "missed":
            last_shooting_team = team
            current_possession = ""
        elif action_type == "rebound":
            current_possession = "home" if team == home_team else "away"
        elif action_type == "turnover":
            current_possession = "away" if team == home_team else "home"
        elif action_type == "period":
            current_possession = ""  # reset at period boundaries

        # Sample every 3rd event
        if i % 3 != 0:
            continue
        score_home = action.get("scoreHome")
        score_away = action.get("scoreAway")
        if score_home is None or score_away is None:
            continue
        try:
            score_diff = int(score_home) - int(score_away)
        except (ValueError, TypeError):
            continue
        minutes_remaining = _clock_to_minutes_remaining(action["period"], action["clock"])
        samples.append((score_diff, minutes_remaining, current_possession, home_won))

    return samples


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=150)
    args = parser.parse_args()

    print("Fetching schedule...")
    game_ids = _final_game_ids(args.games)
    print(f"Selected {len(game_ids)} completed games.")

    rows = []
    for i, game_id in enumerate(game_ids, 1):
        try:
            playbyplay = _fetch_json(PLAYBYPLAY_URL.format(game_id=game_id))
        except Exception as exc:
            print(f"  [{i}/{len(game_ids)}] {game_id}: skipped ({exc})")
            continue
        samples = _extract_samples(playbyplay)
        rows.extend(samples)
        print(f"  [{i}/{len(game_ids)}] {game_id}: {len(samples)} samples")
        time.sleep(0.1)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["score_diff", "minutes_remaining", "possession", "home_won"])
        writer.writerows(rows)

    with_possession = sum(1 for r in rows if r[2] != "")
    print(f"\nWrote {len(rows):,} samples from {len(game_ids)} games to {OUTPUT_PATH}")
    print(f"Possession known for {with_possession:,} samples ({100*with_possession/len(rows):.1f}%)")


if __name__ == "__main__":
    main()
