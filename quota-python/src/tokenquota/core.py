"""Plans, reservations and the Quota object."""

from __future__ import annotations

import logging
import math
import time
import uuid
import warnings
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Tuple, Union

from .errors import (
    BackendError,
    QuotaError,
    QuotaExceeded,
    ReservationClosed,
    UnknownModelError,
    UnknownPlanError,
)
from .periods import PERIODS, current_period
from .pricing import Pricing
from .stores import FALLBACK, REJECT, MemoryStore, Store

log = logging.getLogger("tokenquota")

MICRO = 1_000_000
KEY_GRACE_SECONDS = 7 * 24 * 3600  # keep last period's numbers readable for a week


# --------------------------------------------------------------------------- plans


@dataclass(frozen=True)
class Plan:
    """A budget attached to a pricing tier of *your* product.

    Set exactly one of ``tokens`` (a token budget) or ``usd`` (a dollar budget,
    which requires a :class:`Pricing` table).

    ``degrade_at`` is the fraction of the budget after which requests are
    switched to the cheaper model named in ``degrade_to`` instead of being
    refused. Requests are only refused when even the cheaper model would not
    fit under the full budget.
    """

    name: str
    tokens: Optional[int] = None
    usd: Optional[float] = None
    period: str = "month"
    degrade_at: float = 0.8
    degrade_to: Mapping[str, str] = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        if (self.tokens is None) == (self.usd is None):
            raise ValueError(f"plan {self.name!r}: set exactly one of tokens= or usd=")
        if self.tokens is not None and self.tokens < 0:
            raise ValueError(f"plan {self.name!r}: tokens must be >= 0")
        if self.usd is not None and self.usd < 0:
            raise ValueError(f"plan {self.name!r}: usd must be >= 0")
        if self.period not in PERIODS:
            raise ValueError(f"plan {self.name!r}: period must be one of {PERIODS}")
        if not 0 < self.degrade_at <= 1:
            raise ValueError(f"plan {self.name!r}: degrade_at must be in (0, 1]")
        object.__setattr__(self, "degrade_to", MappingProxyType(dict(self.degrade_to)))

    @property
    def unit(self) -> str:
        return "tokens" if self.tokens is not None else "usd"

    @property
    def limit_units(self) -> int:
        if self.tokens is not None:
            return int(self.tokens)
        return int(round(self.usd * MICRO))  # type: ignore[operator]

    @property
    def soft_limit_units(self) -> int:
        return int(math.floor(self.limit_units * self.degrade_at))


# --------------------------------------------------------------------------- usage


def _to_display(unit: str, value: int) -> Union[int, float]:
    return value / MICRO if unit == "usd" else value


@dataclass(frozen=True)
class Usage:
    """A snapshot of one user's budget for the current period.

    Internal values are integers: tokens, or micro-dollars for ``usd`` plans.
    ``to_dict()`` and ``headers()`` convert micro-dollars back to dollars.
    """

    user_id: str
    plan: str
    unit: str
    limit: int
    used: int
    reserved: int
    period_id: str
    resets_at: Optional[float]
    retry_after: Optional[int] = None

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used - self.reserved)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "plan": self.plan,
            "unit": self.unit,
            "limit": _to_display(self.unit, self.limit),
            "used": _to_display(self.unit, self.used),
            "reserved": _to_display(self.unit, self.reserved),
            "remaining": _to_display(self.unit, self.remaining),
            "period": self.period_id,
            "resets_at": int(self.resets_at) if self.resets_at is not None else None,
        }

    def headers(self) -> Dict[str, str]:
        """Response headers you can forward to your own API clients."""
        h = {
            "X-Quota-Limit": str(_to_display(self.unit, self.limit)),
            "X-Quota-Remaining": str(_to_display(self.unit, self.remaining)),
            "X-Quota-Unit": self.unit,
        }
        if self.resets_at is not None:
            h["X-Quota-Reset"] = str(int(self.resets_at))
        if self.retry_after is not None:
            h["Retry-After"] = str(self.retry_after)
        return h


# --------------------------------------------------------------------------- tokens


def _read(obj: Any, name: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def _first(obj: Any, *names: str) -> Optional[int]:
    for name in names:
        value = _read(obj, name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
    return None


def extract_tokens(usage: Any) -> Tuple[int, int]:
    """Return ``(input_tokens, output_tokens)`` from a provider usage object.

    Understands OpenAI chat completions (``prompt_tokens``/``completion_tokens``),
    the OpenAI Responses API and Anthropic (``input_tokens``/``output_tokens``),
    Anthropic prompt-cache fields, and Vercel AI SDK camelCase fields.
    Works with SDK objects and plain dicts.

    Anthropic reports cache reads and cache writes separately from
    ``input_tokens``; they are added to the input count. Cache reads are billed
    at a discount by the provider, so this errs on the side of charging the
    user slightly more, never less.
    """
    if usage is None:
        raise ValueError("usage is None; pass the provider's usage object or explicit token counts")
    inp = _first(usage, "input_tokens", "prompt_tokens", "inputTokens", "promptTokens")
    out = _first(usage, "output_tokens", "completion_tokens", "outputTokens", "completionTokens")
    if inp is None and out is None:
        total = _first(usage, "total_tokens", "totalTokens")
        if total is None:
            raise ValueError(f"could not find token counts in {type(usage).__name__}")
        return total, 0
    cache = (_first(usage, "cache_creation_input_tokens") or 0) + (_first(usage, "cache_read_input_tokens") or 0)
    return (inp or 0) + cache, out or 0


# --------------------------------------------------------------------------- reservation


class Reservation:
    """Budget held for one model call.

    Call the model with ``reservation.model`` (it may be a cheaper model than
    you asked for), then call ``commit()`` with the provider's usage object.
    Call ``release()`` if the model call failed and nothing was billed.

    Used as a context manager, an exception releases the reservation; leaving
    the block without committing records the *estimate* as usage (and warns),
    because the call probably happened and was billed.
    """

    def __init__(
        self,
        quota: "Quota",
        *,
        id: str,
        key: str,
        user_id: str,
        plan: Plan,
        model: Optional[str],
        requested_model: Optional[str],
        degraded: bool,
        estimate: int,
        usage: Optional[Usage],
        checked: bool,
    ) -> None:
        self._quota = quota
        self.id = id
        self.key = key
        self.user_id = user_id
        self.plan = plan
        self.model = model
        self.requested_model = requested_model
        self.degraded = degraded
        self.estimate = estimate
        self.usage = usage
        self.checked = checked
        self.closed = False

    def __repr__(self) -> str:
        return (
            f"Reservation(user_id={self.user_id!r}, model={self.model!r}, degraded={self.degraded}, "
            f"estimate={self.estimate} {self.plan.unit}, checked={self.checked})"
        )

    def commit(
        self,
        usage: Any = None,
        *,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
    ) -> Optional[Usage]:
        """Record what the call actually used. Returns the updated :class:`Usage`."""
        if self.closed:
            raise ReservationClosed(f"reservation {self.id} is already closed")
        if usage is not None:
            input_tokens, output_tokens = extract_tokens(usage)
        elif input_tokens is None and output_tokens is None:
            raise ValueError("pass the provider usage object or input_tokens/output_tokens")
        input_tokens, output_tokens = int(input_tokens or 0), int(output_tokens or 0)
        actual = self._quota._units(self.plan, self.model, input_tokens, output_tokens)
        self.closed = True
        return self._quota._commit(self, actual, input_tokens, output_tokens)

    def release(self) -> Optional[Usage]:
        """Give the held budget back without recording usage."""
        if self.closed:
            raise ReservationClosed(f"reservation {self.id} is already closed")
        self.closed = True
        return self._quota._release(self)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize so another process can finish the reservation (see ``Quota.restore``)."""
        return {
            "v": 1,
            "id": self.id,
            "key": self.key,
            "user_id": self.user_id,
            "plan": self.plan.name,
            "model": self.model,
            "requested_model": self.requested_model,
            "degraded": self.degraded,
            "estimate": self.estimate,
            "checked": self.checked,
        }

    def __enter__(self) -> "Reservation":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self.closed:
            return False
        if exc_type is not None:
            self.release()
        else:
            warnings.warn(
                "reservation left without commit(); recording the estimate as usage",
                RuntimeWarning,
                stacklevel=2,
            )
            self.closed = True
            self._quota._commit(self, self.estimate, None, None)
        return False


# --------------------------------------------------------------------------- quota


class Quota:
    """Enforces per-user budgets before model calls.

    >>> quota = Quota([Plan("free", tokens=50_000, degrade_to={"big": "small"})], default_plan="free")
    >>> with quota.reserve("user_1", model="big", est_tokens=1_000) as r:
    ...     r.commit(input_tokens=400, output_tokens=300)  # doctest: +ELLIPSIS
    Usage(...)
    """

    def __init__(
        self,
        plans: Union[Iterable[Plan], Mapping[str, Plan]],
        *,
        store: Optional[Store] = None,
        pricing: Optional[Pricing] = None,
        default_plan: Optional[str] = None,
        plan_for: Optional[Callable[[str], Optional[str]]] = None,
        fail_open: bool = True,
        reservation_ttl: float = 120.0,
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
        key_prefix: str = "",
        clock: Callable[[], float] = time.time,
    ) -> None:
        plan_list = list(plans.values()) if isinstance(plans, Mapping) else list(plans)
        self.plans: Dict[str, Plan] = {p.name: p for p in plan_list}
        if len(self.plans) != len(plan_list):
            raise ValueError("plan names must be unique")
        if default_plan is not None and default_plan not in self.plans:
            raise UnknownPlanError(f"default_plan {default_plan!r} is not a configured plan")
        if pricing is None and any(p.unit == "usd" for p in self.plans.values()):
            raise ValueError("dollar-denominated plans need pricing=Pricing(...)")
        if pricing is not None:
            for p in self.plans.values():
                for src, dst in p.degrade_to.items():
                    for m in (src, dst):
                        if p.unit == "usd" and m not in pricing:
                            raise UnknownModelError(f"plan {p.name!r} references unpriced model {m!r}")
        self.store: Store = store if store is not None else MemoryStore()
        self.pricing = pricing
        self.default_plan = default_plan
        self.plan_for = plan_for
        self.fail_open = fail_open
        self.reservation_ttl = reservation_ttl
        self.on_event = on_event
        self.key_prefix = key_prefix
        self.clock = clock

    # ---- public API

    def reserve(
        self,
        user_id: str,
        *,
        model: Optional[str] = None,
        est_tokens: Optional[int] = None,
        est_input_tokens: Optional[int] = None,
        est_output_tokens: Optional[int] = None,
        plan: Optional[str] = None,
        ttl: Optional[float] = None,
    ) -> Reservation:
        """Hold budget for one model call, or raise :class:`QuotaExceeded`.

        Estimate generously: use your ``max_tokens`` for the output side. The
        reservation is released automatically after ``ttl`` seconds if your
        process dies before committing.
        """
        p = self._plan(user_id, plan)
        now = self.clock()
        period_id, resets_at = current_period(p.period, now)
        key = self._key(user_id, p, period_id)
        est_primary = self._estimate(p, model, est_tokens, est_input_tokens, est_output_tokens)
        fallback_model = p.degrade_to.get(model) if model is not None else None
        est_fallback = (
            self._estimate(p, fallback_model, est_tokens, est_input_tokens, est_output_tokens)
            if fallback_model is not None
            else None
        )
        res_id = uuid.uuid4().hex
        try:
            decision, used, reserved = self.store.reserve(
                key,
                res_id,
                limit=p.limit_units,
                soft_limit=p.soft_limit_units,
                est_primary=est_primary,
                est_fallback=est_fallback,
                expires_at=now + (ttl if ttl is not None else self.reservation_ttl),
                now=now,
                key_ttl=self._key_ttl(resets_at, now),
            )
        except QuotaError:
            raise
        except Exception as exc:
            if not self.fail_open:
                raise BackendError(f"quota store unavailable: {exc}") from exc
            log.warning("quota store unavailable, allowing request (fail_open=True): %s", exc)
            self._emit("fail_open", user_id, p, model, est_primary, error=str(exc))
            return Reservation(
                self,
                id=res_id,
                key=key,
                user_id=user_id,
                plan=p,
                model=model,
                requested_model=model,
                degraded=False,
                estimate=est_primary,
                usage=None,
                checked=False,
            )
        usage = self._usage(user_id, p, period_id, resets_at, used, reserved, now)
        if decision == REJECT:
            self._emit("rejected", user_id, p, model, est_primary)
            raise QuotaExceeded(
                self._usage(user_id, p, period_id, resets_at, used, reserved, now, rejected=True)
            )
        degraded = decision == FALLBACK
        chosen = fallback_model if degraded else model
        estimate = est_fallback if degraded else est_primary
        self._emit("degraded" if degraded else "reserved", user_id, p, chosen, estimate, requested_model=model)
        return Reservation(
            self,
            id=res_id,
            key=key,
            user_id=user_id,
            plan=p,
            model=chosen,
            requested_model=model,
            degraded=degraded,
            estimate=int(estimate),  # type: ignore[arg-type]
            usage=usage,
            checked=True,
        )

    def record(
        self,
        user_id: str,
        *,
        model: Optional[str] = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        plan: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Optional[Usage]:
        """Add usage without a reservation (e.g. usage reported after the fact)."""
        p = self._plan(user_id, plan)
        now = self.clock()
        period_id, resets_at = current_period(p.period, now)
        key = self._key(user_id, p, period_id)
        actual = self._units(p, model, int(input_tokens), int(output_tokens))
        res = Reservation(
            self,
            id=idempotency_key or uuid.uuid4().hex,
            key=key,
            user_id=user_id,
            plan=p,
            model=model,
            requested_model=model,
            degraded=False,
            estimate=0,
            usage=None,
            checked=False,
        )
        res.closed = True
        return self._commit(res, actual, int(input_tokens), int(output_tokens))

    def status(self, user_id: str, *, plan: Optional[str] = None) -> Usage:
        """Current usage for ``user_id`` (raises :class:`BackendError` if the store is down)."""
        p = self._plan(user_id, plan)
        now = self.clock()
        period_id, resets_at = current_period(p.period, now)
        try:
            used, reserved = self.store.get(self._key(user_id, p, period_id), now=now)
        except Exception as exc:
            raise BackendError(f"quota store unavailable: {exc}") from exc
        return self._usage(user_id, p, period_id, resets_at, used, reserved, now)

    def restore(self, data: Mapping[str, Any]) -> Reservation:
        """Rebuild a reservation from ``Reservation.to_dict()`` output."""
        if data.get("v") != 1:
            raise ValueError("unsupported reservation format")
        p = self.plans.get(data["plan"])
        if p is None:
            raise UnknownPlanError(f"unknown plan {data['plan']!r}")
        return Reservation(
            self,
            id=str(data["id"]),
            key=str(data["key"]),
            user_id=str(data["user_id"]),
            plan=p,
            model=data.get("model"),
            requested_model=data.get("requested_model"),
            degraded=bool(data.get("degraded")),
            estimate=int(data["estimate"]),
            usage=None,
            checked=bool(data.get("checked", True)),
        )

    # ---- internals

    def _plan(self, user_id: str, plan: Optional[str]) -> Plan:
        name = plan
        if name is None and self.plan_for is not None:
            name = self.plan_for(user_id)
        if name is None:
            name = self.default_plan
        if name is None:
            raise UnknownPlanError("no plan given and no default_plan configured")
        try:
            return self.plans[name]
        except KeyError:
            raise UnknownPlanError(f"unknown plan {name!r}") from None

    def _key(self, user_id: str, plan: Plan, period_id: str) -> str:
        # Usage follows the user, not the plan, so upgrading mid-month keeps
        # the tokens already spent this month.
        return f"{self.key_prefix}{user_id}|{plan.period}|{period_id}|{plan.unit}"

    @staticmethod
    def _key_ttl(resets_at: Optional[float], now: float) -> int:
        if resets_at is None:
            return 0
        return int(math.ceil(resets_at - now)) + KEY_GRACE_SECONDS

    def _units(self, plan: Plan, model: Optional[str], input_tokens: int, output_tokens: int) -> int:
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("token counts must be >= 0")
        if plan.unit == "tokens":
            return input_tokens + output_tokens
        if model is None:
            raise UnknownModelError("dollar-denominated plans need model=")
        assert self.pricing is not None
        return self.pricing.cost_micro_usd(model, input_tokens, output_tokens)

    def _estimate(
        self,
        plan: Plan,
        model: Optional[str],
        est_tokens: Optional[int],
        est_input: Optional[int],
        est_output: Optional[int],
    ) -> int:
        if est_tokens is None and est_input is None and est_output is None:
            raise ValueError("pass est_tokens= or est_input_tokens=/est_output_tokens=")
        if plan.unit == "tokens":
            total = est_tokens if est_tokens is not None else (est_input or 0) + (est_output or 0)
            if total < 0:
                raise ValueError("estimates must be >= 0")
            return int(total)
        if model is None:
            raise UnknownModelError("dollar-denominated plans need model=")
        assert self.pricing is not None
        if est_tokens is not None:
            if est_tokens < 0:
                raise ValueError("estimates must be >= 0")
            return self.pricing.worst_case_micro_usd(model, int(est_tokens))
        return self._units(plan, model, int(est_input or 0), int(est_output or 0))

    def _usage(self, user_id, plan, period_id, resets_at, used, reserved, now, rejected=False) -> Usage:
        retry = None
        if rejected and resets_at is not None:
            retry = max(1, int(math.ceil(resets_at - now)))
        return Usage(
            user_id=user_id,
            plan=plan.name,
            unit=plan.unit,
            limit=plan.limit_units,
            used=int(used),
            reserved=int(reserved),
            period_id=period_id,
            resets_at=resets_at,
            retry_after=retry,
        )

    def _period_of(self, res: Reservation) -> Tuple[str, Optional[float]]:
        # The period comes from the key, so a call that started on the 31st
        # and finished on the 1st is charged to the month it was reserved in.
        period_id = res.key.split("|")[-2]
        _, resets_at = current_period(res.plan.period, self.clock())
        if current_period(res.plan.period, self.clock())[0] != period_id:
            resets_at = self.clock()  # old period: no longer resetting in the future
        return period_id, resets_at

    def _commit(self, res: Reservation, actual: int, input_tokens, output_tokens) -> Optional[Usage]:
        now = self.clock()
        period_id, resets_at = self._period_of(res)
        try:
            used, reserved = self.store.commit(
                res.key, res.id, actual=int(actual), now=now, key_ttl=self._key_ttl(resets_at, now) or 0
            )
        except Exception as exc:
            if not self.fail_open:
                raise BackendError(f"quota store unavailable: {exc}") from exc
            log.warning("quota store unavailable, usage not recorded (fail_open=True): %s", exc)
            self._emit("commit_failed", res.user_id, res.plan, res.model, actual, error=str(exc))
            return None
        self._emit(
            "committed",
            res.user_id,
            res.plan,
            res.model,
            actual,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimate=res.estimate,
        )
        return self._usage(res.user_id, res.plan, period_id, resets_at, used, reserved, now)

    def _release(self, res: Reservation) -> Optional[Usage]:
        now = self.clock()
        period_id, resets_at = self._period_of(res)
        try:
            used, reserved = self.store.release(res.key, res.id, now=now)
        except Exception as exc:
            if not self.fail_open:
                raise BackendError(f"quota store unavailable: {exc}") from exc
            log.warning("quota store unavailable during release: %s", exc)
            return None
        self._emit("released", res.user_id, res.plan, res.model, res.estimate)
        return self._usage(res.user_id, res.plan, period_id, resets_at, used, reserved, now)

    def _emit(self, event: str, user_id: str, plan: Plan, model: Optional[str], units: Any, **extra: Any) -> None:
        if self.on_event is None:
            return
        payload = {
            "event": event,
            "ts": self.clock(),
            "user_id": user_id,
            "plan": plan.name,
            "unit": plan.unit,
            "model": model,
            "units": units,
            **extra,
        }
        try:
            self.on_event(payload)
        except Exception:  # never let a logging hook break a request
            log.exception("on_event hook raised")
