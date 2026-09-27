---
phase: 01-a-reliable-backend-core
plan: 03
subsystem: api-v1-core
tags: [api-v1, blueprint, auth, rate-limiting, error-envelope, correlation-id, flask-limiter]
provides:
  - Dual-mounted API Blueprints under /api/v1 (primary) and /api (legacy alias)
  - Timing-safe API key authentication via @require_api_key checking X-API-Key with hmac.compare_digest
  - Exempt unauthenticated health and NSE market status endpoints for Render checks and frontend UI
  - Uniform error envelopes: {"ok": false, "error": {"code": ..., "message": ..., "details": ...}}
  - Loguru request correlation ID tracing with X-Request-ID propagation
  - Memory-backed rate limiting via Flask-Limiter with configurable mutation limits
  - Modernized frontend API client targeting /api/v1 with automated request tracing
  - Comprehensive integration test suite in tests/test_api_v1.py (12 passing tests)
affects: [02-testing-ci-cd, frontend]
actuals:
  tokens: 4200
  tasks: 4
  commits: 1
tech-stack:
  added: [flask-limiter]
  patterns: [dual-mount-blueprints, timing-safe-auth, uniform-error-envelope, correlation-id-tracing]
key-files:
  created:
    - src/api/__init__.py
    - src/api/middleware.py
    - src/api/limiter.py
    - src/api/v1/__init__.py
    - src/api/v1/blueprint.py
    - tests/test_api_v1.py
  modified:
    - flask_app.py
    - frontend/js/api.js
    - requirements.txt
    - src/config/settings.py
    - src/db/repository.py
    - src/models/dtos.py
key-decisions:
  - "Dual-mount api_v1_bp under both /api/v1 and /api so that legacy endpoints continue functioning without breakage while new endpoints target versioned paths."
  - "Bypass CORS OPTIONS preflight requests in before_request hook returning 204 No Content immediately without requiring auth, preserving browser preflight."
  - "Centralize operational database statistics and audit log queries in src/db/repository.py, ensuring no HTTP route handler executes raw SQL directly."
duration: 25min
completed: 2026-09-27
status: complete
---

# Plan 01-03: API v1 Blueprint, Authentication & Uniform Errors Summary

**Implemented the API v1 Blueprint with dual-mounting, timing-safe API key authentication (`@require_api_key`), uniform error envelopes, Loguru correlation tracking, `Flask-Limiter` rate limiting, and updated the frontend API client.**

## Performance
- **Tasks:** 4 completed
- **Tests passing:** 12/12 API v1 tests passed
- **Full suite status:** 63 passed, 1 skipped in 3.50s

## Accomplishments
- **API Security & Auth:** Created `src/api/middleware.py` with `@require_api_key` enforcing timing-safe `X-API-Key` verification via `hmac.compare_digest`, while keeping `/health` and `/market/status` public for Render probes and UI status pills.
- **Dual-Mount Blueprint:** Implemented `src/api/v1/blueprint.py` and registered it under `/api/v1` (primary) and `/api` (legacy alias) in `flask_app.py`, completely decoupling routes from raw SQL queries.
- **Uniform Error Envelope:** Standardized all error responses across 400, 401, 403, 404, 405, 422, 429, and 500 into `{"ok": false, "error": {"code": str, "message": str, "details": any}}`.
- **Request Tracing:** Integrated `X-Request-ID` generation and contextvar propagation through Loguru and outgoing HTTP headers.
- **Rate Limiting:** Added `Flask-Limiter` with in-memory storage, configuring 120 req/min default and 10 req/min for mutation endpoints, with testing-mode bypass.
- **Frontend Modernization:** Updated `frontend/js/api.js` to target `/api/v1`, include `X-API-Key` and `X-Request-ID`, and cleanly parse uniform error messages.
- **Test Coverage:** Added `tests/test_api_v1.py` covering health, market status, 401 rejection, valid key authorization, legacy aliasing, CORS preflight 204, request ID tracing, 404 formatting, and Pydantic validation.

## Task Commits
1. **Plan 01-03: API v1 Blueprint, auth, rate limiting, and client updates** - `head`

## Phase 1 Milestone Status
All three plans in **Phase 1: A Reliable Backend Core** are now complete:
- [x] **01-01**: Idempotent Migration Runner (`db/run_migrations.py` with `schema_migrations` and checksums)
- [x] **01-02**: Data Layer & Typed Repository (`src/models/dtos.py` & `src/db/repository.py`)
- [x] **01-03**: API v1 Blueprint & Auth (`src/api/` middleware, blueprint, rate limiting, and tests)
