# nba-game-processor

A real-time NBA game state processor. It ingests play-by-play events (live or
replayed historical games), maintains running game state (score, pace,
momentum, on-court lineups) in Redis, and serves it through a FastAPI REST
API.

---

## Architecture

```
nba_api (live or historical)
      │
      ▼
  poller.py  ──────────────────────────────────────────►  Redis Stream
  (polls every 3s,                                        (persistent,
   detects new events,                                     observable,
   pushes as JSON)                                         decoupled)
                                                                │
                                                                ▼
                                                         processor.py
                                                         (consumer group,
                                                          updates GameState,
                                                          writes snapshot)
                                                                │
                                                                ▼
                                                          Redis Hash
                                                          (materialized
                                                           current state,
                                                           O(1) reads)
                                                                │
                                                                ▼
                                                           api.py
                                                           (FastAPI)
                                                                │
                                                                ▼
                                                            client
```

The poller and processor are separate processes connected only through
Redis. The poller's only job is pulling new events from `nba_api` and
appending them to a stream; the processor consumes that stream, updates a
`GameState` model, and writes the result to a Redis Hash. The API reads only
from that hash (CQRS-style: stream is the write path, hash is the read path),
so a query never has to replay history — it's always an O(1) `HGET`.

This split also gives crash recovery for free. If the processor dies, events
keep accumulating in the stream; on restart it drains its pending entries
list (PEL) and picks up where it left off. `scripts/demo_crash_recovery.py`
exercises this end to end.

A replay mode (`src/replay.py`) feeds a completed game's play-by-play through
the same stream the live poller writes to, at a configurable speed multiplier
— useful for development and for the demos below since it doesn't depend on
a game being in progress.

---

## Setup

### Prerequisites

- Docker and Docker Compose
- Python 3.11+

### Install

```bash
git clone https://github.com/vidithi-curry30/nba-game-processor
cd nba-game-processor
pip install -r requirements.txt
cp .env.example .env
```

### Start Redis

```bash
docker-compose up redis -d
```

---

## Running the Pipeline

### Live Mode (requires a game in progress)

Find today's game ID from the NBA scoreboard:

```python
from nba_api.live.nba.endpoints import scoreboard
sb = scoreboard.ScoreBoard()
for g in sb.get_dict()["scoreboard"]["games"]:
    print(g["gameId"], g["homeTeam"]["teamTricode"], "vs", g["awayTeam"]["teamTricode"])
```

Then start the poller and processor in separate terminals:

```bash
# Terminal 1: poll nba_api and push events to Redis Stream
python -m src.poller 0022301214

# Terminal 2: consume events and maintain game state
python -m src.processor 0022301214

# Terminal 3: serve the REST API
uvicorn src.api:app --reload
```

### Replay Mode (always available — no live game required)

List available completed games:

```bash
python -m src.replay --list-games
```

Replay at 20x speed (with the processor running in a separate terminal):

```bash
# Terminal 1: processor
python -m src.processor 0042300401

# Terminal 2: replay
python -m src.replay --game 0042300401 --speed 20
```

---

## API Reference

### `GET /game/{game_id}/state`

Returns the full current GameState.

```bash
curl http://localhost:8000/game/0042300401/state
```

```json
{
  "game_id": "0042300401",
  "home_team": "BOS",
  "away_team": "DAL",
  "home_score": 86,
  "away_score": 72,
  "period": 3,
  "clock": "4:22",
  "possession_count": 85,
  "pace": 142.9,
  "last_10_possessions": ["home_score", "away_score", "home_score", ...],
  "updated_at": "2024-06-06T21:00:00"
}
```

### `GET /game/{game_id}/win-probability`

P(home team wins), from a logistic regression trained on real play-by-play
(see "Win probability model" below). On a `final` game the result is
deterministic (1.0/0.0), not a model estimate.

```bash
curl http://localhost:8000/game/0042300401/win-probability
```

```json
{
  "game_id": "0042300401",
  "home_team": "BOS",
  "away_team": "DAL",
  "home_win_probability": 0.91,
  "away_win_probability": 0.09,
  "is_final": false
}
```

### `GET /game/{game_id}/momentum`

Last 10 possessions, a momentum score (positive = home team on a run), and a
z-score testing whether the split is significant against a 50/50 null
(`|z| > 1.5` flags a real run).

```bash
curl http://localhost:8000/game/0042300401/momentum
```

```json
{
  "game_id": "0042300401",
  "last_10_possessions": ["home_score", "home_score", "turnover", ...],
  "momentum_score": 3,
  "z_score": 1.0,
  "interpretation": "Contested"
}
```

### `GET /game/{game_id}/pace`

Current pace vs. league average (100.0 possessions per 48 min).

```bash
curl http://localhost:8000/game/0042300401/pace
```

```json
{
  "game_id": "0042300401",
  "pace": 108.4,
  "league_average": 100.0,
  "pace_differential": 8.4,
  "interpretation": "High-pace game — expect more total possessions"
}
```

### `GET /game/{game_id}/efficiency`

Offensive rating (points per 100 possessions) for both teams vs. the 2023-24
league average of ~113.

```bash
curl http://localhost:8000/game/0042300401/efficiency
```

```json
{
  "game_id": "0042300401",
  "home_team": "BOS",
  "away_team": "DAL",
  "home_offensive_rating": 122.6,
  "away_offensive_rating": 105.3,
  "home_ortg_vs_average": 9.6,
  "away_ortg_vs_average": -7.7,
  "interpretation": "BOS offense dominant"
}
```

### `GET /game/{game_id}/events`

Most recent raw events from the Redis Stream (the audit log). The only O(n)
endpoint; `limit` (default 50, max 500) bounds the cost.

```bash
curl "http://localhost:8000/game/0042300401/events?limit=10"
```

```json
{
  "game_id": "0042300401",
  "count": 2,
  "events": [
    {"stream_id": "1717704000000-0", "fields": {"event_type": "score", "home_score": "3", ...}},
    {"stream_id": "1717703990000-0", "fields": {"event_type": "period start", "period": "1", ...}}
  ]
}
```

### `GET /health`

```bash
curl http://localhost:8000/health
# {"status": "ok"}
```

---

## Win probability model

`src/win_probability.py` serves a `LogisticRegression` over three features:
score differential, minutes remaining, and the interaction term
`score_diff / sqrt(minutes_remaining + 1)` (a fixed lead matters more as time
runs out).

The model is trained on real play-by-play from ~150 completed games, fetched
from `cdn.nba.com`'s live-data endpoints by `scripts/fetch_training_games.py`
(`stats.nba.com`, which `nba_api`'s historical endpoints normally use, is not
reachable from this environment, but the live-data CDN is and serves
play-by-play for completed games too). Each game contributes a snapshot every
few events — `(score_diff, minutes_remaining)` at that point in the game,
labeled with the actual final outcome — giving about 29,000 training
examples. The cached dataset is checked into `data/wp_training_data.csv` so
`scripts/train_win_probability.py` can be re-run without network access; pass
`--games N` to `fetch_training_games.py` to pull a larger or more recent
sample.

```bash
python -m scripts.fetch_training_games --games 150  # optional, refreshes the CSV
python -m scripts.train_win_probability
```

A 3-feature logistic regression is the right starting point here: the
relationship between (score differential, time remaining) and win probability
is close to linear in the logit, the model is small enough for sub-millisecond
inference, and the fitted coefficients are directly interpretable (e.g. the
`score_diff_over_sqrt_time` coefficient says how much a fixed lead matters as
the clock runs down).

---

## Demos

### Crash recovery

`scripts/demo_crash_recovery.py` seeds a game's worth of synthetic events,
starts the processor, `SIGKILL`s it mid-stream, and shows that Redis's
consumer-group pending entries list (PEL) still holds the in-flight,
unacknowledged messages. Restarting the processor drains the PEL (via
`XREADGROUP ... id="0"`) before resuming new messages with `">"`, and the
final state is verified to be exactly correct.

```bash
python -m scripts.demo_crash_recovery
```

```
--- XPENDING (after crash) ---
{'pending': 6, 'min': '...-0', 'max': '...-1', 'consumers': [{'name': 'processor-1', 'pending': 6}]}
...
Final score 8-6 matches expected 8-6: every event was applied exactly once,
despite the mid-stream crash.
```

### Multi-game scaling

`scripts/demo_multi_game.py` seeds two different games and starts two
independent `python -m src.processor <game_id>` subprocesses concurrently —
one per game, sharing nothing (separate stream keys, consumer groups, and
state hashes). Both reach correct final state independently.

```bash
python -m scripts.demo_multi_game
```

```
demo_multi_a: BOS 6 - 2 DAL (status=final)
demo_multi_b: LAL 3 - 5 GSW (status=final)
```

---

## Running Tests

```bash
pytest tests/ -v
```

No Redis or `nba_api` connection required — all external dependencies are
mocked.

---

## Benchmarks

Measured with `scripts/benchmark_api.py` against `uvicorn src.api:app` and
Redis both running locally (1000 requests, concurrency 10, against
`/game/{id}/state` for a populated game):

```bash
python -m scripts.demo_multi_game   # populate a game's state
uvicorn src.api:app --port 8001 &
python -m scripts.benchmark_api --host http://localhost:8001 --game demo_multi_a \
    --requests 1000 --concurrency 10
```

| Metric | `/state` | `/win-probability` |
|--------|----------|---------------------|
| p50 latency | 10.6ms | 1.9ms |
| p99 latency | 140.7ms | 2.5ms |
| Mean latency | 18.8ms | 1.9ms |
| Throughput | ~498 req/s | — |

These are from a single uvicorn worker without `--reload`. The `/state` p99
tail is consistent with a single worker serializing requests under
concurrency. `uvicorn --workers N` adds parallel processes for the I/O-bound
request path; the Redis `HGET` stays O(1) regardless of game length.

---

## Deployment (Fly.io)

```bash
fly launch --no-deploy
fly secrets set REDIS_URL=redis://your-redis-url:6379
fly deploy
```

The `/health` endpoint is configured as the Fly.io health check path in
`fly.toml`. The app auto-stops when idle and auto-starts on the first request
(`auto_stop_machines = true`).
