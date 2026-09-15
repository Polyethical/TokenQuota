"""How much delay does a quota check add? Measures reserve + commit.

    python latency.py
    REDIS_URL=redis://localhost:6379/0 python latency.py
    SERVER_URL=http://localhost:8080 TOKENQUOTA_API_KEY=... python latency.py

Numbers depend on your hardware and network; re-run them on yours.
"""

from __future__ import annotations

import json
import os
import platform
import statistics
import time
import urllib.request
import uuid

from tokenquota import MemoryStore, Plan, Quota, RedisStore

N = int(os.environ.get("N", "2000"))


def pct(samples, p):
    s = sorted(samples)
    return s[min(len(s) - 1, int(len(s) * p / 100))]


def summarize(name, samples_ms):
    return {
        "backend": name,
        "n": len(samples_ms),
        "p50_ms": round(statistics.median(samples_ms), 3),
        "p95_ms": round(pct(samples_ms, 95), 3),
        "p99_ms": round(pct(samples_ms, 99), 3),
    }


def bench_library(name, store):
    q = Quota([Plan("p", tokens=10**12)], store=store, default_plan="p")
    user = f"lat-{uuid.uuid4().hex[:6]}"
    for _ in range(100):  # warm-up
        q.reserve(user, est_tokens=10).commit(input_tokens=10)
    samples = []
    for _ in range(N):
        t0 = time.perf_counter()
        q.reserve(user, est_tokens=10).commit(input_tokens=10)
        samples.append((time.perf_counter() - t0) * 1000)
    return summarize(name, samples)


def bench_http(url, key):
    def post(path, body):
        req = urllib.request.Request(
            url.rstrip("/") + path,
            data=json.dumps(body).encode(),
            headers={"content-type": "application/json", "authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    user = f"lat-{uuid.uuid4().hex[:6]}"
    samples = []
    for i in range(min(N, 1000) + 50):
        t0 = time.perf_counter()
        r = post("/v1/reserve", {"user_id": user, "est_tokens": 1})
        post("/v1/commit", {"reservation": r["reservation"], "input_tokens": 1})
        if i >= 50:
            samples.append((time.perf_counter() - t0) * 1000)
    return summarize("http server (2 round trips)", samples)


def main():
    print(f"# {platform.platform()} / Python {platform.python_version()} / N={N}")
    rows = [bench_library("memory", MemoryStore())]
    if os.environ.get("REDIS_URL"):
        rows.append(bench_library("redis", RedisStore(url=os.environ["REDIS_URL"], prefix="lat")))
    if os.environ.get("SERVER_URL"):
        rows.append(bench_http(os.environ["SERVER_URL"], os.environ.get("TOKENQUOTA_API_KEY", "")))
    print("| backend | n | p50 ms | p95 ms | p99 ms |\n|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['backend']} | {r['n']} | {r['p50_ms']} | {r['p95_ms']} | {r['p99_ms']} |")


if __name__ == "__main__":
    main()
