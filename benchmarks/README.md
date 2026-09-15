# tokenquota benchmarks

Re-runnable checks for the two questions an engineer asks before putting something in front of every model call: **does it hold under load**, and **how much delay does it add**.

```bash
pip install -r requirements.txt
docker run -d -p 6379:6379 redis:7

REDIS_URL=redis://localhost:6379/0 python concurrency.py
REDIS_URL=redis://localhost:6379/0 python latency.py
SERVER_URL=http://localhost:8080 TOKENQUOTA_API_KEY=... python latency.py   # through quota-server
```

## concurrency.py

64 threads (in-memory and Redis) and 16 separate processes (Redis) race for one user's 100,000-token budget in 250-token requests. It passes only if exactly 400 requests are admitted, final usage is exactly 100,000 and nothing is left reserved. Exit code 1 on any violation.

## latency.py

Median, p95 and p99 time for one reserve plus one commit. Results depend heavily on where Redis and the server run relative to your app, so run it on your own infrastructure. Every push to `main` also runs both scripts in GitHub Actions; see the job summary for the latest numbers.

Example run (sandbox VM, Redis and server on localhost, N=1000):

| backend | p50 ms | p95 ms | p99 ms |
|---|---|---|---|
| memory | 0.026 | 0.046 | 0.072 |
| redis | 0.231 | 0.337 | 0.418 |
| http server (2 round trips) | 3.56 | 4.38 | 5.24 |

## reconcile.py

Accuracy check. Compares the tokens tokenquota recorded (via the `on_event` hook) against your provider's usage export, per day and model, and flags differences above 2%. See the docstring for the ledger format.
