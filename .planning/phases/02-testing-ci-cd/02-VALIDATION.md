---
phase: "02"
slug: "testing-ci-cd"
status: draft
nyquist_compliant: true
wave_0_complete: false
created: "2026-09-27"
---

# Phase 02 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x / python 3.12 |
| **Config file** | `pytest.ini` & `pyproject.toml` |
| **Quick run command** | `pytest tests/ -v --tb=short` |
| **Full suite command** | `pytest tests/ -v --tb=short --cov=src --cov-fail-under=75` |
| **Lint & Format command** | `ruff check . && ruff format --check .` |
| **Estimated runtime** | ~10 seconds |

---

## Sampling Rate

- **After every task commit:** Run targeted test command or linter check
- **After every plan wave:** Run full suite `pytest tests/ -v --tb=short` and `ruff check .`
- **Before phase completion:** Full suite green, coverage >= 75% overall, critical modules >= 85%
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 02-01-01 | 01 | 1 | D-03 | — | Ruff & coverage configuration | config | `ruff --version && python -c "import pyproject"` | ❌ W0 | ⬜ pending |
| 02-01-02 | 01 | 1 | D-01, D-02 | T-02-01 | DB isolation & savepoint rollback | unit/integration | `pytest tests/test_conftest.py -v` | ❌ W0 | ⬜ pending |
| 02-01-03 | 01 | 1 | D-09 | — | Local pre-commit hook setup | config | `git diff .pre-commit-config.yaml` | ✅ | ⬜ pending |
| 02-02-01 | 02 | 2 | D-03 | — | Codebase formatting & lint clean | lint | `ruff check . && ruff format --check .` | ✅ | ⬜ pending |
| 02-02-02 | 02 | 2 | D-04 | T-02-02 | Safety-critical execution & config tests | unit | `pytest tests/test_execution.py tests/test_config.py -v` | ❌ W0 | ⬜ pending |
| 02-02-03 | 02 | 2 | D-04 | — | Critical coverage gate (>= 85%) | audit | `python scripts/check_critical_coverage.py` | ❌ W0 | ⬜ pending |
| 02-03-01 | 03 | 3 | D-06 | — | Post-deploy smoke test script | unit | `pytest tests/test_smoke_test.py -v` | ❌ W0 | ⬜ pending |
| 02-03-02 | 03 | 3 | D-07 | T-02-03 | Rollback trigger on smoke test failure | unit | `pytest tests/test_smoke_test.py -k test_rollback -v` | ❌ W0 | ⬜ pending |
| 02-04-01 | 04 | 4 | D-05, D-08 | T-02-04 | GitHub Actions workflow integration | config | `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `pyproject.toml` — tool.ruff and tool.coverage configurations
- [ ] `tests/conftest.py` — PostgreSQL harness, transaction savepoints, and `@pytest.mark.postgres` hooks
- [ ] `scripts/check_critical_coverage.py` — Safety-critical path coverage evaluator
- [ ] `scripts/smoke_test.py` & `tests/test_smoke_test.py` — Smoke test runner and unit tests

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|---|---|---|---|
| GitHub Branch Protection Policy | D-08 | GitHub repo settings cannot be applied via git commit | Verify in GitHub repo settings > Branches that `main` requires `test`, `lint-and-audit`, `docker-build` |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify commands
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all newly introduced harness references
- [x] Feedback latency < 15s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** verified 2026-09-27
