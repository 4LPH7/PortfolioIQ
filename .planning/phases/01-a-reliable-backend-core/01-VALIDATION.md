---
phase: "01"
slug: "a-reliable-backend-core"
status: draft
nyquist_compliant: true
wave_0_complete: false
created: "2026-09-27"
---

# Phase 01 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.x / python 3.12 |
| **Config file** | `pytest.ini` |
| **Quick run command** | `pytest tests/test_phase1_backend.py -v` |
| **Full suite command** | `pytest tests/ -v --tb=short` |
| **Estimated runtime** | ~10 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_phase1_backend.py -v`
- **After every plan wave:** Run `pytest tests/ -v --tb=short`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 01-01-01 | 01 | 1 | D-09, D-10 | T-01-01 | Tamper-proof migrations tracking | unit | `pytest tests/test_migrations.py -k test_migration_runner` | ❌ W0 | ⬜ pending |
| 01-02-01 | 02 | 1 | D-06 | — | Typed DTO validation | unit | `pytest tests/test_repository.py -k test_dtos` | ❌ W0 | ⬜ pending |
| 01-02-02 | 02 | 1 | D-07, D-08 | T-01-02 | Append-only audit integrity | integration | `pytest tests/test_repository.py -k test_audit_append_only` | ❌ W0 | ⬜ pending |
| 01-03-01 | 03 | 2 | D-03, D-04, D-05 | T-01-03 | Timing-safe API key auth | unit/integration | `pytest tests/test_api_v1.py -k test_auth` | ❌ W0 | ⬜ pending |
| 01-03-02 | 03 | 2 | D-01, D-02 | — | Dual-mount /api/v1 & legacy alias | integration | `pytest tests/test_api_v1.py -k test_dual_mount` | ❌ W0 | ⬜ pending |
| 01-03-03 | 03 | 2 | D-11, D-12, D-13 | T-01-04 | Standard error & rate limits | integration | `pytest tests/test_api_v1.py -k test_errors_and_limits` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_migrations.py` — migration tracking and checksum tamper tests
- [ ] `tests/test_repository.py` — repository and DTO tests
- [ ] `tests/test_api_v1.py` — API v1 auth, routing, and error envelope tests

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|---|---|---|---|
| Frontend Dual-Mount Compatibility | D-02 | Verify Netlify dashboard interacts smoothly with `/api/v1` | Open Netlify UI, trigger sync and check network inspector for `/api/v1` calls |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 15s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** verified 2026-09-27
