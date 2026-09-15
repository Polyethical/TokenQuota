"""Budget periods. All periods are calendar-based and use UTC."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

PERIODS = ("day", "month", "total")


def current_period(period: str, now: float) -> Tuple[str, Optional[float]]:
    """Return ``(period_id, resets_at)`` for ``period`` at epoch time ``now``.

    ``resets_at`` is an epoch timestamp, or ``None`` for the ``"total"`` period,
    which never resets.
    """
    dt = datetime.fromtimestamp(now, tz=timezone.utc)
    if period == "day":
        start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        return start.strftime("%Y-%m-%d"), (start + timedelta(days=1)).timestamp()
    if period == "month":
        start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start.month == 12:
            nxt = start.replace(year=start.year + 1, month=1)
        else:
            nxt = start.replace(month=start.month + 1)
        return start.strftime("%Y-%m"), nxt.timestamp()
    if period == "total":
        return "total", None
    raise ValueError(f"unknown period {period!r}; expected one of {PERIODS}")
