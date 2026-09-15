# Changelog

All notable changes to this project are documented here. This project follows [Semantic Versioning](https://semver.org/); until 1.0, minor versions may include breaking changes, which will always be listed.

## [Unreleased]

## [0.1.0] - 2026-09-11

### Added
- First public release.
- `QuotaClient` with `reserve`, `record`, `usage` and `withQuota`.
- Fail-open by default for network errors; never for auth or config errors.
- `extractTokens` for Vercel AI SDK, OpenAI and Anthropic usage objects.
