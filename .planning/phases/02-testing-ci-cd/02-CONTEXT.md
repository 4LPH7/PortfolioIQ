# Phase 2: Testing & CI/CD - Context

**Gathered:** 2026-09-27
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 2 establishes robust, production-grade test automation and CI/CD pipelines so that schema drift, regression bugs, and broken builds cannot land in `main` or reach production.
This encompasses PostgreSQL service container testing in CI, session-scoped database test fixtures, unified Ruff linting and formatting, coverage thresholds on safety-critical modules, dependency vulnerability auditing (`pip-audit`), container image validation (`docker build`), automated deployment smoke tests with rollback triggers, and GitHub branch protection.

</domain>

<decisions>
## Implementation Decisions

### PostgreSQL Test Environment & Fixtures
- **D-01:** Real PostgreSQL container in CI (`postgres:16`) with session-level migration runner execution (`db/run_migrations.py`) and nested transaction rollbacks per test for schema fidelity and execution speed. — **Reversibility:** costly — changes to the test harness architecture affect all database test fixtures and session setup.
- **D-02:** Local offline fallback: tests that require PostgreSQL automatically skip gracefully if no local database is reachable, but CI fails fast if PostgreSQL is unavailable.

### CI Pipeline Quality Gates & Coverage Thresholds
- **D-03:** Unified Ruff toolchain: `ruff check .` for linting and `ruff format --check .` for code formatting (replaces separate flake8/black toolchains with sub-second execution).
- **D-04:** Test coverage gates: enforce minimum 75% overall coverage in CI (`--cov-fail-under=75`) and strictly require 85% coverage on safety-critical paths (`src/execution/`, `src/config/`, `src/api/middleware.py`).
- **D-05:** Dependency security & container verification: execute `pip-audit` to detect CVEs in dependencies and validate container buildability via `docker build .` during CI runs.

### Deployment Smoke Testing & Post-Deploy Health Checks
- **D-06:** Automated post-deploy smoke test: poll `/api/v1/health` (asserting `status == "ok"` and `db == True`) and `/api/v1/market/status` with 60s exponential retry to accommodate cold-start latency.
- **D-07:** Automated rollback: if smoke tests fail after maximum retry attempts, trigger automated rollback to the previous successful commit SHA via platform CLI/API and fail the GitHub Action workflow.

### Branch Protection & Developer Workflow
- **D-08:** Branch protection on `main`: require passing CI status checks (tests, linter, migrations, secret-scan, docker-build) before pull request merges, permitting administrator bypass for emergency hotfixes.
- **D-09:** Dual-layer enforcement: maintain `.pre-commit-config.yaml` with Ruff and Gitleaks for instantaneous local developer feedback, while GitHub Actions serves as the unbypassable gate.

### Downstream Agent Discretion
- Exact retry backoff schedules (e.g. 5s initial, doubling up to 60s total) for post-deployment smoke tests.
- Pytest fixture naming and organization (`tests/conftest.py`).
- Specific Ruff rule codes (`E`, `F`, `W`, `I`, `B`, `UP`).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Workflows & Infrastructure
- `.github/workflows/ci.yml` — Existing GitHub Actions workflow covering secret scans, service container tests, and deployment steps.
- `docker-compose.yml` — Local PostgreSQL service definition and container healthcheck setup.
- `Dockerfile` — Container build configuration and application entrypoint.
- `render.yaml` — Hosting configuration and health check specifications.
- `pytest.ini` — Test configuration, test markers, and python path settings.

### Schema & Migrations
- `db/run_migrations.py` — Idempotent migration runner for CI database bootstrapping.
- `db/migrations/*.sql` — Canonical database migration scripts.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `tests/test_api_v1.py` & `tests/test_repository.py`: Established unit test patterns using pytest fixtures and Flask test client.
- `.pre-commit-config.yaml`: Pre-existing pre-commit hook configuration ready for Ruff and Gitleaks integration.
- `db/run_migrations.py`: Fully functional migration runner with checksum tracking and tamper detection.

### Established Patterns
- Pydantic DTOs and SQLAlchemy Core parameterized queries via `src/db/repository.py`.
- Unauthenticated `/api/v1/health` and `/api/health` returning `{"status": "ok", "db": bool}`.
- Fail-closed dry-run enforcement via `DRY_RUN_MODE`.

### Integration Points
- `.github/workflows/ci.yml`: GitHub Actions pipeline where test container, linting, pip-audit, coverage, and smoke tests integrate.
- `tests/conftest.py`: Shared pytest fixtures for database connection, transactions, and client injection.

</code_context>

<specifics>
## Specific Ideas
- Provide a `tests/conftest.py` with a session fixture that runs migrations once against the test database and a function fixture that yields a transactional connection that rolls back upon test exit.
- Ensure `pip-audit` runs against `requirements.txt` to catch known vulnerabilities before deployments.
- Ensure smoke test script can run both in CI and manually from CLI against staging/production URLs.

</specifics>

<deferred>
## Deferred Ideas
- None — discussion stayed strictly within the testing and CI/CD boundary.

</deferred>

---

*Phase: 2-Testing & CI/CD*
*Context gathered: 2026-09-27*
