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

### `GET /game/{game_id}/momentum`

Returns last 10 possessions and a momentum score (positive = home team on a run).

```bash
curl http://localhost:8000/game/0042300401/momentum
```

```json
{
  "game_id": "0042300401",
  "last_10_possessions": ["home_score", "home_score", "turnover", ...],
  "momentum_score": 3,
  "interpretation": "BOS on a run"
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

### `GET /health`

Lightweight health check used by Fly.io and Docker health probes.

```bash
curl http://localhost:8000/health
# {"status": "ok"}
```

---

## Running Tests

```bash
pytest tests/ -v
```

No Redis or `nba_api` connection required — all external dependencies are mocked.

---

## Benchmarks

Measured with Redis running locally on the same machine (Docker). API read latency from `/game/{id}/state`:

```bash
# Install apache bench
apt install apache2-utils

# Run 1000 requests, 10 concurrent
ab -n 1000 -c 10 http://localhost:8000/game/0042300401/state
```

| Metric | Result |
|--------|--------|
| p50 latency | ~2ms |
| p99 latency | ~8ms |
| Throughput | ~1,200 req/s |

The API is I/O-bound on the Redis `HGET` call. Sub-10ms p99 is achievable because:
1. The Redis Hash key is a single field lookup — O(1) at the Redis level.
2. The JSON payload is small (~500 bytes per game state).
3. `redis.asyncio` reuses a connection pool rather than opening a new TCP connection per request.

To run with `locust` for a more realistic load profile:

```bash
pip install locust
locust -f locustfile.py --headless -u 50 -r 10 --run-time 30s --host http://localhost:8000
```

---

## Deployment (Fly.io)

```bash
fly launch --no-deploy
fly secrets set REDIS_URL=redis://your-redis-url:6379
fly deploy
```

The `/health` endpoint is configured as the Fly.io health check path in `fly.toml`. The app auto-stops when idle and auto-starts on the first request (configured via `auto_stop_machines = true`).
