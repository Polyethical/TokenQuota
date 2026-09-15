# tokenquota-server

[![CI](https://github.com/YOUR-ORG/quota-server/actions/workflows/ci.yml/badge.svg)](https://github.com/YOUR-ORG/quota-server/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

A small HTTP service that enforces per-user AI budgets for apps written in any language, including serverless and edge apps that can't hold state. It runs the [tokenquota](https://github.com/YOUR-ORG/quota-python) library against Redis.

The server is stateless: reservations come back as signed tokens, so you can run several replicas behind a load balancer against one Redis.

## Run it

```bash
cp config.example.toml config.toml        # edit your plans
export TOKENQUOTA_API_KEY=$(openssl rand -hex 24)
docker compose up
curl localhost:8080/healthz
```

Or without Docker: `pip install tokenquota-server && REDIS_URL=redis://... TOKENQUOTA_API_KEY=... tokenquota-server`.

| Environment variable | Required | Meaning |
|---|---|---|
| `TOKENQUOTA_API_KEY` | yes | Bearer token your app sends. |
| `REDIS_URL` | for production | Without it the server uses memory: one process, data lost on restart. |
| `TOKENQUOTA_CONFIG` | no | Path to the TOML config (default `config.toml`; `/config/config.toml` in Docker). |
| `TOKENQUOTA_SIGNING_KEY` | no | Secret for signing reservation tokens. Derived from the API key if unset. Set it explicitly if you rotate API keys. |
| `PORT`, `HOST`, `LOG_LEVEL` | no | Defaults `8080`, `0.0.0.0`, `INFO`. |

## API

All `/v1` endpoints need `Authorization: Bearer <TOKENQUOTA_API_KEY>`.

**`POST /v1/reserve`** `{"user_id", "plan"?, "model"?, "est_tokens"? | "est_input_tokens"? + "est_output_tokens"?, "ttl"?}`

- `200` `{"reservation": "<token>", "model": "<model to call>", "degraded": bool, "checked": bool, "usage": {...}}`
- `429` `{"error": "quota_exceeded", "usage": {...}, "retry_after": seconds}` with a `Retry-After` header

**`POST /v1/commit`** `{"reservation", "input_tokens", "output_tokens"}`. Idempotent: retries don't double-count.

**`POST /v1/release`** `{"reservation"}`. Call when the model call failed before billing.

**`POST /v1/record`** `{"user_id", "plan"?, "model"?, "input_tokens", "output_tokens", "idempotency_key"?}`. Add usage without a reservation.

**`GET /v1/usage/{user_id}?plan=`** Current usage.

Errors: `400` (`unknown_plan`, `unknown_model`, `invalid_request`), `401` (bad key), `503` (Redis unreachable and `fail_open = false`).

## Configuration

See [`config.example.toml`](config.example.toml). Plans can be token budgets (`tokens = 50_000`) or dollar budgets (`usd = 20.0`, priced from `prices_file`).

## Security notes

- Put the server on a private network or behind TLS; it's meant to be called by your backend, never by browsers.
- It stores only the user IDs you send and counters. Pass internal or hashed IDs, not emails.
- Reservation tokens are HMAC-signed, so clients can't edit the user, plan or estimate inside them.

## License

MIT
