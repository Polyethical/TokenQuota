"""Per-user dollar budget in front of the Anthropic SDK.

    pip install tokenquota anthropic
    export ANTHROPIC_API_KEY=...
    python chat.py

Edit prices.json with current prices from the provider's pricing page first.
"""

import os
from pathlib import Path

import anthropic

from tokenquota import Plan, Pricing, Quota, QuotaExceeded

LARGE = os.environ.get("MODEL_LARGE", "claude-sonnet-5")
SMALL = os.environ.get("MODEL_SMALL", "claude-haiku-4-5-20251001")
MAX_OUTPUT = 1024

PRICES = Pricing.from_file(Path(__file__).with_name("prices.json"))
if PRICES.updated == "YYYY-MM-DD":
    raise SystemExit("Fill in prices.json with current prices before running this example.")

client = anthropic.Anthropic()
quota = Quota(
    [Plan("starter", usd=2.00, degrade_at=0.75, degrade_to={LARGE: SMALL})],
    pricing=PRICES,
    default_plan="starter",
)


def answer(user_id: str, question: str) -> str:
    try:
        r = quota.reserve(user_id, model=LARGE, est_input_tokens=len(question) // 3 + 50, est_output_tokens=MAX_OUTPUT)
    except QuotaExceeded:
        return "You've reached this month's AI limit on the Starter plan."
    try:
        msg = client.messages.create(
            model=r.model,
            max_tokens=MAX_OUTPUT,
            messages=[{"role": "user", "content": question}],
        )
    except Exception:
        r.release()
        raise
    r.commit(msg.usage)  # includes prompt-cache tokens
    return msg.content[0].text


if __name__ == "__main__":
    print(answer("user_42", "In one sentence, what is a token budget?"))
    print(quota.status("user_42").to_dict())
