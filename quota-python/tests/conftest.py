import os
import uuid

import pytest

from tokenquota import MemoryStore, RedisStore

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/15")


def _redis_available():
    try:
        import redis

        redis.Redis.from_url(REDIS_URL, socket_connect_timeout=0.5).ping()
        return True
    except Exception:
        return False


HAVE_REDIS = _redis_available()


@pytest.fixture(params=["memory", "redis"])
def store(request):
    if request.param == "memory":
        yield MemoryStore()
        return
    if not HAVE_REDIS:
        pytest.skip("Redis not available (set REDIS_URL)")
    import redis

    client = redis.Redis.from_url(REDIS_URL)
    prefix = f"test-{uuid.uuid4().hex[:8]}"
    yield RedisStore(client, prefix=prefix)
    for key in client.scan_iter(f"{prefix}:*"):
        client.delete(key)


class Clock:
    def __init__(self, t=1_788_000_000.0):  # 2026-08-29 UTC
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture
def clock():
    return Clock()
