# nba-game-processor

A real-time NBA game state processor that ingests live play-by-play events from `nba_api`, maintains running game state (score, pace, momentum, on-court lineups), and serves it through a REST API with sub-10ms query latency. The system uses Redis Streams as a persistent, observable message queue between a poller process and a processor process — a deliberate architectural choice that provides crash recovery, component decoupling, and independent observability. The API reads exclusively from a Redis Hash materialized view maintained by the processor, implementing the CQRS pattern (Command Query Responsibility Segregation) to keep read latency O(1) regardless of how many events have been processed. A replay mode feeds historical play-by-play through the identical live pipeline, making it simultaneously a demo tool and an integration test.

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
                                                           (FastAPI,
                                                            sub-10ms
                                                            responses)
                                                                │
                                                                ▼
                                                            client
```

---

## Design Decisions

### Why Redis Streams instead of an in-process queue

Redis Streams provide three properties that `asyncio.Queue` cannot: **persistence** (events survive a processor crash and are replayed on restart via consumer group offsets), **observability** (the stream can be inspected with `XRANGE` independently of both the poller and processor — you can see exactly what's in the queue without touching either component), and **decoupling** (the poller and processor share zero code and communicate only through Redis, so either can be restarted, scaled, or replaced without affecting the other). An `asyncio.Queue` would be simpler but would couple both components in the same process, losing all three of these properties the moment the process restarts.

### Why separate poller and processor processes

Fault isolation: if `nba_api` is slow or throws rate-limit errors, the poller backs off and retries without affecting the processor — events already in the stream continue to be consumed normally. Conversely, if the processor has a bug and crashes, events keep accumulating in the stream without being dropped, and the processor picks up exactly where it left off when it restarts. Each component has a single, clearly bounded responsibility, which maps directly to the Single Responsibility Principle that appears in every Big Tech system design interview.

### Why the API reads from Redis Hash and not the Stream

The stream is the authoritative event log, but it's append-only and sequential — computing current state from it requires replaying every event from the beginning, which is O(n) in the number of events. After processing each event, the processor writes a complete JSON snapshot of current state to a Redis Hash; the API reads from this hash in O(1) regardless of whether 10 or 10,000 events have occurred. This is the **CQRS pattern**: the stream is the write path, the hash is the read path. Separating them means the read path scales independently — you could add read replicas, caching, or CDN fronting without touching the event processing logic.

### Why a logistic regression for win probability, not XGBoost/a neural net

`src/win_probability.py` serves a `LogisticRegression` trained on simulated
possession-by-possession trajectories (`scripts/train_win_probability.py`).
With three features — score differential, minutes remaining, and their
interaction term `score_diff / sqrt(minutes_remaining + 1)` — the relationship
to P(home win) is close to linear-in-the-logit by construction (it's literally
how the simulator's win labels are generated). A gradient-boosted tree or
neural net would add training time, inference latency, and a model file an
order of magnitude larger, for an accuracy gain that's within noise on a
3-feature problem. Logistic regression is also the *interpretable* choice:
the fitted coefficients can be read directly off `joblib.load(...)['model'].coef_`
and explained in one sentence each — the `score_diff_over_sqrt_time`
coefficient (+0.85) directly encodes "how much a fixed lead matters as time
runs out." Start simple; only reach for a more complex model if accuracy,
log loss, or Brier score on held-out data demonstrate simple is insufficient.

### Why simulated training data instead of real nba_api play-by-play

`stats.nba.com` (the source of historical play-by-play via `nba_api`) is
rate-limited and unreachable from this development environment — verified
with a direct request that returned a 503 and a `PlayByPlayV2` call that timed
out after 10s. Rather than block the model on external network access, the
training script Monte Carlo-simulates ~3,000 games possession-by-possession,
calibrated so simulated team shooting percentages produce a league-average
offensive rating of ~113 (matching `LEAGUE_AVERAGE_OFFENSIVE_RATING` in
`src/metrics.py`) and a league-average pace of ~100 (matching
`LEAGUE_AVERAGE_PACE`). The feature schema and training code are identical
either way — `scripts/train_win_probability.py` documents exactly how to
re-point `generate_training_data` at real historical games once network access
is available, with zero changes to `src/win_probability.py` or the API.

### Why Python and not C++

Every component in this system is I/O-bound: polling an HTTP API, reading and writing to Redis over a network socket, serving HTTP responses. C++ only provides meaningful performance benefit on CPU-bound hot paths. The [Monte Carlo Equity Simulator](https://github.com/vidithi-curry30) on this GitHub already demonstrates CPU-bound C++ systems programming with lock-free SPSC queues, xoshiro256++ RNG, and a custom thread pool. Adding C++ here would be forced — all the latency is in network I/O, not computation — and the decision wouldn't be defensible under interview questioning. Knowing *when not to use a tool* is as important as knowing when to use it.

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

Replay at 20x speed (with processor running in a separate terminal):

```bash
# Terminal 1: processor
python -m src.processor 0042300401

# Terminal 2: replay
python -m src.replay --game 0042300401 --speed 20
```

---

## API Reference

### `GET /game/{game_id}/state`

Returns full current GameState.

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

Returns P(home team wins), from a logistic regression trained on simulated
game trajectories (see "Why a logistic regression" above). On a `final` game,
the result is deterministic (1.0/0.0), not a model estimate.

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

Returns last 10 possessions, a momentum score (positive = home team on a run),
and a z-score that tests whether the split is statistically significant
against the null hypothesis that scoring possessions are a 50/50 coin flip
(`|z| > 1.5` flags a real run; see "Why a z-score" in Design Decisions).

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

Returns current pace vs. league average (100.0 possessions per 48 min).

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

Returns offensive rating (points per 100 possessions) for both teams vs. the
2023-24 league average of ~113.

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

Returns the most recent raw events from the Redis Stream — the append-only
audit log. This is the one O(n) endpoint, intended for debugging/inspection
rather than the hot query path; `limit` (default 50, max 500) bounds the cost.

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

Lightweight health check used by Fly.io and Docker health probes.

```bash
curl http://localhost:8000/health
# {"status": "ok"}
```

---

## Demos

### Crash recovery

`scripts/demo_crash_recovery.py` seeds a game's worth of synthetic events,
starts the processor, `SIGKILL`s it mid-stream, and shows that Redis's
consumer-group pending entries list (PEL) still holds the in-flight,
unacknowledged messages. Restarting the processor drains the PEL (via
`XREADGROUP ... id="0"`) before resuming new messages with `">"`, and the
final state is verified to be exactly correct — no event lost, none
double-applied.

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

Building this demo surfaced a real bug: `XREADGROUP` with id `">"` does
**not** redeliver a consumer's own pending messages after a restart — only
`id="0"` does. The fix (draining the PEL on startup, plus rehydrating the
last materialized snapshot before replaying it) is in `src/processor.py`.

### Multi-game horizontal scaling

`scripts/demo_multi_game.py` seeds two different games and starts two
independent `python -m src.processor <game_id>` subprocesses concurrently —
one per game, sharing nothing (separate stream keys, separate consumer
groups, separate Redis Hash keys). Both reach correct final state
independently, demonstrating that adding capacity for more concurrent games
is purely horizontal.

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

No Redis or `nba_api` connection required — all external dependencies are mocked.

---

## Benchmarks

Measured with `scripts/benchmark_api.py` against `uvicorn src.api:app` and
Redis both running locally in this repo's dev container (1000 requests,
concurrency 10, against `/game/{id}/state` for a populated game):

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

These numbers are from a single uvicorn worker with `--reload`-free defaults
on shared sandbox hardware — not a tuned production deployment, and the long
`/state` p99 tail is consistent with Python's GIL serializing requests on a
single worker under concurrency 10. The `/win-probability` numbers (measured
sequentially, no concurrency) show what the same machine does for a request
that's pure CPU (`predict_proba` on a 3-feature logistic regression) plus the
same Redis `HGET` — under 2ms even on this hardware, confirming the model
inference itself is not the bottleneck.

In production, this scales horizontally and vertically: `uvicorn --workers N`
adds parallel processes (sidesteps the GIL for I/O-bound request handling),
and the Redis `HGET` is O(1) regardless of how many events have been
processed for a game. `apache2-utils`/`locust` weren't installable in this
sandboxed environment (no package mirror access); `scripts/benchmark_api.py`
uses `httpx`, an existing dependency, to produce the numbers above without
new tooling.

---

## Deployment (Fly.io)

```bash
fly launch --no-deploy
fly secrets set REDIS_URL=redis://your-redis-url:6379
fly deploy
```

The `/health` endpoint is configured as the Fly.io health check path in `fly.toml`. The app auto-stops when idle and auto-starts on the first request (configured via `auto_stop_machines = true`).
