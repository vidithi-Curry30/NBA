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

NBA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}
PLAYBYPLAY_URL = "https://cdn.nba.com/static/json/liveData/playbyplay/playbyplay_{game_id}.json"

# 2023-24 regular season + playoff game IDs spanning the full season.
# Using a hardcoded list avoids the schedule endpoint which blocks non-browser clients.
# IDs starting with 0022 = regular season, 0042 = playoffs.
KNOWN_GAME_IDS = [
    "0022300001","0022300002","0022300003","0022300004","0022300005",
    "0022300050","0022300100","0022300150","0022300200","0022300250",
    "0022300300","0022300350","0022300400","0022300450","0022300500",
    "0022300550","0022300600","0022300650","0022300700","0022300750",
    "0022300800","0022300850","0022300900","0022300950","0022301000",
    "0022301050","0022301100","0022301150","0022301200","0022301214",
    "0022301215","0022301216","0022301217","0022301218","0022301219",
    "0022301220","0022301221","0022301222","0022301223","0022301224",
    "0022300010","0022300020","0022300030","0022300040","0022300060",
    "0022300070","0022300080","0022300090","0022300110","0022300120",
    "0022300130","0022300140","0022300160","0022300170","0022300180",
    "0022300190","0022300210","0022300220","0022300230","0022300240",
    "0022300260","0022300270","0022300280","0022300290","0022300310",
    "0022300320","0022300330","0022300340","0022300360","0022300370",
    "0022300380","0022300390","0022300410","0022300420","0022300430",
    "0022300440","0022300460","0022300470","0022300480","0022300490",
    "0022300510","0022300520","0022300530","0022300540","0022300560",
    "0022300570","0022300580","0022300590","0022300610","0022300620",
    "0022300630","0022300640","0022300660","0022300670","0022300680",
    "0022300690","0022300710","0022300720","0022300730","0022300740",
    "0022300760","0022300770","0022300780","0022300790","0022300810",
    "0022300820","0022300830","0022300840","0022300860","0022300870",
    "0022300880","0022300890","0022300910","0022300920","0022300930",
    "0022300940","0022300960","0022300970","0022300980","0022300990",
    "0022301010","0022301020","0022301030","0022301040","0022301060",
    "0022301070","0022301080","0022301090","0022301110","0022301120",
    "0022301130","0022301140","0022301160","0022301170","0022301180",
    "0022301190","0022301210","0022301211","0022301212","0022301213",
    "0042300101","0042300102","0042300103","0042300104",
    "0042300201","0042300202","0042300203","0042300204",
    "0042300301","0042300302","0042300303","0042300304",
    "0042300401","0042300402","0042300403","0042300404",
    "0042300501","0042300502","0042300503","0042300504",
]

GAME_MINUTES = 48.0
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "wp_training_data.csv"


def _fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=NBA_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _select_game_ids(max_games: int) -> list[str]:
    if len(KNOWN_GAME_IDS) <= max_games:
        return list(KNOWN_GAME_IDS)
    step = len(KNOWN_GAME_IDS) / max_games
    return [KNOWN_GAME_IDS[int(i * step)] for i in range(max_games)]


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

    game_ids = _select_game_ids(args.games)
    print(f"Selected {len(game_ids)} games from hardcoded 2023-24 season list.")

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
