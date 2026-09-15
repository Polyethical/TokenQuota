"""Runs with no API keys: a fake model shows allow -> degrade -> block.

    pip install tokenquota
    python demo.py

Good for recording the README GIF.
"""

import random
import time

from tokenquota import Plan, Quota, QuotaExceeded

quota = Quota(
    [Plan("free", tokens=20_000, degrade_at=0.6, degrade_to={"model-large": "model-small"})],
    default_plan="free",
)


def fake_model(model: str, max_tokens: int) -> dict:
    """Stand-in for a provider call. Returns a usage object like OpenAI's."""
    time.sleep(0.15)
    return {"prompt_tokens": random.randint(800, 1200), "completion_tokens": random.randint(1500, max_tokens)}


for i in range(1, 12):
    try:
        with quota.reserve("user_42", model="model-large", est_tokens=1_200 + 2_500) as r:
            result = fake_model(r.model, max_tokens=2_500)
            usage = r.commit(result)
    except QuotaExceeded as e:
        print(f"request {i:2}: BLOCKED     {e.usage.used:>6,} / {e.usage.limit:,} tokens used - show an upgrade prompt")
        continue
    tag = "degraded -> " if r.degraded else "            "
    print(f"request {i:2}: {tag}{r.model:<12} {usage.used:>6,} / {usage.limit:,} tokens used")
