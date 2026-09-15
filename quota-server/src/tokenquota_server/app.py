"""FastAPI application.

Reservations are returned as signed, self-contained tokens, so the server
itself is stateless: run as many replicas as you like against one Redis.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from tokenquota import BackendError, QuotaError, QuotaExceeded, UnknownModelError, UnknownPlanError

from . import __version__
from .config import Settings

UserId = Field(min_length=1, max_length=256)


class ReserveIn(BaseModel):
    user_id: str = UserId
    plan: Optional[str] = Field(default=None, max_length=128)
    model: Optional[str] = Field(default=None, max_length=256)
    est_tokens: Optional[int] = Field(default=None, ge=0)
    est_input_tokens: Optional[int] = Field(default=None, ge=0)
    est_output_tokens: Optional[int] = Field(default=None, ge=0)
    ttl: Optional[float] = Field(default=None, gt=0, le=3600)


class CommitIn(BaseModel):
    reservation: str = Field(min_length=1, max_length=4096)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class ReleaseIn(BaseModel):
    reservation: str = Field(min_length=1, max_length=4096)


class RecordIn(BaseModel):
    user_id: str = UserId
    plan: Optional[str] = Field(default=None, max_length=128)
    model: Optional[str] = Field(default=None, max_length=256)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    idempotency_key: Optional[str] = Field(default=None, max_length=256)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def create_app(settings: Settings) -> FastAPI:
    quota = settings.quota
    app = FastAPI(title="tokenquota-server", version=__version__)

    def sign(data: Dict[str, Any]) -> str:
        payload = _b64(json.dumps(data, separators=(",", ":")).encode())
        mac = hmac.new(settings.signing_key, payload.encode(), hashlib.sha256).digest()
        return f"{payload}.{_b64(mac)}"

    def verify(token: str) -> Dict[str, Any]:
        try:
            payload, mac = token.split(".", 1)
            expected = hmac.new(settings.signing_key, payload.encode(), hashlib.sha256).digest()
            if not hmac.compare_digest(expected, _unb64(mac)):
                raise ValueError
            return json.loads(_unb64(payload))
        except Exception:
            raise HTTPException(status_code=400, detail="invalid reservation token") from None

    def auth(authorization: Optional[str] = Header(default=None)) -> None:
        if settings.api_key is None:
            return
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(token.encode(), settings.api_key.encode()):
            raise HTTPException(status_code=401, detail="missing or invalid API key")

    @app.exception_handler(QuotaExceeded)
    async def _exceeded(_: Request, exc: QuotaExceeded):
        body = {"error": "quota_exceeded", "usage": exc.usage.to_dict(), "retry_after": exc.retry_after}
        return JSONResponse(status_code=429, content=body, headers=exc.usage.headers())

    @app.exception_handler(BackendError)
    async def _backend(_: Request, exc: BackendError):
        return JSONResponse(status_code=503, content={"error": "store_unavailable", "detail": str(exc)})

    @app.exception_handler(QuotaError)
    async def _bad(_: Request, exc: QuotaError):
        code = "unknown_plan" if isinstance(exc, UnknownPlanError) else (
            "unknown_model" if isinstance(exc, UnknownModelError) else "quota_error"
        )
        return JSONResponse(status_code=400, content={"error": code, "detail": str(exc)})

    @app.exception_handler(ValueError)
    async def _value(_: Request, exc: ValueError):
        return JSONResponse(status_code=400, content={"error": "invalid_request", "detail": str(exc)})

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "version": __version__, "store": settings.store_name}

    @app.post("/v1/reserve", dependencies=[Depends(auth)])
    def reserve(body: ReserveIn):
        r = quota.reserve(
            body.user_id,
            model=body.model,
            est_tokens=body.est_tokens,
            est_input_tokens=body.est_input_tokens,
            est_output_tokens=body.est_output_tokens,
            plan=body.plan,
            ttl=body.ttl,
        )
        content = {
            "reservation": sign(r.to_dict()),
            "model": r.model,
            "requested_model": r.requested_model,
            "degraded": r.degraded,
            "checked": r.checked,
            "usage": r.usage.to_dict() if r.usage else None,
        }
        return JSONResponse(content=content, headers=r.usage.headers() if r.usage else {})

    @app.post("/v1/commit", dependencies=[Depends(auth)])
    def commit(body: CommitIn):
        r = quota.restore(verify(body.reservation))
        usage = r.commit(input_tokens=body.input_tokens, output_tokens=body.output_tokens)
        return {"recorded": usage is not None, "usage": usage.to_dict() if usage else None}

    @app.post("/v1/release", dependencies=[Depends(auth)])
    def release(body: ReleaseIn):
        usage = quota.restore(verify(body.reservation)).release()
        return {"usage": usage.to_dict() if usage else None}

    @app.post("/v1/record", dependencies=[Depends(auth)])
    def record(body: RecordIn):
        usage = quota.record(
            body.user_id,
            model=body.model,
            input_tokens=body.input_tokens,
            output_tokens=body.output_tokens,
            plan=body.plan,
            idempotency_key=body.idempotency_key,
        )
        return {"recorded": usage is not None, "usage": usage.to_dict() if usage else None}

    @app.get("/v1/usage/{user_id}", dependencies=[Depends(auth)])
    def usage(user_id: str, plan: Optional[str] = None):
        u = quota.status(user_id, plan=plan)
        return JSONResponse(content={"usage": u.to_dict()}, headers=u.headers())

    return app
