"""Pick each user's quota plan from their Stripe subscription.

    pip install tokenquota stripe
    export STRIPE_API_KEY=sk_test_...

Map your Stripe price IDs to plan names, then pass plan_for=plan_for to Quota.
Results are cached for five minutes so Stripe isn't called on every request;
for instant upgrades, clear the cache from your customer.subscription.updated
webhook handler.
"""

import os
import time
from typing import Callable, Dict, Optional, Tuple

import stripe

from tokenquota import Plan, Quota

stripe.api_key = os.environ["STRIPE_API_KEY"]

PRICE_TO_PLAN: Dict[str, str] = {
    "price_REPLACE_starter_monthly": "starter",
    "price_REPLACE_pro_monthly": "pro",
}


def stripe_plan_resolver(
    customer_id_for: Callable[[str], Optional[str]], ttl: float = 300
) -> Tuple[Callable[[str], Optional[str]], Callable[[str], None]]:
    """Return ``(plan_for, invalidate)``. ``customer_id_for`` maps your user id to a Stripe customer id."""
    cache: Dict[str, Tuple[float, Optional[str]]] = {}

    def plan_for(user_id: str) -> Optional[str]:
        hit = cache.get(user_id)
        if hit and hit[0] > time.time():
            return hit[1]
        plan = None
        customer = customer_id_for(user_id)
        if customer:
            subs = stripe.Subscription.list(customer=customer, status="active", limit=1)
            for sub in subs.data:
                for item in sub["items"]["data"]:
                    plan = PRICE_TO_PLAN.get(item["price"]["id"], plan)
        cache[user_id] = (time.time() + ttl, plan)
        return plan  # None -> Quota falls back to default_plan

    def invalidate(user_id: str) -> None:
        cache.pop(user_id, None)

    return plan_for, invalidate


# Example wiring. Replace the lambda with a lookup in your own users table.
plan_for, invalidate = stripe_plan_resolver(lambda user_id: None)
quota = Quota(
    [Plan("free", tokens=20_000), Plan("starter", tokens=500_000), Plan("pro", tokens=5_000_000)],
    default_plan="free",
    plan_for=plan_for,
)
