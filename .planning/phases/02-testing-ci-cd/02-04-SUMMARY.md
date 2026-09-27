---
phase: 02-testing-ci-cd
plan: 04
subsystem: ci-cd-pipeline
tags: [github-actions, ci-cd, docker-build, pip-audit, postgres-service, smoke-test, rollback, branch-protection]
provides:
  - Modernized .github/workflows/ci.yml with 6 pipeline jobs forming a clean dependency DAG
  - secret-scan (Gitleaks)
  - lint-and-audit (Ruff format, Ruff lint, pip-audit CVE scan, pip cache)
  - docker-build (Docker container build validation)
  - test (postgres:16-alpine service container with health check, migration runner, pytest with coverage, check_critical_coverage.py)
  - deploy-railway (deployment via Railway CLI, post-deploy smoke test with exponential backoff, automated rollback to HEAD~1 on smoke test failure)
  - deploy-netlify (frontend deployment to Netlify after test pass)
  - Explicit branch protection status check names documented
affects: [ROADMAP.md, STATE.md]
actuals:
  tokens: 4200
  tasks: 2
  commits: 1
tech-stack:
  added: [.github/workflows/ci.yml]
  patterns: [clean-ci-dag, postgres-service-container, pip-caching, automated-rollback, post-deploy-smoke-test]
key-files:
  modified:
    - .github/workflows/ci.yml
key-decisions:
  - "Standardize PostgreSQL service container credentials in CI (portfolioiq_user / portfolioiq_pass_change_me) matching local docker-compose and conftest defaults."
  - "Run Docker container build validation in parallel with lint-and-audit so container issues are caught early."
  - "Use fetch-depth: 2 on checkout in deploy-railway so git checkout HEAD~1 is available for automated rollback upon smoke test failure."
duration: 15min
completed: 2026-09-27
status: complete
---

# Plan 02-04: GitHub Actions CI/CD Pipeline & Branch Protection Summary

**Modernized the PortfolioIQ CI/CD pipeline in `.github/workflows/ci.yml` into a production-grade 6-stage DAG covering secret detection, Ruff lint/formatting, pip vulnerability auditing, Docker build validation, PostgreSQL service testing with migration and coverage gates, and automated deployment with post-deploy smoke test verification and Git-based rollback.**

## Performance
- **Tasks:** 2 completed
- **Jobs defined:** 6 (`secret-scan`, `lint-and-audit`, `docker-build`, `test`, `deploy-railway`, `deploy-netlify`)
- **YAML Validation:** Verified valid syntax via `yaml.safe_load`
- **Branch Protection Status Checks:** 4 required checks confirmed for `main`

## Accomplishments
- **Modernized Pipeline DAG:**
  - `secret-scan` runs Gitleaks across full repository history on both pull requests and pushes to `main`.
  - `lint-and-audit` runs in parallel with Docker build, validating code formatting (`ruff format --check .`), code quality (`ruff check .`), and dependency CVE vulnerabilities (`pip-audit -r requirements.txt --desc`) with pip package caching.
  - `docker-build` verifies `docker build -t portfolioiq:ci .` ensuring container buildability without waiting for deployment stages.
  - `test` orchestrates `postgres:16-alpine` service container with standard credentials (`portfolioiq_user` / `portfolioiq_pass_change_me`) and pg_isready health checks, executes `db/run_migrations.py`, enforces >= 75% overall coverage via `pytest --cov-fail-under=75`, and validates >= 85% safety-critical coverage with `scripts/check_critical_coverage.py`.
  - `deploy-railway` deploys to Railway on `main` push, probes `/api/v1/health` and `/api/v1/market/status` with `scripts/smoke_test.py`, and triggers automated rollback to `HEAD~1` if health checks fail.
  - `deploy-netlify` publishes the static frontend to Netlify after tests pass.
- **Documented Branch Protection Settings:**
  - Recommended branch protection rules for `main`:
    - Require status checks to pass before merging:
      1. `Scan repository history for secrets` (`secret-scan`)
      2. `Lint code and audit dependencies` (`lint-and-audit`)
      3. `Validate Docker container build` (`docker-build`)
      4. `Run tests and migration checks` (`test`)
    - Require linear history.
    - Allow administrators to bypass protections for emergency hotfixes (per Decision D-08).

## Task Commits
1. **Plan 02-04: GitHub Actions CI/CD Pipeline & Branch Protection** - to be committed

## Phase 2 Completion Readiness
- All 4 plans of Phase 2 are complete:
  - Plan 02-01: Toolchain Modernization & PostgreSQL Test Harness (complete)
  - Plan 02-02: Safety-Critical Test Expansion & Ruff Codebase Normalization (complete)
  - Plan 02-03: Post-Deployment Smoke Testing & Failure Rollback (complete)
  - Plan 02-04: GitHub Actions CI/CD Pipeline & Branch Protection (complete)
- Ready for Phase 2 Verification and Milestone completion.
