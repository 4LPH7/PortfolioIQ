# Phase 2: Testing & CI/CD - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-09-27
**Phase:** 2-Testing & CI/CD
**Areas discussed:** PostgreSQL Test Environment & Fixtures, CI Pipeline Quality Gates & Coverage Thresholds, Deployment Smoke Testing & Post-Deploy Health Checks, Branch Protection & Merge Policies

---

## PostgreSQL Test Environment & Fixtures

| Option | Description | Selected |
|--------|-------------|----------|
| Real PostgreSQL container | Session-level migrations + nested transaction rollbacks (fast, faithful to Supabase/PostgreSQL schema) | ✓ |
| Clean-and-truncate | Truncate tables between tests using pytest fixtures (cleanest separation, slight performance overhead) | |
| Dual-track mock/integration | Mock/unit tests by default, live PostgreSQL integration tests opt-in with a marker (-m integration) | |

**User's choice:** Real PostgreSQL container with session-level migrations + nested transaction rollbacks.
**Notes:** Provides fast test turnaround while executing against true PostgreSQL schema constraints and triggers.

| Option | Description | Selected |
|--------|-------------|----------|
| Auto-skip offline DB tests | Gracefully skip PostgreSQL-dependent tests if no live DB is detected locally, fail-fast in CI | ✓ |
| Strict local requirement | Always require local PostgreSQL container running and fail tests if unreachable | |
| Testcontainers Python | Spin up ephemeral Docker container on-the-fly during test runs | |

**User's choice:** Auto-skip PostgreSQL-dependent tests locally if DB is offline, but fail-fast in CI.
**Notes:** Smooth local developer experience without Docker daemon friction, strict correctness in CI.

---

## CI Pipeline Quality Gates & Coverage Thresholds

| Option | Description | Selected |
|--------|-------------|----------|
| Unified Ruff stack | ruff check . (linting) + ruff format --check . (formatting, drop-in black replacement, 10-100x faster) | ✓ |
| Ruff + Black | ruff check . && black --check . | |
| Full suite | ruff check . + black --check . + isort --check . | |

**User's choice:** Unified Ruff stack (linting and formatting).
**Notes:** High speed and unified configuration in pyproject.toml / ruff.toml.

| Option | Description | Selected |
|--------|-------------|----------|
| Tiered coverage | Minimum 75% overall coverage + 85% strictly enforced on safety-critical paths | ✓ |
| Uniform 80% | Uniform 80% coverage threshold across entire codebase | |
| Informative only | Report coverage in CI logs without breaking the build on percentage drops | |

**User's choice:** Minimum 75% overall coverage + 85% strictly enforced on safety-critical paths (`src/execution/`, `src/config/`, `src/api/middleware.py`).
**Notes:** Protects order routing and authorization paths from regression.

| Option | Description | Selected |
|--------|-------------|----------|
| Parallel audit & build | Run pip-audit for CVE scanning and docker build . for container validation | ✓ |
| pip-audit only | Run pip-audit only, leave Docker build to deployment | |
| Weekly cron audit | Run pip-audit in scheduled weekly cron job rather than on PR push | |

**User's choice:** Run pip-audit and docker build verification in CI.

---

## Deployment Smoke Testing & Post-Deploy Health Checks

| Option | Description | Selected |
|--------|-------------|----------|
| Comprehensive smoke test | Poll /api/v1/health (asserting db=True) and /api/v1/market/status with 60s exponential retry | ✓ |
| Authenticated smoke test | Check /api/v1/health plus authenticated read using SMOKE_TEST_API_KEY | |
| Simple ping | Single curl to /api/health with 30s timeout | |

**User's choice:** Comprehensive smoke test polling `/api/v1/health` and `/api/v1/market/status` with 60s retry.
**Notes:** Accommodates cold starts while verifying core database connectivity and market routing.

| Option | Description | Selected |
|--------|-------------|----------|
| Automated rollback | If health checks fail, redeploy previous successful Git commit SHA via CLI/API and fail CI | ✓ |
| GitHub Issue / Alert | Fail CI and open alert, rely on host zero-downtime healthcheck gates | |
| Silent fail | Fail CI and let developers manually roll back | |

**User's choice:** Automated rollback via platform CLI/API upon health check failure.

---

## Branch Protection & Merge Policies

| Option | Description | Selected |
|--------|-------------|----------|
| Required CI status checks | Require tests, linter, migrations, secret-scan before merge to main, admin bypass for emergency | ✓ |
| Strict PR + approval | Require PR + passing CI + 1 approval before any commit can land on main | |
| Lightweight protection | Linear history required, checks advisory | |

**User's choice:** Required CI status checks before merge to main, with admin bypass for emergency solo fixes.

| Option | Description | Selected |
|--------|-------------|----------|
| Dual enforcement | Pre-commit hook (Ruff + Gitleaks) for local feedback + GitHub Actions CI as definitive gate | ✓ |
| CI-only | Run everything in GitHub Actions without local git hooks | |
| Comprehensive pre-commit | Run pytest, ruff, and gitleaks before every commit locally | |

**User's choice:** Dual enforcement (local pre-commit for fast developer iteration + CI as definitive gate).

---

## the agent's Discretion

- Specific retry intervals for post-deploy smoke checks (e.g. 5s initial, doubling up to 60s total).
- Pytest fixture organization in `tests/conftest.py`.
- Granular Ruff rule configuration (`E`, `F`, `W`, `I`, `B`, `UP`).

## Deferred Ideas

- None — discussion stayed within the Phase 2 boundary.
