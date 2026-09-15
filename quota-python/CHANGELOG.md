# Changelog

All notable changes to this project are documented here. This project follows [Semantic Versioning](https://semver.org/); until 1.0, minor versions may include breaking changes, which will always be listed.

## [Unreleased]

## [0.1.0] - 2026-09-11

### Added
- First public release.
- `Quota.reserve()` / `Reservation.commit()` / `release()` with atomic admission.
- Token and dollar budgets per `day`, `month` or `total`.
- Degrade-to-cheaper-model before the hard limit.
- `MemoryStore` and `RedisStore` (Lua scripts, Redis Cluster compatible).
- Idempotent commits, automatic expiry of abandoned reservations, fail-open or fail-closed.
- Usage extraction for OpenAI, Anthropic and Vercel AI SDK usage objects.
