import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tokenquota import MemoryStore
from tokenquota_server.app import create_app
from tokenquota_server.config import Settings, load_quota

ROOT = Path(__file__).resolve().parents[1]
AUTH = {"Authorization": "Bearer test-key"}


@pytest.fixture
def client():
    quota = load_quota(ROOT / "config.example.toml", MemoryStore())
    app = create_app(Settings(quota, "test-key", b"signing-key", "memory"))
    return TestClient(app)


def test_health_needs_no_auth(client):
    assert client.get("/healthz").json()["ok"] is True


def test_auth_required(client):
    assert client.post("/v1/reserve", json={"user_id": "u", "est_tokens": 1}).status_code == 401
    bad = {"Authorization": "Bearer nope"}
    assert client.post("/v1/reserve", json={"user_id": "u", "est_tokens": 1}, headers=bad).status_code == 401


def test_full_flow_tokens_plan(client):
    r = client.post("/v1/reserve", json={"user_id": "u1", "model": "model-large", "est_tokens": 30_000}, headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["model"] == "model-large" and body["degraded"] is False
    assert r.headers["X-Quota-Limit"] == "50000"

    c = client.post("/v1/commit", json={"reservation": body["reservation"], "input_tokens": 20_000, "output_tokens": 15_000}, headers=AUTH)
    assert c.json()["usage"]["used"] == 35_000

    # retrying the same commit does not double count
    c2 = client.post("/v1/commit", json={"reservation": body["reservation"], "input_tokens": 20_000, "output_tokens": 15_000}, headers=AUTH)
    assert c2.json()["usage"]["used"] == 35_000

    r = client.post("/v1/reserve", json={"user_id": "u1", "model": "model-large", "est_tokens": 10_000}, headers=AUTH)
    assert r.json()["model"] == "model-small" and r.json()["degraded"] is True
    client.post("/v1/release", json={"reservation": r.json()["reservation"]}, headers=AUTH)

    r = client.post("/v1/reserve", json={"user_id": "u1", "model": "model-large", "est_tokens": 20_000}, headers=AUTH)
    assert r.status_code == 429
    assert r.json()["error"] == "quota_exceeded" and int(r.headers["Retry-After"]) > 0


def test_usd_plan(client):
    r = client.post(
        "/v1/reserve",
        json={"user_id": "u2", "plan": "pro", "model": "model-large", "est_input_tokens": 1000, "est_output_tokens": 1000},
        headers=AUTH,
    )
    assert r.status_code == 200 and r.json()["usage"]["unit"] == "usd"
    c = client.post("/v1/commit", json={"reservation": r.json()["reservation"], "input_tokens": 1000, "output_tokens": 1000}, headers=AUTH)
    assert c.json()["usage"]["used"] == pytest.approx(0.018)


def test_tampered_token_rejected(client):
    r = client.post("/v1/reserve", json={"user_id": "u3", "est_tokens": 10}, headers=AUTH).json()
    payload, mac = r["reservation"].split(".")
    import base64

    data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    data["user_id"] = "someone-else"
    forged = base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode() + "." + mac
    assert client.post("/v1/commit", json={"reservation": forged, "input_tokens": 1}, headers=AUTH).status_code == 400


def test_record_and_usage(client):
    client.post("/v1/record", json={"user_id": "u4", "input_tokens": 100, "output_tokens": 50, "idempotency_key": "e1"}, headers=AUTH)
    client.post("/v1/record", json={"user_id": "u4", "input_tokens": 100, "output_tokens": 50, "idempotency_key": "e1"}, headers=AUTH)
    u = client.get("/v1/usage/u4", headers=AUTH).json()["usage"]
    assert u["used"] == 150 and u["remaining"] == 49_850


def test_bad_requests(client):
    assert client.post("/v1/reserve", json={"user_id": "u", "est_tokens": 1, "plan": "nope"}, headers=AUTH).json()["error"] == "unknown_plan"
    assert client.post("/v1/reserve", json={"user_id": "u", "plan": "pro", "model": "mystery", "est_tokens": 1}, headers=AUTH).json()["error"] == "unknown_model"
    assert client.post("/v1/reserve", json={"user_id": "u"}, headers=AUTH).json()["error"] == "invalid_request"
    assert client.post("/v1/reserve", json={"user_id": "", "est_tokens": 1}, headers=AUTH).status_code == 422
