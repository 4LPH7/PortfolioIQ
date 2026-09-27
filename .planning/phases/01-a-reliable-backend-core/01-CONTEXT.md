# Phase 1: A Reliable Backend Core - Context

**Gathered:** 2026-09-27
**Status:** Ready for planning

<domain>
## Phase Boundary

Transform the existing Flask prototype backend into an idempotent, typed, structured, and authenticated service layer:
- Idempotent migration version tracking (`schema_migrations`)
- Typed Pydantic DTOs and centralized repository functions replacing ad-hoc SQL
- Structured request/response validation with uniform error payloads (`{"error": {"code": ..., "message": ...}}`)
- API key authentication on state-changing & portfolio endpoints
- Explicit `/api/v1` API versioning with dual-mounted legacy compatibility
- Structured logging with correlation IDs and basic rate limiting on mutations

</domain>

<decisions>
## Implementation Decisions

### API Versioning & Routing Strategy
- **D-01:** Dual-mount all endpoints under `/api/v1` prefix (e.g., `/api/v1/portfolio/summary`, `/api/v1/settings/sync`) while maintaining legacy `/api/...` routes as compatibility aliases.
- **D-02:** Update `frontend/js/api.js` to target `/api/v1` by default.

### Authentication & Authorization
- **D-03:** Enforce simple header-based API key authentication (`X-API-Key`) validated against `PORTFOLIOIQ_API_KEY` (or `API_SECRET_KEY`) from environment settings.
- **D-04:** Protect all mutation endpoints (sync, rebalancing, orders, settings updates) and sensitive portfolio valuation data.
- **D-05:** Keep `/api/health`, `/api/v1/health`, and `/api/market/status` unauthenticated for public pinging and deployment checks.

### Data Layer & Persistence Pattern
- **D-06:** Implement repository pattern with typed Pydantic DTOs for data transfer objects (e.g. `HoldingDTO`, `OrderAttemptDTO`, `ValidationCheckDTO`).
- **D-07:** Centralize persistence functions in `src/db/repository.py` using parameterized SQLAlchemy Core queries (`record_order_attempt`, `record_validation_check`, `record_broker_execution`, `get_current_holdings`).
- **D-08:** Prohibit raw inline SQL in Flask route handlers or analytics business logic.

### Migration Tooling
- **D-09:** Implement a lightweight `schema_migrations` tracking table directly inside `db/run_migrations.py`.
- **D-10:** Track migration filename, SHA-256 checksum, and execution timestamp; execute only unapplied `.sql` scripts in transactional batches. Preserve existing numbered SQL migrations in `db/migrations/`.

### Error Handling & Reliability
- **D-11:** Enforce a uniform error envelope: `{"error": {"code": "<ERROR_CODE>", "message": "<Human description>", "details": {...}}}` with proper HTTP 4xx/5xx status codes.
- **D-12:** Integrate request/correlation IDs using `loguru` contextvars for end-to-end traceability.
- **D-13:** Apply basic rate limiting (`Flask-Limiter`) on mutation and broker-interaction endpoints.

### the agent's Discretion
- Exact naming and organization of Pydantic DTO models in `src/models/`.
- Correlation ID generation scheme (`uuid4().hex[:12]`).
- Default rate limits (e.g., 10 req/min for sync/orders, 60 req/min for read).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Schema & Database
- `supabase_schema.sql` — Canonical table schema and constraints (source of truth).
- `db/run_migrations.py` — Current migration runner to be upgraded with `schema_migrations`.
- `db/migrations/*.sql` — 14 baseline migration scripts.

### Application & Safety
- `flask_app.py` — Current route handlers to be migrated to `/api/v1` and repository calls.
- `src/config/settings.py` — Pydantic application settings (add `PORTFOLIOIQ_API_KEY`, rate limit settings).
- `src/execution/gatekeeper.py` — Gatekeeper requiring persistence integration.
- `src/execution/order_router.py` — Order router requiring persistence integration.
- `frontend/js/api.js` — Client API wrapper to update to `/api/v1` and attach `X-API-Key`.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/db/connection.py`: `get_db_session()` and `execute_sql()` already exist with connection pooling; can back repository functions.
- `src/config/settings.py`: Pydantic `BaseSettings` setup already exists.
- `loguru`: Already configured in `src/` modules.

### Established Patterns
- Fail-closed `DRY_RUN_MODE` (from Phase 0).
- Explicit `ALLOWED_ORIGINS` CORS check in `flask_app.py`.

### Integration Points
- `flask_app.py`: Route definitions, error handlers, authentication decorator.
- `src/db/repository.py`: Centralized persistence layer.
- `db/run_migrations.py`: Schema migration runner with tracking table.

</code_context>

<deferred>
## Deferred Ideas

- Full multi-user tenant isolation & Row-Level Security — Phase 7.
- Full OAuth / JWT session management with refresh tokens — Phase 6 / Phase 7.
- Automated OpenAPI / Swagger documentation UI — Backlog.

</deferred>

---

*Phase: 01-a-reliable-backend-core*
*Context gathered: 2026-09-27*
