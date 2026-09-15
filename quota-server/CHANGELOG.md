# Changelog

All notable changes to this project are documented here. This project follows [Semantic Versioning](https://semver.org/); until 1.0, minor versions may include breaking changes, which will always be listed.

## [Unreleased]

## [0.1.0] - 2026-09-11

### Added
- First public release.
- HTTP API: `/v1/reserve`, `/v1/commit`, `/v1/release`, `/v1/record`, `/v1/usage/{user_id}`.
- Signed, stateless reservation tokens; bearer-token auth.
- Docker image and docker-compose setup with Redis.
