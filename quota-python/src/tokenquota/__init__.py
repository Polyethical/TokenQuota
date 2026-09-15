"""Per-user token and dollar budgets for AI apps, enforced before the model call."""

from .core import Plan, Quota, Reservation, Usage, extract_tokens
from .errors import (
    BackendError,
    QuotaError,
    QuotaExceeded,
    ReservationClosed,
    UnknownModelError,
    UnknownPlanError,
)
from .pricing import ModelPrice, Pricing
from .stores import MemoryStore, RedisStore

__version__ = "0.1.0"

__all__ = [
    "BackendError",
    "MemoryStore",
    "ModelPrice",
    "Plan",
    "Pricing",
    "Quota",
    "QuotaError",
    "QuotaExceeded",
    "RedisStore",
    "Reservation",
    "ReservationClosed",
    "UnknownModelError",
    "UnknownPlanError",
    "Usage",
    "extract_tokens",
    "__version__",
]
