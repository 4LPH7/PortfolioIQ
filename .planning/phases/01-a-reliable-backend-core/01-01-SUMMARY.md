---
phase: 01-a-reliable-backend-core
plan: 01
subsystem: database-migrations
tags: [migrations, schema_migrations, checksum, sha256, idempotency]
provides:
  - Idempotent schema_migrations tracking table
  - Deterministic CRLF/LF normalized SHA-256 migration checksum verification
  - Fail-closed tamper detection (MigrationTamperedError)
  - Transactional rollback on migration execution failure
  - CLI commands (--dry-run, --status, --baseline)
  - Unit and integration tests for migration runner (tests/test_migrations.py)
affects: [01-02-PLAN, 02-testing-ci-cd]
actuals:
  tokens: 2800
  tasks: 2
  commits: 1
tech-stack:
  added: [hashlib, psycopg2]
  patterns: [transactional migrations, content hashing, tamper detection]
key-files:
  created:
    - tests/test_migrations.py
  modified:
    - db/run_migrations.py
key-decisions:
  - "Normalize CRLF to LF before computing SHA-256 migration checksums to prevent cross-platform hash divergence between Windows and Linux/Docker."
  - "Fail closed with MigrationTamperedError if an already applied migration's content has been altered in the local repository."
duration: 15min
completed: 2026-09-27
status: complete
---

# Plan 01-01: Idempotent Migration Runner Summary

**Implemented deterministic migration tracking with `schema_migrations`, cross-platform line-ending normalized SHA-256 checksums, tamper detection, and transactional rollback.**

## Performance
- **Tasks:** 2 completed
- **Tests passing:** 6/6 migration runner tests passed
- **Regression tests:** 7/7 Phase 0 safety tests passed (1 skipped live-DB test)

## Accomplishments
- **Tracking Table:** Added automatic bootstrapping of `schema_migrations` (`version`, `checksum_sha256`, `executed_at`).
- **Tamper Detection:** Applied migrations verify that local code matches the recorded SHA-256 checksum; any unauthorized modification raises `MigrationTamperedError`.
- **Cross-Platform Hash Parity:** Standardized hash calculation by normalizing `\r\n` to `\n` before computing SHA-256.
- **Transactional Rollback:** Each unapplied migration runs in an isolated transaction block (`conn.autocommit = False`) that rolls back cleanly if SQL errors occur.
- **CLI Enhancements:** Added `--status` for tabular status display, `--dry-run` for previewing pending files, and `--baseline` for pre-existing databases.
- **Automated Tests:** Created `tests/test_migrations.py` validating checksum normalization, idempotency, tampering exceptions, and rollback behavior.

## Task Commits
1. **Task 1 & 2: Idempotent migration runner and tests** - `head`

## Next Plan Readiness
- Database migrations are now strictly versioned and idempotent.
- Ready for **Plan 01-02**: Typed Pydantic DTOs & Centralized Persistence Repository (`src/models/dtos.py` & `src/db/repository.py`).
