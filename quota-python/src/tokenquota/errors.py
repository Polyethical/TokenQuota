"""Exceptions raised by tokenquota."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover
    from .core import Usage


class QuotaError(Exception):
    """Base class for all tokenquota errors."""


class QuotaExceeded(QuotaError):
    """The request would push the user past their plan's hard limit."""

    def __init__(self, usage: "Usage") -> None:
        self.usage = usage
        self.retry_after: Optional[int] = usage.retry_after
        super().__init__(
            f"quota exceeded for user {usage.user_id!r} on plan {usage.plan!r} "
            f"({usage.to_dict()['used']} of {usage.to_dict()['limit']} {usage.unit} used)"
        )


class UnknownModelError(QuotaError):
    """A dollar-denominated plan was used with a model that has no price."""


class UnknownPlanError(QuotaError):
    """No plan with the requested name is configured."""


class ReservationClosed(QuotaError):
    """``commit()`` or ``release()`` was called on a finished reservation."""


class BackendError(QuotaError):
    """The storage backend failed and the quota is configured to fail closed."""
