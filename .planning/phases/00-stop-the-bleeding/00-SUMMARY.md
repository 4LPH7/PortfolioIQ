---
phase: 00-stop-the-bleeding
plan: 00
subsystem: security-and-safety
tags: [security, schema-reconciliation, dry-run, gatekeeper, cors]
provides:
  - Secrets rotation and removal from tracked config files (render.yaml, .env)
  - Pre-commit and CI secrets scanning via Gitleaks
  - Canonical schema reconciliation across flask_app, gatekeeper, order_router, and drift_detector
  - Fail-closed DRY_RUN_MODE enforcement at infrastructure and gatekeeper layers
  - Restricted CORS allowlist
  - Comprehensive safety regression test suite (tests/test_phase0_safety.py)
affects: [01-reliable-backend-core, 02-testing-ci-cd]
actuals:
  tokens: 4500
  tasks: 9
  commits: 2
tech-stack:
  added: [gitleaks, pre-commit]
  patterns: [fail-closed security, gatekeeper audit enforcement, canonical schema alignment]
key-files:
  created:
    - .pre-commit-config.yaml
    - tests/test_phase0_safety.py
  modified:
    - render.yaml
    - .env.example
    - flask_app.py
    - src/config/settings.py
    - src/execution/gatekeeper.py
    - src/execution/order_router.py
    - src/analytics/drift_detector.py
    - src/scheduler/jobs.py
    - .github/workflows/ci.yml
key-decisions:
  - "Enforce fail-closed DRY_RUN_MODE at the environment level; gatekeeper overrides any live order requests to simulated orders while DRY_RUN_MODE is true."
  - "Align all database queries with supabase_schema.sql as single source of truth."
  - "Enforce strict origin allowlist for CORS in flask_app instead of wildcard '*'"
duration: 45min
completed: 2026-09-27
status: complete
---

# Phase 0: Stop the Bleeding Summary

**Hardened security, eliminated hardcoded secrets, reconciled schema discrepancies, and locked execution into fail-closed dry-run mode.**

## Performance
- **Tasks:** 9 completed
- **Files created/modified:** 11 files
- **Tests passing:** 7/7 automated safety tests passed (1 skipped live-DB integration test)

## Accomplishments
- **Secrets Sanitization:** Removed hardcoded `KITE_API_KEY` from `render.yaml` (`sync: false`), documented all secrets in `.env.example`, and installed Gitleaks scanning in `.pre-commit-config.yaml` and `.github/workflows/ci.yml`.
- **Trading Safety Lock:** Forced `DRY_RUN_MODE=true` at environment settings and implemented fail-closed enforcement in `gatekeeper.py` and `order_router.py`. Live order placement cannot occur even if requested by client or application bugs.
- **Schema Reconciliation:** Standardized database columns across `flask_app.py`, `gatekeeper.py`, `order_router.py`, and `drift_detector.py` to match `supabase_schema.sql`.
- **Scheduler & CORS Fixes:** Fixed partition creation call in `scheduler/jobs.py` and restricted CORS to `ALLOWED_ORIGINS`.

## Task Commits
1. **Security & Schema hardening** - `eb74a7e`
2. **Phase 0 verification & planning documentation** - `head`

## Next Phase Readiness
- Foundation is stabilized and safe from credential leakage or unintended order placement.
- Ready to begin **Phase 1: A Reliable Backend Core** (Alembic migrations, typed SQLAlchemy/Pydantic models, centralized persistence, and API v1 routing).
