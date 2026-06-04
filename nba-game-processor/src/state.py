"""
Game state model and event processing logic.

This module is the single source of truth for what NBA game state looks like
and how raw play-by-play events mutate it. Keeping state transitions here
(not in processor.py) means the logic can be unit tested without Redis.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class GameState(BaseModel):
    """
    Materialized view of current NBA game state derived from play-by-play events.

    WHY: Pydantic BaseModel gives us free JSON serialization/deserialization,
    field validation, and a clear schema contract between the processor and API.
    Every field is typed so bugs surface at model construction, not at query time.
    """

    # WHY game_id as string: nba_api returns game IDs like "0022300512" — leading
    # zeros would be lost if stored as int, breaking all downstream lookups.
    game_id: str

    home_team: str = ""   # team abbreviation, e.g. "BOS"
    away_team: str = ""   # team abbreviation, e.g. "MIA"

    home_score: int = 0   # current home team score
    away_score: int = 0   # current away team score

    # WHY period as int: 1-4 for regulation, 5+ for overtime periods.
    # Keeping it as an int lets the API consumer display "OT", "2OT" etc.
    period: int = 1

    clock: str = "12:00"  # time remaining in period, e.g. "4:32"

    # WHY track possession_count separately from scoring plays: pace requires
    # total possessions including non-scoring ones (turnovers, defensive stops).
    possession_count: int = 0

    # WHY player lists as str IDs not names: nba_api substitution events give
    # player IDs; resolving names on every event would require extra API calls.
    home_players_on_court: list[str] = Field(default_factory=list)
    away_players_on_court: list[str] = Field(default_factory=list)

    # WHY cap at 10: long enough for a meaningful momentum signal (3+ possessions
    # is noise; 10 is a mini-quarter's worth), short enough to reflect current
    # game state rather than first-half history.
    last_10_possessions: list[str] = Field(default_factory=list)

    # WHY pace as float: possessions per 48 minutes. League average is ~100.
    # Pace contextualizes score — a 120-110 game at pace 115 is a blowout;
    # at pace 85 it's a close game with very different defensive implications.
    pace: float = 0.0

    # WHY store minutes_elapsed separately: pace formula divides by time elapsed,
    # and we parse the clock string to update this on every event.
    minutes_elapsed: float = 0.0

    # WHY store game_status: processor uses this to stop consuming once "Final".
    game_status: str = "in_progress"

    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def _parse_clock(self, clock_str: str, period: int) -> float:
        """
        Convert period + clock string to total minutes elapsed in game.

        WHY: pace = (possessions / minutes_elapsed) * 48. We need a monotonically
        increasing time value; period boundary resets make raw clock insufficient.
        """
        try:
            parts = clock_str.split(":")
            minutes_left = float(parts[0])
            seconds_left = float(parts[1]) if len(parts) > 1 else 0.0
        except (ValueError, IndexError):
            minutes_left, seconds_left = 12.0, 0.0

        # WHY 12 per regulation period, 5 per OT period:
        # standard NBA period lengths; OT is 5 minutes.
        if period <= 4:
            minutes_per_period = 12.0
            elapsed_in_period = minutes_per_period - minutes_left - (seconds_left / 60.0)
            return (period - 1) * 12.0 + elapsed_in_period
        else:
            ot_number = period - 4
            elapsed_in_period = 5.0 - minutes_left - (seconds_left / 60.0)
            return 48.0 + (ot_number - 1) * 5.0 + elapsed_in_period

    def _update_pace(self) -> None:
        """
        Recalculate pace after each possession event.

        WHY formula (possession_count / minutes_elapsed) * 48: this annualizes
        the current possession rate to a full-game (48 min) basis so it's
        comparable to the league-average stat of ~100 possessions per 48 min.
        """
        if self.minutes_elapsed > 0:
            self.pace = (self.possession_count / self.minutes_elapsed) * 48.0

    def _record_possession(self, outcome: str) -> None:
        """
        Append outcome to last_10_possessions, enforcing the 10-entry cap.

        WHY enforce max 10 here rather than in the API: keeping the list bounded
        in the model means the Redis Hash payload stays small regardless of game
        length, and the momentum endpoint never needs to slice.
        """
        self.possession_count += 1
        self.last_10_possessions.append(outcome)
        if len(self.last_10_possessions) > 10:
            self.last_10_possessions.pop(0)

    def _handle_score_change(self, event: dict) -> None:
        """
        Update score and record possession outcome on a scoring play.

        WHY: scoring plays are the most common event type and must update both
        the displayed score and the possession history used for momentum.
        """
        home_score = event.get("home_score")
        away_score = event.get("away_score")

        if home_score is not None:
            try:
                new_home = int(home_score)
                new_away = int(away_score) if away_score is not None else self.away_score

                if new_home > self.home_score:
                    # WHY check delta > 0: nba_api sometimes resends the same score
                    # for non-scoring events; only record a possession when score
                    # actually changes.
                    self.home_score = new_home
                    self.away_score = new_away
                    self._record_possession("home_score")
                elif new_away > self.away_score:
                    self.home_score = new_home
                    self.away_score = new_away
                    self._record_possession("away_score")
            except (ValueError, TypeError):
                pass

    def _handle_substitution(self, event: dict) -> None:
        """
        Swap one player for another in the on-court roster.

        WHY: tracking on-court players enables lineup-based analytics downstream
        (e.g., net rating by lineup). We store player IDs, not names, to avoid
        a secondary lookup on every substitution event.
        """
        team = event.get("team", "")
        player_in = str(event.get("player_in", ""))
        player_out = str(event.get("player_out", ""))

        if not player_in or not player_out:
            return

        # WHY determine home vs away by team abbreviation stored at game start:
        # nba_api substitution events include the team tricode on every event.
        if team == self.home_team:
            roster = self.home_players_on_court
        elif team == self.away_team:
            roster = self.away_players_on_court
        else:
            return

        if player_out in roster:
            roster.remove(player_out)
        roster.append(player_in)

    def _handle_period_change(self, event: dict) -> None:
        """
        Advance period counter and reset clock to period start.

        WHY: period changes are explicit events in nba_api; incrementing here
        rather than inferring from clock=0:00 is more reliable because the
        API sometimes omits clock values on period-end events.
        """
        new_period = event.get("period")
        if new_period is not None:
            try:
                self.period = int(new_period)
            except (ValueError, TypeError):
                pass
        # WHY reset clock to standard period length: this is the canonical
        # starting state for the new period; individual events will update it.
        self.clock = "5:00" if self.period > 4 else "12:00"

    def _handle_turnover(self, event: dict) -> None:
        """
        Record a turnover possession without changing the score.

        WHY: turnovers count as possessions for pace calculation even though
        no points are scored. Omitting them would overestimate pace in
        turnover-heavy games.
        """
        self._record_possession("turnover")

    def _handle_end_of_game(self) -> None:
        """
        Mark game as final so the processor stops consuming events.

        WHY: the poller already stops when it detects game_status == "Final",
        but the processor also needs to know so it can log completion and
        allow graceful shutdown rather than waiting for new events indefinitely.
        """
        self.game_status = "final"

    def update(self, event: dict) -> None:
        """
        Mutate state based on a single raw play-by-play event dict.

        WHY this method lives on the model rather than in processor.py: the
        state transition logic can then be unit tested with plain dicts —
        no Redis, no async, no network. The processor is just plumbing.
        """
        event_type = event.get("event_type", "").lower()

        # Update clock and minutes elapsed on every event so pace stays current.
        raw_clock = event.get("clock", self.clock)
        raw_period = event.get("period", self.period)
        try:
            period_int = int(raw_period)
        except (ValueError, TypeError):
            period_int = self.period
        self.period = period_int
        self.clock = str(raw_clock)
        self.minutes_elapsed = self._parse_clock(self.clock, self.period)

        # Update team names if this is the first event that carries them.
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
        elif event_type in ("end of game", "final"):
            self._handle_end_of_game()
        else:
            # WHY still record "other" possessions: any unrecognized event that
            # accompanies a possession (e.g., offensive foul) should still
            # contribute to the possession stream rather than silently drop.
            if event.get("is_possession_end"):
                self._record_possession("other")

        self._update_pace()
        self.updated_at = datetime.utcnow()
