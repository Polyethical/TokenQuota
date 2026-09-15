import warnings
from types import SimpleNamespace

import pytest

from tokenquota import (
    BackendError,
    Plan,
    Pricing,
    Quota,
    QuotaExceeded,
    ReservationClosed,
    UnknownModelError,
    UnknownPlanError,
    extract_tokens,
)


def token_quota(store, clock, **kw):
    plans = [
        Plan("free", tokens=10_000, degrade_at=0.5, degrade_to={"big": "small"}),
        Plan("pro", tokens=100_000),
    ]
    return Quota(plans, store=store, default_plan="free", clock=clock, **kw)


# ---------------------------------------------------------------- admission


def test_allows_then_degrades_then_rejects(store, clock):
    q = token_quota(store, clock)
    r = q.reserve("u1", model="big", est_tokens=4_000)
    assert r.model == "big" and not r.degraded
    r.commit(input_tokens=2_000, output_tokens=2_000)

    r = q.reserve("u1", model="big", est_tokens=4_000)  # 4k used + 4k > 5k soft limit
    assert r.model == "small" and r.degraded
    r.commit(input_tokens=1_000, output_tokens=3_000)

    with pytest.raises(QuotaExceeded) as info:
        q.reserve("u1", model="big", est_tokens=4_000)  # 8k used + 4k > 10k
    assert info.value.usage.used == 8_000
    assert info.value.retry_after and info.value.retry_after > 0
    assert "Retry-After" in info.value.usage.headers()


def test_without_degrade_target_allows_up_to_hard_limit(store, clock):
    q = token_quota(store, clock)
    r = q.reserve("u1", model="other", est_tokens=9_000)
    assert r.model == "other" and not r.degraded
    r.release()


def test_reservations_count_before_commit(store, clock):
    q = token_quota(store, clock)
    q.reserve("u1", est_tokens=6_000)
    with pytest.raises(QuotaExceeded):
        q.reserve("u1", est_tokens=6_000)


def test_release_frees_budget(store, clock):
    q = token_quota(store, clock)
    r = q.reserve("u1", est_tokens=10_000)
    r.release()
    assert q.status("u1").reserved == 0
    q.reserve("u1", est_tokens=10_000).release()


def test_expired_reservation_frees_budget(store, clock):
    q = token_quota(store, clock, reservation_ttl=30)
    q.reserve("u1", est_tokens=10_000)  # process "crashes" and never commits
    with pytest.raises(QuotaExceeded):
        q.reserve("u1", est_tokens=1)
    clock.advance(31)
    q.reserve("u1", est_tokens=10_000).release()


def test_users_are_isolated(store, clock):
    q = token_quota(store, clock)
    q.reserve("u1", est_tokens=10_000)
    q.reserve("u2", est_tokens=10_000)


def test_plan_resolution(store, clock):
    q = token_quota(store, clock, plan_for=lambda uid: "pro" if uid.startswith("paid") else None)
    q.reserve("paid-1", est_tokens=50_000).release()
    with pytest.raises(QuotaExceeded):
        q.reserve("free-1", est_tokens=50_000)
    with pytest.raises(UnknownPlanError):
        q.reserve("x", est_tokens=1, plan="enterprise")


def test_usage_follows_user_across_plan_change(store, clock):
    q = token_quota(store, clock)
    q.reserve("u1", est_tokens=9_000, plan="free").commit(input_tokens=9_000, output_tokens=0)
    assert q.status("u1", plan="pro").used == 9_000


def test_monthly_reset(store, clock):
    q = token_quota(store, clock)
    q.reserve("u1", est_tokens=10_000).commit(input_tokens=10_000)
    with pytest.raises(QuotaExceeded):
        q.reserve("u1", est_tokens=1)
    clock.advance(40 * 24 * 3600)
    assert q.status("u1").used == 0
    q.reserve("u1", est_tokens=10_000).release()


def test_daily_plan(store, clock):
    q = Quota([Plan("d", tokens=100, period="day")], store=store, default_plan="d", clock=clock)
    q.reserve("u1", est_tokens=100).commit(input_tokens=100)
    with pytest.raises(QuotaExceeded):
        q.reserve("u1", est_tokens=1)
    clock.advance(24 * 3600)
    q.reserve("u1", est_tokens=100).release()


def test_total_plan_never_resets(store, clock):
    q = Quota([Plan("trial", tokens=100, period="total")], store=store, default_plan="trial", clock=clock)
    q.reserve("u1", est_tokens=100).commit(input_tokens=100)
    clock.advance(400 * 24 * 3600)
    with pytest.raises(QuotaExceeded) as info:
        q.reserve("u1", est_tokens=1)
    assert info.value.retry_after is None


# ---------------------------------------------------------------- commit semantics


def test_commit_records_actual_not_estimate(store, clock):
    q = token_quota(store, clock)
    r = q.reserve("u1", est_tokens=5_000)
    usage = r.commit(input_tokens=100, output_tokens=50)
    assert usage.used == 150 and usage.reserved == 0


def test_double_commit_is_rejected_client_side(store, clock):
    q = token_quota(store, clock)
    r = q.reserve("u1", est_tokens=100)
    r.commit(input_tokens=10)
    with pytest.raises(ReservationClosed):
        r.commit(input_tokens=10)


def test_retried_commit_from_restored_reservation_counts_once(store, clock):
    q = token_quota(store, clock)
    data = q.reserve("u1", est_tokens=100).to_dict()
    q.restore(data).commit(input_tokens=60)
    q.restore(data).commit(input_tokens=60)  # e.g. client retried after a timeout
    assert q.status("u1").used == 60


def test_context_manager_releases_on_error(store, clock):
    q = token_quota(store, clock)
    with pytest.raises(RuntimeError):
        with q.reserve("u1", est_tokens=5_000):
            raise RuntimeError("provider timeout")
    s = q.status("u1")
    assert s.used == 0 and s.reserved == 0


def test_context_manager_without_commit_records_estimate(store, clock):
    q = token_quota(store, clock)
    with pytest.warns(RuntimeWarning):
        with q.reserve("u1", est_tokens=700):
            pass
    assert q.status("u1").used == 700


def test_commit_after_period_rollover_charges_reserved_period(store, clock):
    q = Quota([Plan("d", tokens=1_000, period="day")], store=store, default_plan="d", clock=clock)
    r = q.reserve("u1", est_tokens=500)
    clock.advance(24 * 3600)
    r.commit(input_tokens=500)
    assert q.status("u1").used == 0  # new day is untouched


def test_record_without_reservation(store, clock):
    q = token_quota(store, clock)
    q.record("u1", input_tokens=300, output_tokens=200, idempotency_key="evt-1")
    q.record("u1", input_tokens=300, output_tokens=200, idempotency_key="evt-1")
    assert q.status("u1").used == 500


# ---------------------------------------------------------------- dollars


PRICES = Pricing(
    {
        "big": {"input_per_mtok": 3.0, "output_per_mtok": 15.0},
        "small": {"input_per_mtok": 0.25, "output_per_mtok": 1.25},
    },
    source="example numbers, not real prices",
    updated="2026-09-11",
)


def usd_quota(store, clock):
    return Quota(
        [Plan("starter", usd=1.00, degrade_at=0.8, degrade_to={"big": "small"})],
        store=store,
        pricing=PRICES,
        default_plan="starter",
        clock=clock,
    )


def test_usd_costs_are_exact(store, clock):
    q = usd_quota(store, clock)
    r = q.reserve("u1", model="big", est_input_tokens=1_000, est_output_tokens=1_000)
    assert r.estimate == 18_000  # 1000*3 + 1000*15 micro-dollars
    usage = r.commit(input_tokens=1_000, output_tokens=500)
    assert usage.used == 10_500
    assert usage.to_dict()["used"] == pytest.approx(0.0105)


def test_usd_degrades_to_cheaper_model(store, clock):
    q = usd_quota(store, clock)
    q.record("u1", model="big", input_tokens=0, output_tokens=50_000)  # $0.75
    r = q.reserve("u1", model="big", est_output_tokens=4_000)  # $0.06 more would pass $0.80
    assert r.model == "small" and r.degraded
    assert r.estimate == 5_000
    r.commit(input_tokens=0, output_tokens=4_000)
    assert q.status("u1").used == 755_000


def test_usd_fallback_can_fit_when_primary_cannot(store, clock):
    q = usd_quota(store, clock)
    q.record("u1", model="big", output_tokens=60_000)  # $0.90
    r = q.reserve("u1", model="big", est_output_tokens=10_000)  # big: $0.15 (over), small: $0.0125
    assert r.model == "small"


def test_usd_est_tokens_uses_worst_case_rate(store, clock):
    q = usd_quota(store, clock)
    r = q.reserve("u1", model="big", est_tokens=1_000)
    assert r.estimate == 15_000


def test_usd_requires_known_model(store, clock):
    q = usd_quota(store, clock)
    with pytest.raises(UnknownModelError):
        q.reserve("u1", model="mystery", est_tokens=10)
    with pytest.raises(UnknownModelError):
        q.reserve("u1", est_tokens=10)


def test_usd_plans_require_pricing():
    with pytest.raises(ValueError):
        Quota([Plan("p", usd=5)])


def test_pricing_file(tmp_path):
    path = tmp_path / "prices.json"
    path.write_text('{"source": "s", "updated": "2026-09-11", "models": {"m": {"input_per_mtok": 1, "output_per_mtok": 2}}}')
    p = Pricing.from_file(path)
    assert p.cost_micro_usd("m", 10, 10) == 30 and p.updated == "2026-09-11"


# ---------------------------------------------------------------- failure modes


class BrokenStore:
    def __getattr__(self, name):
        def fail(*a, **k):
            raise ConnectionError("redis down")

        return fail


def test_fail_open_allows_request(clock):
    events = []
    q = token_quota(BrokenStore(), clock, on_event=events.append)
    r = q.reserve("u1", model="big", est_tokens=999_999)
    assert r.checked is False and r.model == "big"
    assert r.commit(input_tokens=1) is None
    assert [e["event"] for e in events] == ["fail_open", "commit_failed"]


def test_fail_closed_raises(clock):
    q = token_quota(BrokenStore(), clock, fail_open=False)
    with pytest.raises(BackendError):
        q.reserve("u1", est_tokens=1)


def test_event_hook_errors_do_not_break_requests(store, clock):
    def bad_hook(event):
        raise ValueError("boom")

    q = token_quota(store, clock, on_event=bad_hook)
    q.reserve("u1", est_tokens=1).commit(input_tokens=1)


# ---------------------------------------------------------------- validation & helpers


def test_plan_validation():
    with pytest.raises(ValueError):
        Plan("x")
    with pytest.raises(ValueError):
        Plan("x", tokens=1, usd=1)
    with pytest.raises(ValueError):
        Plan("x", tokens=1, period="week")
    with pytest.raises(ValueError):
        Plan("x", tokens=1, degrade_at=0)


def test_estimate_required(store, clock):
    q = token_quota(store, clock)
    with pytest.raises(ValueError):
        q.reserve("u1")


@pytest.mark.parametrize(
    "usage, expected",
    [
        (SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15), (10, 5)),
        ({"input_tokens": 10, "output_tokens": 5}, (10, 5)),
        (
            {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 20},
            (130, 5),
        ),
        ({"inputTokens": 7, "outputTokens": 3}, (7, 3)),
        ({"promptTokens": 7, "completionTokens": 3}, (7, 3)),
        ({"totalTokens": 9}, (9, 0)),
    ],
)
def test_extract_tokens(usage, expected):
    assert extract_tokens(usage) == expected


def test_extract_tokens_rejects_unknown():
    with pytest.raises(ValueError):
        extract_tokens({"foo": 1})
    with pytest.raises(ValueError):
        extract_tokens(None)


def test_headers_and_dict(store, clock):
    q = token_quota(store, clock)
    usage = q.reserve("u1", est_tokens=1_000).commit(input_tokens=400)
    h = usage.headers()
    assert h["X-Quota-Limit"] == "10000" and h["X-Quota-Remaining"] == "9600"
    assert usage.to_dict()["period"] == "2026-08"
