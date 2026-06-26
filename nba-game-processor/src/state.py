"""
Game state model and event processing logic.

Keeping the state transitions here (not in processor.py) means they can be
unit tested with plain dicts, no Redis or async required.
"""

from collections import deque
from datetime import datetime
from typing import Deque
from pydantic import BaseModel, ConfigDict, Field


class GameState(BaseModel):
    """Materialized view of current game state, derived from play-by-play events."""

    # Pydantic v2 models are immutable by default; the processor mutates
    # this in place on every event, so we opt back into mutability.
    model_config = ConfigDict(frozen=False)

    game_id: str

    home_team: str = ""
    away_team: str = ""

    home_score: int = 0
    away_score: int = 0

    period: int = 1
    clock: str = "12:00"

    # Tracked per team because pace and offensive rating are both per-team
    # stats (possessions per 48 min per team, points per 100 of that team's
    # possessions).
    home_possessions: int = 0
    away_possessions: int = 0

    # deque(maxlen=10) evicts the oldest entry automatically, giving an O(1)
    # rolling window for the momentum endpoint.
    last_10_possessions: Deque[str] = Field(default_factory=lambda: deque(maxlen=10))

    pace: float = 0.0
    minutes_elapsed: float = 0.0

    game_status: str = "in_progress"

    home_players_on_court: list[str] = Field(default_factory=list)
    away_players_on_court: list[str] = Field(default_factory=list)

    # Foul counts per player name. Players with 4+ fouls in the first three
    # quarters are in "foul trouble" — they play reduced minutes and teams
    # often pull them to avoid fouling out. This materially affects win
    # probability in ways the score-diff model can't see.
    player_fouls: dict[str, int] = Field(default_factory=dict)

    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def possession_count(self) -> int:
        return self.home_possessions + self.away_possessions

    def _parse_clock(self, clock_str: str, period: int) -> float:
        """Convert period + clock string ("4:32") to total minutes elapsed in game."""
        try:
            parts = clock_str.split(":")
            minutes_left = float(parts[0])
            seconds_left = float(parts[1]) if len(parts) > 1 else 0.0
        except (ValueError, IndexError):
            minutes_left, seconds_left = 12.0, 0.0

        if period <= 4:
            elapsed_in_period = 12.0 - minutes_left - (seconds_left / 60.0)
            return (period - 1) * 12.0 + elapsed_in_period
        else:
            ot_number = period - 4
            elapsed_in_period = 5.0 - minutes_left - (seconds_left / 60.0)
            return 48.0 + (ot_number - 1) * 5.0 + elapsed_in_period

    def _update_pace(self) -> None:
        """Pace = possessions per team per 48 minutes (Basketball Reference formula)."""
        if self.minutes_elapsed > 0:
            avg_team_possessions = (self.home_possessions + self.away_possessions) / 2.0
            self.pace = (avg_team_possessions / self.minutes_elapsed) * 48.0

    def _record_possession(self, team: str, outcome: str) -> None:
        if team == "home":
            self.home_possessions += 1
        elif team == "away":
            self.away_possessions += 1
        self.last_10_possessions.append(outcome)

    def _handle_score_change(self, event: dict) -> None:
        home_score = event.get("home_score")
        away_score = event.get("away_score")

        if home_score is None:
            return
        try:
            new_home = int(home_score)
            new_away = int(away_score) if away_score is not None else self.away_score
        except (ValueError, TypeError):
            return

        # nba_api sometimes carries the running score on non-scoring events
        # (e.g. fouls); only record a possession when the score actually moves.
        if new_home > self.home_score:
            self.home_score = new_home
            self.away_score = new_away
            self._record_possession("home", "home_score")
        elif new_away > self.away_score:
            self.home_score = new_home
            self.away_score = new_away
            self._record_possession("away", "away_score")

    def _handle_substitution(self, event: dict) -> None:
        """
        Handles two event shapes: the live poller emits one event per player
        with sub_type "in"/"out"; replay emits a single combined event with
        player_in and player_out both set.
        """
        team = event.get("team", "")
        if team == self.home_team:
            roster = self.home_players_on_court
        elif team == self.away_team:
            roster = self.away_players_on_court
        else:
            return

        sub_type = str(event.get("sub_type", "")).lower()
        player = str(event.get("player", ""))
        player_in = str(event.get("player_in", ""))
        player_out = str(event.get("player_out", ""))

        if sub_type == "in" and player:
            roster.append(player)
        elif sub_type == "out" and player:
            if player in roster:
                roster.remove(player)
        elif player_in and player_out and player_in != player_out:
            if player_out in roster:
                roster.remove(player_out)
            roster.append(player_in)

    def _handle_period_change(self, event: dict) -> None:
        new_period = event.get("period")
        if new_period is not None:
            try:
                self.period = int(new_period)
            except (ValueError, TypeError):
                pass
        self.clock = "5:00" if self.period > 4 else "12:00"

    def _handle_foul(self, event: dict) -> None:
        player = str(event.get("player", "")).strip()
        if not player:
            return
        self.player_fouls[player] = self.player_fouls.get(player, 0) + 1

    def foul_trouble_players(self) -> dict[str, int]:
        """
        Returns players currently in foul trouble.

        The NBA threshold: 4+ fouls before the 4th quarter, or 5+ fouls at
        any point (6 fouls = fouled out). This is the thing TV analysts talk
        about that the score alone doesn't capture.
        """
        trouble: dict[str, int] = {}
        for player, fouls in self.player_fouls.items():
            if fouls >= 6:
                trouble[player] = fouls  # fouled out
            elif fouls >= 4 and self.period < 4:
                trouble[player] = fouls  # in trouble before 4th
            elif fouls >= 5 and self.period == 4:
                trouble[player] = fouls  # one foul from out in 4th
        return trouble

    def _handle_turnover(self, event: dict) -> None:
        # Turnovers count as possessions for pace even though no points
        # are scored.
        team = event.get("team", "")
        possessing_team = "home" if team == self.home_team else "away"
        self._record_possession(possessing_team, "turnover")

    def _handle_end_of_game(self) -> None:
        self.game_status = "final"

    def update(self, event: dict) -> None:
        """Mutate state based on a single raw play-by-play event dict."""
        event_type = event.get("event_type", "").lower()

        raw_clock = event.get("clock", self.clock)
        raw_period = event.get("period", self.period)
        try:
            period_int = int(raw_period)
        except (ValueError, TypeError):
            period_int = self.period
        self.period = period_int
        self.clock = str(raw_clock)
        self.minutes_elapsed = self._parse_clock(self.clock, self.period)

        if event.get("home_team") and not self.home_team:
            self.home_team = str(event["home_team"])
        if event.get("away_team") and not self.away_team:
            self.away_team = str(event["away_team"])

        if event_type in ("score", "made shot", "free throw", "score change"):
            self._handle_score_change(event)
        elif event_type == "substitution":
            self._handle_substitution(event)
        elif event_type == "period start":
            self._handle_period_change(event)
        elif event_type == "turnover":
            self._handle_turnover(event)
        elif event_type in ("foul", "personal foul", "technical foul"):
            self._handle_foul(event)
        elif event_type in ("end of game", "final"):
            self._handle_end_of_game()
        else:
            if event.get("is_possession_end"):
                team = event.get("team", "")
                possessing_team = "home" if team == self.home_team else "away"
                self._record_possession(possessing_team, "other")

        self._update_pace()
        self.updated_at = datetime.utcnow()
