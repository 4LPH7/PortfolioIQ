---
phase: 02-testing-ci-cd
plan: 01
subsystem: toolchain-test-harness
tags: [pytest, conftest, ruff, coverage, savepoints, postgresql, pip-audit, pre-commit]
provides:
  - pyproject.toml with tool.ruff (linting & formatting) and tool.coverage configurations
  - Updated requirements.txt with pytest-cov and pip-audit; removed obsolete black dependency
  - tests/conftest.py with session migrations, transaction rollback savepoints, and @pytest.mark.postgres auto-skip
  - tests/test_conftest.py verifying PostgreSQL detection, marker registration, and transaction rollback isolation
  - Updated .pre-commit-config.yaml with astral-sh/ruff-pre-commit (ruff + ruff-format) and gitleaks
  - Converted test_phase0_safety.py to use @pytest.mark.postgres and db_session fixture (0 tests skipped)
affects: [02-02-PLAN, 02-04-PLAN]
actuals:
  tokens: 4100
  tasks: 3
  commits: 1
tech-stack:
  added: [pytest-cov, pip-audit]
  removed: [black]
  patterns: [nested-transaction-savepoints, session-migration-bootstrapping, offline-db-auto-skip]
key-files:
  created:
    - pyproject.toml
    - tests/test_conftest.py
  modified:
    - requirements.txt
    - .pre-commit-config.yaml
    - tests/conftest.py
    - db/run_migrations.py
    - tests/test_phase0_safety.py
key-decisions:
  - "Use SQLAlchemy sessionmaker with join_transaction_mode='create_savepoint' so application commits merely release savepoints and test fixtures roll back the outer transaction."
  - "Auto-skip @pytest.mark.postgres tests locally if PostgreSQL is down, but fail-fast in CI if CI=true."
  - "Close read transactions in db/run_migrations.py after get_applied_migrations to prevent psycopg2 'set_session cannot be used inside a transaction' errors."
duration: 15min
completed: 2026-09-27
status: complete
---

# Plan 02-01: Toolchain Modernization & PostgreSQL Test Harness Summary

**Modernized the PortfolioIQ development and testing toolchains with `pyproject.toml` (Ruff & Coverage), updated `requirements.txt` and `.pre-commit-config.yaml`, and implemented `tests/conftest.py` with nested transaction rollbacks and live PostgreSQL schema execution.**

## Performance
- **Tasks:** 3 completed
- **Tests passing:** 4/4 test_conftest tests passed; 68/68 total test suite passed (0 skipped)
- **Runtime:** 4.32s full suite execution time

## Accomplishments
- **Toolchain Modernization:** Configured `pyproject.toml` with unified `[tool.ruff]` (E, W, F, I, B, UP rules, 100-char line length) and `[tool.coverage]` (75% overall threshold). Added `pytest-cov` and `pip-audit` to `requirements.txt` and removed redundant `black`.
- **PostgreSQL Test Harness:** Implemented `tests/conftest.py` with:
  - Fast connection reachability probe `is_postgres_reachable`.
  - `@pytest.mark.postgres` marker with auto-skip for offline local developers and fail-fast enforcement in CI.
  - Session-scoped autouse fixture applying all 14 migrations via `db/run_migrations.py`.
  - Function-scoped `db_connection` and `db_session` fixtures utilizing SQLAlchemy Core nested savepoints (`join_transaction_mode="create_savepoint"`), rolling back state after every test.
- **Migration Runner Fix:** Resolved transaction state issue in `db/run_migrations.py` where uncommitted SELECT queries prevented `set_session` calls.
- **Pre-commit Automation:** Updated `.pre-commit-config.yaml` to run `ruff` (with `--fix`), `ruff-format`, and `gitleaks`.
- **Full Test Pass:** Updated `tests/test_phase0_safety.py` to use `db_session` so all 8 tests pass without skipping.

## Task Commits
1. **Plan 02-01: Toolchain Modernization & PostgreSQL Test Harness** - `head`

## Next Plan Readiness
- Wave 1 is now 100% complete.
- Ready for Wave 2: **Plan 02-02**: Safety-Critical Test Expansion & Ruff Codebase Normalization (`tests/test_execution.py`, `tests/test_config.py`, `tests/test_middleware.py`, `scripts/check_critical_coverage.py`).
