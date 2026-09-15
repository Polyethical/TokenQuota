"""Storage backends.

Every backend makes the check-and-reserve step atomic, so two concurrent
requests can never both squeeze into the last bit of a user's budget.

* ``MemoryStore`` - one process only (tests, local development, single-worker apps).
* ``RedisStore`` - shared across processes and machines. Each operation is a
  single Lua script, which Redis runs atomically.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol, Tuple

REJECT, PRIMARY, FALLBACK = 0, 1, 2

# How long a reservation id is remembered after commit, so a retried commit
# (for example after a network timeout) is not counted twice.
DEDUPE_SECONDS = 24 * 3600


def decide(
    used: int, reserved: int, limit: int, soft_limit: int, est_primary: int, est_fallback: Optional[int]
) -> int:
    """Shared admission rule. ``RedisStore`` implements the same rule in Lua."""
    base = used + reserved
    if base + est_primary <= soft_limit:
        return PRIMARY
    if est_fallback is not None:
        return FALLBACK if base + est_fallback <= limit else REJECT
    return PRIMARY if base + est_primary <= limit else REJECT


class Store(Protocol):
    def reserve(
        self,
        key: str,
        res_id: str,
        *,
        limit: int,
        soft_limit: int,
        est_primary: int,
        est_fallback: Optional[int],
        expires_at: float,
        now: float,
        key_ttl: int,
    ) -> Tuple[int, int, int]:
        """Return ``(decision, used, reserved)``; ``reserved`` includes this reservation."""

    def commit(self, key: str, res_id: str, *, actual: int, now: float, key_ttl: int) -> Tuple[int, int]:
        """Close a reservation (if still open) and add ``actual`` to usage. Idempotent per ``res_id``."""

    def release(self, key: str, res_id: str, *, now: float) -> Tuple[int, int]:
        """Cancel a reservation without recording usage."""

    def get(self, key: str, *, now: float) -> Tuple[int, int]:
        """Return ``(used, reserved)``."""


# --------------------------------------------------------------------------- memory


@dataclass
class _Entry:
    used: int = 0
    reservations: Dict[str, Tuple[int, float]] = field(default_factory=dict)
    committed: Dict[str, float] = field(default_factory=dict)
    expires_at: Optional[float] = None


class MemoryStore:
    """Thread-safe in-process store. State is lost when the process exits."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: Dict[str, _Entry] = {}

    def _entry(self, key: str, now: float) -> _Entry:
        entry = self._data.get(key)
        if entry is None or (entry.expires_at is not None and now >= entry.expires_at):
            entry = _Entry()
            self._data[key] = entry
        expired = [rid for rid, (_, exp) in entry.reservations.items() if exp <= now]
        for rid in expired:
            del entry.reservations[rid]
        return entry

    @staticmethod
    def _touch(entry: _Entry, now: float, key_ttl: int) -> None:
        entry.expires_at = now + key_ttl if key_ttl > 0 else None

    @staticmethod
    def _reserved(entry: _Entry) -> int:
        return sum(amount for amount, _ in entry.reservations.values())

    def reserve(self, key, res_id, *, limit, soft_limit, est_primary, est_fallback, expires_at, now, key_ttl):
        with self._lock:
            entry = self._entry(key, now)
            reserved = self._reserved(entry)
            decision = decide(entry.used, reserved, limit, soft_limit, est_primary, est_fallback)
            if decision != REJECT:
                amount = est_fallback if decision == FALLBACK else est_primary
                entry.reservations[res_id] = (amount, expires_at)
                reserved += amount
                self._touch(entry, now, key_ttl)
            return decision, entry.used, reserved

    def commit(self, key, res_id, *, actual, now, key_ttl):
        with self._lock:
            entry = self._entry(key, now)
            entry.reservations.pop(res_id, None)
            if len(entry.committed) > 10_000:
                entry.committed = {k: v for k, v in entry.committed.items() if v > now}
            if entry.committed.get(res_id, 0) <= now:
                entry.used += actual
                entry.committed[res_id] = now + DEDUPE_SECONDS
            self._touch(entry, now, key_ttl)
            return entry.used, self._reserved(entry)

    def release(self, key, res_id, *, now):
        with self._lock:
            entry = self._entry(key, now)
            entry.reservations.pop(res_id, None)
            return entry.used, self._reserved(entry)

    def get(self, key, *, now):
        with self._lock:
            entry = self._entry(key, now)
            return entry.used, self._reserved(entry)


# --------------------------------------------------------------------------- redis

_PURGE = """
local expired = redis.call('ZRANGEBYSCORE', KEYS[3], '-inf', ARGV[1])
for _, rid in ipairs(expired) do redis.call('HDEL', KEYS[2], rid) end
if #expired > 0 then redis.call('ZREMRANGEBYSCORE', KEYS[3], '-inf', ARGV[1]) end
local used = tonumber(redis.call('GET', KEYS[1]) or '0')
local reserved = 0
for _, v in ipairs(redis.call('HVALS', KEYS[2])) do reserved = reserved + tonumber(v) end
"""

# KEYS: used, reservations(hash id->amount), expiries(zset id->ts)
# ARGV: now, res_id, limit, soft_limit, est_primary, est_fallback(-1 = none), expires_at, key_ttl
_RESERVE = _PURGE + """
local limit = tonumber(ARGV[3])
local soft = tonumber(ARGV[4])
local est_p = tonumber(ARGV[5])
local est_f = tonumber(ARGV[6])
local base = used + reserved
local decision = 0
local amount = nil
if base + est_p <= soft then
  decision = 1; amount = ARGV[5]
elseif est_f >= 0 then
  if base + est_f <= limit then decision = 2; amount = ARGV[6] end
elseif base + est_p <= limit then
  decision = 1; amount = ARGV[5]
end
if decision > 0 then
  redis.call('HSET', KEYS[2], ARGV[2], amount)
  redis.call('ZADD', KEYS[3], ARGV[7], ARGV[2])
  reserved = reserved + tonumber(amount)
  local ttl = tonumber(ARGV[8])
  if ttl > 0 then
    redis.call('EXPIRE', KEYS[2], ttl)
    redis.call('EXPIRE', KEYS[3], ttl)
  end
end
return {decision, used, reserved}
"""

# KEYS: used, reservations, expiries, dedupe marker for this reservation id
# ARGV: now, res_id, actual, key_ttl, dedupe_seconds
_COMMIT = """
redis.call('HDEL', KEYS[2], ARGV[2])
redis.call('ZREM', KEYS[3], ARGV[2])
if redis.call('SET', KEYS[4], '1', 'NX', 'EX', ARGV[5]) then
  redis.call('INCRBY', KEYS[1], ARGV[3])
end
local ttl = tonumber(ARGV[4])
if ttl > 0 then
  redis.call('EXPIRE', KEYS[1], ttl)
else
  redis.call('PERSIST', KEYS[1])
end
""" + _PURGE + """
return {used, reserved}
"""

# KEYS: used, reservations, expiries    ARGV: now, res_id
_RELEASE = """
redis.call('HDEL', KEYS[2], ARGV[2])
redis.call('ZREM', KEYS[3], ARGV[2])
""" + _PURGE + """
return {used, reserved}
"""

_GET = _PURGE + """
return {used, reserved}
"""


class RedisStore:
    """Redis-backed store, safe across processes and machines.

    Works with Redis 6+ and Redis Cluster: all keys for one user and period
    share a hash tag, so every script touches a single slot.
    """

    def __init__(self, client=None, *, url: Optional[str] = None, prefix: str = "tq") -> None:
        if client is None:
            try:
                import redis
            except ImportError as exc:  # pragma: no cover
                raise ImportError("RedisStore needs the redis package: pip install 'tokenquota[redis]'") from exc
            client = redis.Redis.from_url(url or "redis://localhost:6379/0")
        self.client = client
        self.prefix = prefix
        self._reserve = client.register_script(_RESERVE)
        self._commit = client.register_script(_COMMIT)
        self._release = client.register_script(_RELEASE)
        self._get = client.register_script(_GET)

    def _keys(self, key: str) -> List[str]:
        base = f"{self.prefix}:{{{key}}}"
        return [f"{base}:used", f"{base}:res", f"{base}:exp"]

    def reserve(self, key, res_id, *, limit, soft_limit, est_primary, est_fallback, expires_at, now, key_ttl):
        result = self._reserve(
            keys=self._keys(key),
            args=[
                repr(float(now)),
                res_id,
                int(limit),
                int(soft_limit),
                int(est_primary),
                -1 if est_fallback is None else int(est_fallback),
                repr(float(expires_at)),
                int(key_ttl),
            ],
        )
        return int(result[0]), int(result[1]), int(result[2])

    def commit(self, key, res_id, *, actual, now, key_ttl):
        keys = self._keys(key) + [f"{self.prefix}:{{{key}}}:done:{res_id}"]
        result = self._commit(
            keys=keys, args=[repr(float(now)), res_id, int(actual), int(key_ttl), DEDUPE_SECONDS]
        )
        return int(result[0]), int(result[1])

    def release(self, key, res_id, *, now):
        result = self._release(keys=self._keys(key), args=[repr(float(now)), res_id])
        return int(result[0]), int(result[1])

    def get(self, key, *, now):
        result = self._get(keys=self._keys(key), args=[repr(float(now))])
        return int(result[0]), int(result[1])
