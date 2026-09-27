# Phase 1: A Reliable Backend Core - Research

**Researched:** 2026-09-27
**Domain:** Database Migrations, Persistence Layer (Repository Pattern), Flask REST API Architecture & Auth
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01 / D-02 (API Versioning):** Dual-mount all endpoints under `/api/v1` while preserving legacy `/api/...` routes as compatibility aliases. Update `frontend/js/api.js` to target `/api/v1` as canonical.
- **D-03 – D-05 (Authentication):** Header-based API key authentication (`X-API-Key`) validated against `PORTFOLIOIQ_API_KEY` for all mutation endpoints (sync, rebalancing, orders, settings) and sensitive portfolio data. Keep `/api/health`, `/api/v1/health`, and `/api/market/status` unauthenticated for health probes and deployment checks.
- **D-06 – D-08 (Data Layer):** Repository pattern with typed Pydantic DTOs (`HoldingDTO`, `OrderAttemptDTO`, etc.) and centralized persistence functions in `src/db/repository.py` using parameterized SQLAlchemy Core queries. Prohibit raw inline SQL in route handlers and business logic.
- **D-09 – D-10 (Migration Tooling):** Add an idempotent `schema_migrations` tracking table inside `db/run_migrations.py` that records migration filename, execution timestamp, and SHA-256 checksums, executing only unapplied scripts in transaction blocks.
- **D-11 – D-13 (Error Handling & Reliability):** Standardize error payloads to `{"ok": false, "error": {"code": ..., "message": ..., "details": ...}}`, attach correlation IDs via `loguru` contextvars, and add rate limiting (`Flask-Limiter`) on mutation endpoints.

### the agent's Discretion
- Organization of Pydantic DTO models in `src/models/dtos.py`.
- Correlation ID generation scheme (`uuid4().hex[:12]`).
- Default rate limit thresholds (e.g., 10 req/min for mutations, 120 req/min default).

### Deferred Ideas (OUT OF SCOPE)
- Full multi-user tenant isolation & Row-Level Security — Phase 7.
- Full OAuth / JWT session management with refresh tokens — Phase 6 / Phase 7.
- Automated OpenAPI / Swagger documentation UI — Backlog.
</user_constraints>

<architectural_responsibility_map>
## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|---|---|---|---|
| Migration Version Tracking | Database/Storage | Deployment Scripts | Table `schema_migrations` stores immutable applied records; `db/run_migrations.py` coordinates verification |
| Data Transfer Objects (DTOs) | API/Backend | Database/Storage | `src/models/dtos.py` defines typed entity contracts across service boundaries |
| Centralized Persistence | Database/Storage | API/Backend | `src/db/repository.py` isolates SQL queries from HTTP handlers and analytics |
| Request Authentication & CORS | API/Backend | CDN/Static | `src/api/middleware.py` enforces `X-API-Key` and permits browser preflight |
| Endpoint Routing & Error Envelope | API/Backend | Client/Frontend | `src/api/v1/` routes dual-mounted in Flask; handles 4xx/5xx uniformly |
| Frontend API Client | Client/Frontend | API/Backend | `frontend/js/api.js` sends `X-API-Key` and requests `/api/v1` routes |
</architectural_responsibility_map>

<research_summary>
## Summary

Phase 1 establishes a predictable, typed, tested, and secure backend foundation. Code inspection reveals that SQL queries are currently embedded directly inside Flask route handlers, `gatekeeper.py`, and `order_router.py`. Furthermore, `db/run_migrations.py` re-runs all 14 migration files on every invocation with zero execution tracking or checksum validation.

Crucially, migration `009_audit_immutability.sql` enforces a Postgres `BEFORE UPDATE OR DELETE` trigger on `order_audit_trail` and `order_validation_log`. Any attempt to run an `UPDATE` statement on these tables throws an unrecoverable database exception. Therefore, our repository design strictly models `order_audit_trail` as an **append-only event log**.

The Flask layer will be restructured using Blueprint dual-mounting (`/api/v1` as canonical, `/api` as legacy alias), timing-safe API key authentication (`hmac.compare_digest`), structured Loguru correlation ID logging via contextvars, Pydantic request validation decorators, and `Flask-Limiter` rate limiting.
</research_summary>

<standard_stack>
## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---|---|---|---|
| `pydantic` | `>=2.10.3` | Typed DTOs and request validation | Industry standard for Python type validation, already in project dependencies |
| `sqlalchemy` | `>=2.0.36` | Parameterized Core SQL queries | Connection pooling, SQL injection prevention, transaction management |
| `flask` | `>=3.1.3` | REST API framework & Blueprints | Lightweight web framework powering the backend |
| `flask-limiter` | `>=3.8.0` | IP-based rate limiting | Protects write & broker execution endpoints from abuse |
| `loguru` | `>=0.7.3` | Structured logging with contextvars | Zero-boilerplate contextual logging with correlation IDs |

### Supporting
| Library | Version | Purpose | When to Use |
|---|---|---|---|
| `psycopg2-binary` | `>=2.9.10` | Low-level Postgres driver | Direct connection for `db/run_migrations.py` |
| `hmac` / `hashlib` | stdlib | Timing-safe auth & SHA-256 checksums | Cryptographic verification without external dependencies |

**New Dependency to Add:**
`flask-limiter>=3.8.0` in `requirements.txt`.
</standard_stack>

<architecture_patterns>
## Architecture Patterns

### Component Architecture & Data Flow

```
[Browser / Netlify Frontend]
       │
       ▼ (HTTP GET/POST + X-API-Key + X-Request-ID)
[Flask App Entry (flask_app.py)]
       │
       ├─► [CORS & Correlation ID Middleware] (Sets request-scoped Loguru context)
       ├─► [Auth Decorator (@require_api_key)] (Exempts /health & preflight OPTIONS)
       ├─► [Rate Limiter (Flask-Limiter)] (10/min on mutations)
       │
       ▼
[API v1 Blueprint (src/api/v1/)]
       │
       ├─► Validates Request via Pydantic Schemas
       │
       ▼
[Domain Services (Analytics / Rebalancer / Gatekeeper)]
       │
       ▼
[Repository Layer (src/db/repository.py)]
       │ (Executes parameterized SQL via get_db_session())
       ▼
[PostgreSQL Database (Supabase / Local)]
```

### Append-Only Immutability Pattern
Because of the trigger in `009_audit_immutability.sql`:
1. Order validation is created via `record_order_attempt()` -> inserts row with `validation_status='APPROVED'/'BLOCKED'`, `broker_status='NOT_SENT'`.
2. Broker outcome is recorded via `record_broker_execution()` -> inserts a **new** row sharing the same `internal_order_id` with `broker_status='OPEN'/'REJECTED'/'NOT_SENT'` and `kite_order_id`.
3. Never issue SQL `UPDATE` on `order_audit_trail`.
</architecture_patterns>

<implementation_details>
## Implementation Details

### 1. Schema Migrations Runner (`db/run_migrations.py`)
- **Table DDL:**
  ```sql
  CREATE TABLE IF NOT EXISTS schema_migrations (
      version VARCHAR(255) PRIMARY KEY,
      checksum_sha256 VARCHAR(64) NOT NULL,
      executed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
  );
  ```
- **Checksum Calculation:**
  Must normalize CRLF (`\r\n`) to LF (`\n`) before computing SHA-256 so checksums remain identical between Windows and Linux/Render:
  ```python
  def calculate_checksum(path: Path) -> str:
      content = path.read_text(encoding="utf-8").replace("\r\n", "\n")
      return hashlib.sha256(content.encode("utf-8")).hexdigest()
  ```
- **Execution Lifecycle:**
  1. Ensure `schema_migrations` table exists.
  2. Query existing rows into a dictionary `{version: checksum_sha256}`.
  3. Scan `db/migrations/*.sql` sorted by filename.
  4. If already applied: compare checksum; if altered, raise `MigrationTamperedError`; if identical, skip.
  5. If unapplied: execute in an explicit transaction (`conn.autocommit = False`), record row in `schema_migrations`, commit. Rollback and abort on failure.

### 2. Typed Repository (`src/db/repository.py`) & DTOs (`src/models/dtos.py`)
- DTOs defined with Pydantic v2 `BaseModel` and `model_config = ConfigDict(from_attributes=True)`.
- Core functions:
  - `record_order_attempt(...) -> OrderAttemptDTO`
  - `record_validation_check(...) -> ValidationCheckDTO`
  - `record_broker_execution(...) -> BrokerExecutionDTO`
  - `get_current_holdings(user_id: str, active_only: bool = True) -> list[HoldingDTO]`
  - `get_app_config(key: str) -> AppConfigDTO | None`
  - `list_app_configs() -> list[AppConfigDTO]`
  - `update_app_config(key: str, value: str) -> None`

### 3. API v1 & Authentication Middleware
- **Blueprint Dual-Mounting:**
  ```python
  app.register_blueprint(api_v1_bp, url_prefix="/api/v1")
  app.register_blueprint(api_v1_bp, url_prefix="/api", name="api_legacy")
  ```
- **Auth Decorator:**
  Timing-safe comparison: `hmac.compare_digest(provided_key, expected_key)`.
  Exemptions:
  - `request.method == "OPTIONS"` (CORS preflight)
  - `/api/health`, `/api/v1/health`
  - `/api/market/status`, `/api/v1/market/status`
- **Uniform Error Envelope:**
  ```json
  {
    "ok": false,
    "error": {
      "code": "VALIDATION_ERROR",
      "message": "Human readable summary",
      "details": null
    }
  }
  ```
- **Logging Correlation ID:**
  Contextvar `correlation_id` set from `X-Request-ID` header or generated `uuid4().hex[:12]`, injected into Loguru format and returned in response headers.
</implementation_details>

<pitfalls>
## Pitfalls & Edge Cases

| Area | Pitfall | Mitigation |
|---|---|---|
| **CORS Preflight** | Browser sends `OPTIONS` without auth headers; 401 response breaks web apps. | Middleware must return `"", 204` immediately on `OPTIONS`. Add `X-API-Key` & `X-Request-ID` to CORS allowed headers. |
| **Render Health Check** | `render.yaml` probes `/api/health`. If protected by auth, Render marks service unhealthy and rolls back. | `/api/health` and `/api/v1/health` must remain public without auth. |
| **Windows vs Linux Hashes** | Git on Windows converts line endings to CRLF; Docker/Render uses LF. | Always normalize `\r\n` -> `\n` before SHA-256 computation. |
| **Audit Trigger** | `009_audit_immutability.sql` blocks all SQL updates on audit tables. | Never run `UPDATE order_audit_trail`. Append execution status as a new row with identical `internal_order_id`. |
| **Pytest Rate Limiting** | Automated test suite trips 429 rate limit during rapid testing. | Set `limiter.enabled = False` whenever `os.environ.get("TESTING") == "true"`. |
</pitfalls>

<verification_strategy>
## Validation Architecture

1. **Migration Runner Tests:**
   - Execute `db/run_migrations.py --dry-run` and verify non-mutating preview.
   - Execute migrations against database and verify `schema_migrations` table records all 14 files.
   - Re-run migrations and verify all 14 files are safely skipped without error.
   - Test tampering detection: alter test file checksum and confirm fail-closed halt.
2. **Repository Unit & Integration Tests:**
   - Verify `record_order_attempt`, `record_validation_check`, and `record_broker_execution` return validated Pydantic DTOs.
   - Verify append-only execution recording does not trigger 009 trigger violation.
   - Verify `get_current_holdings` returns typed `list[HoldingDTO]`.
3. **API & Security Tests:**
   - Verify unauthenticated `/api/v1/health` returns 200 OK.
   - Verify unauthenticated `/api/v1/portfolio/summary` returns 401 with standard error envelope.
   - Verify valid `X-API-Key` returns 200 OK.
   - Verify legacy `/api/...` routes match `/api/v1/...` responses.
   - Verify 400/404/422/500 responses conform to standard error schema.
   - Verify `X-Request-ID` header is attached to responses.
</verification_strategy>

---
*Phase: 01-a-reliable-backend-core*
*Context gathered: 2026-09-27*
