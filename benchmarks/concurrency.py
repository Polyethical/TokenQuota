"""Correctness under contention: many workers race for one user's budget.

Passes only if the admitted estimates never exceed the limit and the final
books balance exactly.

    python concurrency.py                          # threads, in-memory store
    REDIS_URL=redis://localhost:6379/0 python concurrency.py   # + processes, Redis
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import sys
import threading
import uuid

from tokenquota import MemoryStore, Plan, Quota, QuotaExceeded, RedisStore

LIMIT = 100_000
EST = 250
ATTEMPTS = 200


def run_worker(quota: Quota, user: str, out: list, lock: threading.Lock) -> None:
    for _ in range(ATTEMPTS):
        try:
            r = quota.reserve(user, est_tokens=EST)
        except QuotaExceeded:
            continue
        r.commit(input_tokens=EST // 2, output_tokens=EST - EST // 2)
        with lock:
            out.append(1)


def threads_case(store, workers: int) -> dict:
    quota = Quota([Plan("p", tokens=LIMIT)], store=store, default_plan="p")
    user = f"bench-{uuid.uuid4().hex[:8]}"
    admitted, lock = [], threading.Lock()
    ts = [threading.Thread(target=run_worker, args=(quota, user, admitted, lock)) for _ in range(workers)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    s = quota.status(user)
    return {"workers": workers, "admitted": len(admitted), "used": s.used, "reserved": s.reserved}


def _proc(url: str, prefix: str, user: str, q: mp.Queue) -> None:
    quota = Quota([Plan("p", tokens=LIMIT)], store=RedisStore(url=url, prefix=prefix), default_plan="p")
    admitted, lock = [], threading.Lock()
    run_worker(quota, user, admitted, lock)
    q.put(len(admitted))


def processes_case(url: str, procs: int) -> dict:
    prefix, user = f"bench-{uuid.uuid4().hex[:8]}", "shared"
    q: mp.Queue = mp.Queue()
    ps = [mp.Process(target=_proc, args=(url, prefix, user, q)) for _ in range(procs)]
    for p in ps:
        p.start()
    for p in ps:
        p.join()
    admitted = sum(q.get() for _ in ps)
    quota = Quota([Plan("p", tokens=LIMIT)], store=RedisStore(url=url, prefix=prefix), default_plan="p")
    s = quota.status(user)
    return {"processes": procs, "admitted": admitted, "used": s.used, "reserved": s.reserved}


def check(name: str, result: dict) -> bool:
    expected = LIMIT // EST
    ok = result["admitted"] == expected and result["used"] == LIMIT and result["reserved"] == 0
    print(f"{'PASS' if ok else 'FAIL'}  {name:<28} {json.dumps(result)}  (expected {expected} admitted)")
    return ok


def main() -> int:
    results = [check("memory, 64 threads", threads_case(MemoryStore(), 64))]
    url = os.environ.get("REDIS_URL")
    if url:
        results.append(check("redis, 64 threads", threads_case(RedisStore(url=url, prefix=f"b-{uuid.uuid4().hex[:6]}"), 64)))
        results.append(check("redis, 16 processes", processes_case(url, 16)))
    else:
        print("skip  redis cases (set REDIS_URL)")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
