"""Per-user budget in front of the OpenAI SDK.

    pip install tokenquota openai
    export OPENAI_API_KEY=...
    export MODEL_LARGE=<a model you use>  MODEL_SMALL=<a cheaper model>
    python chat.py
"""

import os

from openai import OpenAI

from tokenquota import Plan, Quota, QuotaExceeded

LARGE = os.environ["MODEL_LARGE"]
SMALL = os.environ["MODEL_SMALL"]
MAX_OUTPUT = 800

client = OpenAI()
quota = Quota(
    [Plan("free", tokens=100_000, degrade_to={LARGE: SMALL})],
    default_plan="free",
)


def answer(user_id: str, question: str) -> str:
    est_prompt = len(question) // 3 + 50  # rough, deliberately high
    try:
        r = quota.reserve(user_id, model=LARGE, est_input_tokens=est_prompt, est_output_tokens=MAX_OUTPUT)
    except QuotaExceeded as e:
        return f"You've used your monthly AI allowance. It resets in {e.retry_after // 3600} hours."
    try:
        resp = client.chat.completions.create(
            model=r.model,  # may be the cheaper model
            max_completion_tokens=MAX_OUTPUT,
            messages=[{"role": "user", "content": question}],
        )
    except Exception:
        r.release()  # nothing billed, give the budget back
        raise
    r.commit(resp.usage)
    return resp.choices[0].message.content


if __name__ == "__main__":
    print(answer("user_42", "In one sentence, what is a token budget?"))
    print(quota.status("user_42").to_dict())
