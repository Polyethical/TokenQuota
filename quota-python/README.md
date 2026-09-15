# tokenquota

[![CI](https://github.com/YOUR-ORG/quota-python/actions/workflows/ci.yml/badge.svg)](https://github.com/YOUR-ORG/quota-python/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/tokenquota)](https://pypi.org/project/tokenquota/)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/YOUR-ORG/quota-python/badge)](https://securityscorecards.dev/viewer/?uri=github.com/YOUR-ORG/quota-python)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

**Per-user token and dollar budgets for AI apps, enforced before the model call, not after the bill.**

<!-- Record the offline demo (examples/offline-demo) and put the GIF here -->

```bash
pip install tokenquota            # in-process store
pip install "tokenquota[redis]"   # shared across workers and machines
```

## Quickstart

```python
from openai import OpenAI
from tokenquota import Plan, Quota, QuotaExceeded

client = OpenAI()
quota = Quota(
    [
        Plan("free", tokens=50_000, degrade_to={"model-large": "model-small"}),
        Plan("pro", tokens=2_000_000),
    ],
    default_plan="free",
)

def answer(user_id: str, question: str, plan: str) -> str:
    try:
        r = quota.reserve(user_id, plan=plan, model="model-large",
                          est_input_tokens=len(question) // 3 + 50, est_output_tokens=800)
    except QuotaExceeded as e:
        return f"You're out of AI credits until {e.usage.to_dict()['resets_at']}. Upgrade for more."

    try:
        resp = client.chat.completions.create(
            model=r.model,                   # may be the cheaper model
            max_completion_tokens=800,
            messages=[{"role": "user", "content": question}],
        )
    except Exception:
        r.release()                          # nothing billed: give the budget back
        raise
    r.commit(resp.usage)                     # record what was actually used
    return resp.choices[0].message.content
```

Or use a `with` block: an exception releases the reservation automatically.

```python
with quota.reserve(user_id, model="model-large", est_tokens=2_000) as r:
    resp = client.chat.completions.create(model=r.model, messages=messages)
    r.commit(resp.usage)
```

## How it works

1. **Reserve.** Before the call, `reserve()` holds your estimate against the user's budget in one atomic step. Concurrent requests for the same user can never jointly overshoot the limit.
2. **Degrade.** Once the user passes `degrade_at` (default 80%) of their budget, the reservation hands back the cheaper model from `degrade_to`. Requests are refused only when even the cheaper model wouldn't fit.
3. **Commit or release.** After the call, `commit()` swaps the estimate for real usage. If the call failed, `release()` returns the budget. Reservations your process never finishes expire after `reservation_ttl` seconds (default 120).

Estimate generously (use your `max_tokens` for the output side): a reservation that's too small lets a user go slightly over; one that's too large only refuses a request early.

## Dollar budgets

Different models cost different amounts, so a dollar budget is often fairer than a token budget. Supply your own prices (they change, so keep the source and date with them):

```python
from tokenquota import Plan, Pricing, Quota

pricing = Pricing.from_file("prices.json")   # {"source": ..., "updated": ..., "models": {name: {input_per_mtok, output_per_mtok}}}
quota = Quota([Plan("starter", usd=5.00, degrade_to={"model-large": "model-small"})],
              pricing=pricing, default_plan="starter")
```

Costs are tracked in integer micro-dollars, so thousands of tiny charges add up exactly.

## Production setup

```python
from tokenquota import Quota, RedisStore

quota = Quota(plans, store=RedisStore(url="redis://localhost:6379/0"),
              plan_for=lambda user_id: lookup_plan(user_id),   # e.g. from your Stripe subscription
              fail_open=True)
```

| Option | Default | Meaning |
|---|---|---|
| `store` | `MemoryStore()` | `RedisStore` for more than one process. Works with Redis Cluster. |
| `plan_for` | `None` | Function from user ID to plan name. Or pass `plan=` per call. |
| `default_plan` | `None` | Used when no plan is given or resolved. |
| `fail_open` | `True` | If the store is unreachable: allow requests (and log) or raise `BackendError`. |
| `reservation_ttl` | `120` | Seconds before an unfinished reservation is released. |
| `on_event` | `None` | Callback for `reserved`, `degraded`, `rejected`, `committed`, `released`, `fail_open` events. |

Budgets reset on UTC calendar boundaries (`period="day"`, `"month"`) or never (`"total"`, for trials). Usage follows the user, so upgrading mid-month keeps what they've already spent. `Usage.headers()` gives you `X-Quota-Remaining`-style headers to forward to your own API clients.

## Correctness and speed

- Every store operation is atomic (a single Lua script on Redis).
- Commits are idempotent for 24 hours: retrying a commit after a timeout never double-counts.
- The test suite runs every behaviour test against both stores, including a 32-thread contention test. The [benchmarks](https://github.com/YOUR-ORG/benchmarks) repo adds a 16-process test against Redis and a latency script you can run on your own hardware.

## Supported usage objects

`commit()` accepts OpenAI (chat completions and Responses API), Anthropic (including prompt-cache fields), Vercel AI SDK usage objects, dicts, or `input_tokens=` / `output_tokens=` directly.

**Known limitations (0.1):** Anthropic cache reads are counted at full input price (you may slightly over-charge, never under-charge). Streaming works (reserve before the stream, commit when the final usage arrives) but there's no mid-stream cut-off.

## Other languages

Use [quota-server](https://github.com/YOUR-ORG/quota-server) (HTTP) and the [TypeScript client](https://github.com/YOUR-ORG/quota-js).

## License

MIT. Security reports: see [SECURITY.md](https://github.com/YOUR-ORG/.github/blob/main/SECURITY.md).
