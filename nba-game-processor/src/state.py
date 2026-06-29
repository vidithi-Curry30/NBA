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

    # Possession tracking. Inferred from the event sequence:
    # made basket → other team; rebound → rebounding team;
    # turnover → other team; free throw (last) → other team.
    # Empty string means unknown (e.g. tip-off, period start).
    current_possession: str = ""
    # Needed to determine offensive vs defensive rebound: a rebound by the
    # same team that just missed is offensive (they keep the ball).
    last_shooting_team: str = ""

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
        if self.minutes_elapsed >= 2.0:  # need at least 2 min for a meaningful estimate
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
            self.current_possession = "away"  # made basket → other team inbounds
            self.last_shooting_team = self.home_team
        elif new_away > self.away_score:
            self.home_score = new_home
            self.away_score = new_away
            self._record_possession("away", "away_score")
            self.current_possession = "home"
            self.last_shooting_team = self.away_team

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

    def _handle_rebound(self, event: dict) -> None:
        team = str(event.get("team", ""))
        sub_type = str(event.get("sub_type", "")).lower()

        if not team:
            return

        rebounding_side = "home" if team == self.home_team else "away"

        # Determine offensive vs defensive so we can count possessions correctly.
        # An offensive rebound extends the current possession (no new possession).
        # A defensive rebound ends the shooting team's possession and starts a new one.
        if sub_type == "offensive":
            is_offensive = True
        elif sub_type == "defensive":
            is_offensive = False
        else:
            # No sub_type: infer from last_shooting_team. Same team rebounding
            # their own miss = offensive; different team = defensive.
            is_offensive = (team == self.last_shooting_team)

        if is_offensive:
            # Same possession continues — just confirm possession stays with rebounder.
            self.current_possession = rebounding_side
        else:
            # Defensive rebound: the shooting team's possession just ended.
            shooting_side = "home" if self.last_shooting_team == self.home_team else "away"
            if self.last_shooting_team:
                self._record_possession(shooting_side, "missed_shot")
            self.current_possession = rebounding_side

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

    def _handle_missed_shot(self, event: dict) -> None:
        team = str(event.get("team", ""))
        if team:
            self.last_shooting_team = team

    def _handle_turnover(self, event: dict) -> None:
        # Turnovers count as possessions for pace even though no points scored.
        team = event.get("team", "")
        possessing_team = "home" if team == self.home_team else "away"
        self._record_possession(possessing_team, "turnover")
        # Turnover → other team gets the ball.
        self.current_possession = "away" if possessing_team == "home" else "home"

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

        # Only advance time — never let out-of-order or supplemental events
        # (e.g. a "period start Q1" appended after play events) move the
        # clock backwards. Game time is monotonically non-decreasing.
        new_elapsed = self._parse_clock(str(raw_clock), period_int)
        if new_elapsed >= self.minutes_elapsed:
            self.period = period_int
            self.clock = str(raw_clock)
            self.minutes_elapsed = new_elapsed

        if event.get("home_team") and not self.home_team:
            self.home_team = str(event["home_team"])
        if event.get("away_team") and not self.away_team:
            self.away_team = str(event["away_team"])

        if event_type in ("score", "made shot", "free throw", "score change"):
            self._handle_score_change(event)
        elif event_type == "missed shot":
            self._handle_missed_shot(event)
        elif event_type == "rebound":
            self._handle_rebound(event)
        elif event_type == "substitution":
            self._handle_substitution(event)
        elif event_type == "period start":
            self._handle_period_change(event)
            self.current_possession = ""  # possession resets at period boundary
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
