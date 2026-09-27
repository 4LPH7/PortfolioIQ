# Phase 1: A Reliable Backend Core - Discussion Log

**Date:** 2026-09-27
**Phase:** 01-a-reliable-backend-core

## Areas Discussed

### 1. API Versioning & Routing Strategy
- **Options Presented:**
  1. (Recommended) Dual-mount /api/v1 routes and keep legacy /api routes as compatibility aliases until frontend cutover
  2. Hard cutover directly to /api/v1 only, immediately updating frontend/js/api.js
- **User Selection:** Option 1 (Dual-mount `/api/v1` routes with legacy `/api` compatibility aliases).
- **Notes:** Frontend `api.js` will be updated to point to `/api/v1`, while legacy endpoints prevent breakage during deployments.

### 2. Authentication Scheme
- **Options Presented:**
  1. (Recommended) Simple header-based API key (X-API-Key) protecting all state-changing and portfolio data endpoints, with /api/health public
  2. Strict API key required on every single endpoint, including health checks
  3. Full session/JWT token auth with login endpoint immediately
- **User Selection:** Option 1 (Header-based `X-API-Key` protecting state-changing and portfolio endpoints; health/market public).
- **Notes:** Enables secure automated and frontend calls without unnecessary session state complexity.

### 3. Data Layer Architecture
- **Options Presented:**
  1. (Recommended) Repository pattern with Pydantic DTOs and centralized persistence functions (clean, fast, explicit SQL)
  2. Full SQLAlchemy 2.0 Declarative ORM models with mapped classes and session unit-of-work
- **User Selection:** Option 1 (Repository pattern with Pydantic DTOs and centralized persistence functions).
- **Notes:** Eliminates raw inline SQL from route handlers without introducing heavyweight ORM state tracking overhead.

### 4. Migration Tooling
- **Options Presented:**
  1. (Recommended) Lightweight schema_migrations tracker inside run_migrations.py (tracks applied files & checksums, keeps existing SQL migrations)
  2. Full Alembic setup with alembic.ini, env.py, and Python revision files
- **User Selection:** Option 1 (Lightweight `schema_migrations` tracker in `db/run_migrations.py`).
- **Notes:** Ensures idempotency and safety without rewriting the existing 14 SQL migration scripts.

## Deferred Ideas
- Multi-user tenant isolation — deferred to Phase 7.
- Full OAuth / user login flows — deferred to Phase 6/7.
