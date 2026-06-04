"""
Game state model and event processing logic.

This module is the single source of truth for what NBA game state looks like
and how raw play-by-play events mutate it. Keeping state transitions here
(not in processor.py) means the logic can be unit tested without Redis.
"""

from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class GameState(BaseModel):
    """
    Materialized view of current NBA game state derived from play-by-play events.

    WHY: Pydantic BaseModel gives us free JSON serialization/deserialization,
    field validation, and a clear schema contract between the processor and API.
    Every field is typed so bugs surface at model construction, not at query time.
    """

    # WHY model_config frozen=False: Pydantic v2 models are immutable by default.
    # We explicitly opt into mutability here because GameState is updated in-place
    # by the processor on every event — creating a new instance per event would
    # add unnecessary allocation overhead and complicate the processor loop.
    model_config = ConfigDict(frozen=False)

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

    # WHY track home and away possessions separately rather than a single total:
    # NBA pace is defined per team (possessions per 48 min per team), not combined.
    # A team that runs 60 possessions while holding the opponent to 40 should
    # not have the same pace as one that played 50 possessions on each side.
    # Tracking separately also enables per-team offensive rating calculations.
    home_possessions: int = 0
    away_possessions: int = 0

    # WHY cap at 10: long enough for a meaningful momentum signal (3+ possessions
    # is noise; 10 is a mini-quarter's worth), short enough to reflect current
    # game state rather than first-half history.
    last_10_possessions: list[str] = Field(default_factory=list)

    # WHY pace as float: possessions per 48 minutes per team. League average is
    # ~100. Pace contextualizes score — a 120-110 game at pace 115 is a blowout;
    # at pace 85 it's a grinding defensive battle. Pace is one of the four factors
    # in NBA efficiency analysis (Dean Oliver, "Basketball on Paper", 2004).
    pace: float = 0.0

    # WHY store minutes_elapsed separately: pace formula divides by time elapsed,
    # and we parse the clock string to update this on every event.
    minutes_elapsed: float = 0.0

    # WHY store game_status: processor uses this to stop consuming once "Final".
    game_status: str = "in_progress"

    # WHY player lists as str IDs not names: nba_api substitution events give
    # player names; we store exactly what the event provides. Name-based lookup
    # is sufficient for roster tracking — player ID resolution is a separate
    # concern for a future player-stats endpoint.
    home_players_on_court: list[str] = Field(default_factory=list)
    away_players_on_court: list[str] = Field(default_factory=list)

    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # -------------------------------------------------------------------------
    # Derived property helpers — not stored, computed on demand by the API
    # -------------------------------------------------------------------------

    @property
    def possession_count(self) -> int:
        """Total possessions across both teams — used for backward compatibility."""
        return self.home_possessions + self.away_possessions

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
            elapsed_in_period = 12.0 - minutes_left - (seconds_left / 60.0)
            return (period - 1) * 12.0 + elapsed_in_period
        else:
            ot_number = period - 4
            elapsed_in_period = 5.0 - minutes_left - (seconds_left / 60.0)
            return 48.0 + (ot_number - 1) * 5.0 + elapsed_in_period

    def _update_pace(self) -> None:
        """
        Recalculate pace after each possession event.

        WHY formula ((home + away) / 2 / minutes_elapsed) * 48: NBA pace is
        defined as possessions per team per 48 minutes. Dividing the combined
        count by 2 gives the per-team average, which is comparable to the
        league-average stat of ~100 possessions per team per game.
        Ref: Basketball Reference pace formula.
        """
        if self.minutes_elapsed > 0:
            avg_team_possessions = (self.home_possessions + self.away_possessions) / 2.0
            self.pace = (avg_team_possessions / self.minutes_elapsed) * 48.0

    def _record_possession(self, team: str, outcome: str) -> None:
        """
        Increment the correct team's possession counter and append to the window.

        WHY track which team had the possession: offensive rating is points per
        100 *that team's* possessions — a number only meaningful if you know
        which team controlled the ball, not just that a scoring play occurred.
        """
        if team == "home":
            self.home_possessions += 1
        elif team == "away":
            self.away_possessions += 1

        self.last_10_possessions.append(outcome)
        # WHY pop from front not back: the list is ordered oldest-first;
        # we want the 10 *most recent* entries, so we drop the oldest.
        if len(self.last_10_possessions) > 10:
            self.last_10_possessions.pop(0)

    def _handle_score_change(self, event: dict) -> None:
        """
        Update score and record possession outcome on a scoring play.

        WHY check delta > 0 before recording: nba_api sometimes carries the
        current running score on non-scoring events (e.g., fouls). We only
        record a possession when the score actually changes, preventing
        duplicate possession entries that would corrupt the pace calculation.
        """
        home_score = event.get("home_score")
        away_score = event.get("away_score")

        if home_score is None:
            return
        try:
            new_home = int(home_score)
            new_away = int(away_score) if away_score is not None else self.away_score
        except (ValueError, TypeError):
            return

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
        Update the on-court roster for one substitution action.

        WHY handle two event patterns: the live API (poller.py) emits one event
        per player with sub_type="in" or "out". The historical API (replay.py)
        emits one combined event with player_in and player_out both populated.
        Supporting both lets replay feed through the identical pipeline without
        schema conversion.
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
            # Live API pattern: single-player "in" event.
            roster.append(player)
        elif sub_type == "out" and player:
            # Live API pattern: single-player "out" event.
            if player in roster:
                roster.remove(player)
        elif player_in and player_out and player_in != player_out:
            # Historical API pattern: combined event with both players named.
            # WHY check player_in != player_out: nba_api historical data
            # occasionally has malformed rows where both fields are the same name.
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
        self.clock = "5:00" if self.period > 4 else "12:00"

    def _handle_turnover(self, event: dict) -> None:
        """
        Record a turnover possession without changing the score.

        WHY: turnovers count as possessions for pace calculation even though
        no points are scored. Omitting them would overestimate pace in
        turnover-heavy games (a team that turns it over 20 times looks faster
        than it is if you only count scoring possessions).
        """
        team = event.get("team", "")
        possessing_team = "home" if team == self.home_team else "away"
        self._record_possession(possessing_team, "turnover")

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

        # Update team names on the first event that carries them.
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
            if event.get("is_possession_end"):
                team = event.get("team", "")
                possessing_team = "home" if team == self.home_team else "away"
                self._record_possession(possessing_team, "other")

        self._update_pace()
        self.updated_at = datetime.utcnow()
