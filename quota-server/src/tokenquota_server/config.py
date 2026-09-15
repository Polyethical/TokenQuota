"""Load plans and settings from a TOML file plus environment variables."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from tokenquota import MemoryStore, Plan, Pricing, Quota, RedisStore

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

log = logging.getLogger("tokenquota.server")


@dataclass
class Settings:
    quota: Quota
    api_key: Optional[str]
    signing_key: bytes
    store_name: str


def load_quota(config_path: Path, store) -> Quota:
    data = tomllib.loads(config_path.read_text())
    server = data.get("server", {})
    plans = []
    for name, spec in data.get("plans", {}).items():
        plans.append(
            Plan(
                name=name,
                tokens=spec.get("tokens"),
                usd=spec.get("usd"),
                period=spec.get("period", "month"),
                degrade_at=spec.get("degrade_at", 0.8),
                degrade_to=spec.get("degrade_to", {}),
            )
        )
    if not plans:
        raise ValueError(f"{config_path}: define at least one [plans.<name>] table")
    pricing = None
    if server.get("prices_file"):
        prices_path = Path(server["prices_file"])
        if not prices_path.is_absolute():
            prices_path = config_path.parent / prices_path
        pricing = Pricing.from_file(prices_path)
    return Quota(
        plans,
        store=store,
        pricing=pricing,
        default_plan=server.get("default_plan"),
        fail_open=bool(server.get("fail_open", True)),
        reservation_ttl=float(server.get("reservation_ttl", 120)),
    )


def settings_from_env() -> Settings:
    config_path = Path(os.environ.get("TOKENQUOTA_CONFIG", "config.toml"))
    redis_url = os.environ.get("REDIS_URL")
    if redis_url:
        store, store_name = RedisStore(url=redis_url, prefix=os.environ.get("TOKENQUOTA_PREFIX", "tq")), "redis"
    else:
        log.warning("REDIS_URL not set: using in-memory store (single process only, data lost on restart)")
        store, store_name = MemoryStore(), "memory"

    api_key = os.environ.get("TOKENQUOTA_API_KEY")
    if not api_key:
        if os.environ.get("TOKENQUOTA_INSECURE_NO_AUTH") != "1":
            raise SystemExit("set TOKENQUOTA_API_KEY (or TOKENQUOTA_INSECURE_NO_AUTH=1 for local testing only)")
        log.warning("authentication disabled (TOKENQUOTA_INSECURE_NO_AUTH=1)")
        api_key = None
    signing = os.environ.get("TOKENQUOTA_SIGNING_KEY")
    if signing:
        signing_key = signing.encode()
    elif api_key:
        signing_key = hmac.new(api_key.encode(), b"tokenquota-reservation-signing", hashlib.sha256).digest()
    else:
        signing_key = os.urandom(32)
    return Settings(load_quota(config_path, store), api_key, signing_key, store_name)
