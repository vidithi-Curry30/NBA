"""
Measure real /state endpoint latency and throughput against a running API.

WHY a small async script instead of `ab`/`locust`: neither is installable in
this sandbox (no apt package mirror, no PyPI access for locust). `httpx`
is already a project dependency, so this gives real, reproducible numbers
without adding new tooling.

Usage (with `uvicorn src.api:app` and Redis already running, and at least one
game's state populated, e.g. via scripts/demo_multi_game.py):

    python -m scripts.benchmark_api --game demo_multi_a --requests 1000 --concurrency 10
"""

import argparse
import asyncio
import statistics
import time

import httpx


async def _worker(client: httpx.AsyncClient, url: str, n: int, latencies: list[float]) -> None:
    for _ in range(n):
        start = time.perf_counter()
        resp = await client.get(url)
        resp.raise_for_status()
        latencies.append((time.perf_counter() - start) * 1000.0)


async def run(host: str, game_id: str, total_requests: int, concurrency: int) -> None:
    url = f"{host}/game/{game_id}/state"
    per_worker = total_requests // concurrency
    latencies: list[float] = []

    async with httpx.AsyncClient() as client:
        # WHY a warmup request: the first request pays connection-pool setup
        # cost; excluding it from the measured sample avoids skewing p50/p99.
        await client.get(url)

        start = time.perf_counter()
        await asyncio.gather(*[
            _worker(client, url, per_worker, latencies)
            for _ in range(concurrency)
        ])
        elapsed = time.perf_counter() - start

    latencies.sort()
    n = len(latencies)
    p50 = latencies[int(n * 0.50)]
    p95 = latencies[int(n * 0.95)]
    p99 = latencies[min(int(n * 0.99), n - 1)]

    print(f"Endpoint: GET {url}")
    print(f"Requests: {n}, concurrency: {concurrency}")
    print(f"Total time: {elapsed:.2f}s")
    print(f"Throughput: {n / elapsed:.1f} req/s")
    print(f"Latency p50: {p50:.2f}ms")
    print(f"Latency p95: {p95:.2f}ms")
    print(f"Latency p99: {p99:.2f}ms")
    print(f"Latency mean: {statistics.mean(latencies):.2f}ms")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="http://localhost:8000")
    parser.add_argument("--game", default="demo_multi_a")
    parser.add_argument("--requests", type=int, default=1000)
    parser.add_argument("--concurrency", type=int, default=10)
    args = parser.parse_args()
    asyncio.run(run(args.host, args.game, args.requests, args.concurrency))
