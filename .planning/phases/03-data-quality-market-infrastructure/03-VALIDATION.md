# Phase 3: Data Quality & Market Infrastructure — Validation Strategy

**Created:** 2026-09-27  
**Status:** Active  
**Objective:** Establish automated test suites, Nyquist validation criteria, and coverage thresholds for Phase 3 deliverables.

---

## 1. Quality Gates & Validation Architecture

| Gate | Tool / Runner | Threshold / Requirement | Fail Action |
|---|---|---|---|
| **Syntax & Linter** | `ruff check .` | 0 errors, 0 warnings | Block commit |
| **Formatting** | `ruff format --check .` | 0 reformats needed | Block commit |
| **Test Suite** | `pytest tests/` | 100% pass, 0 failed, 0 skipped when DB reachable | Block merge |
| **Critical Path Coverage** | `scripts/check_critical_coverage.py` | $\ge 85.0\%$ coverage on `src/execution/`, `src/config/`, `src/api/middleware.py` | Pipeline fail |
| **Overall Coverage** | `pytest --cov=src` | $\ge 75.0\%$ overall coverage | Pipeline fail |
| **Migration Integrity** | `tests/test_migrations.py` | Checksums match, clean forward application on PostgreSQL 16 | Block migration |

---

## 2. Test Matrix by Deliverable

### 2.1 Database Migrations & Repository Layer (Plan 03-01)
- **Files:** `db/migrations/015_holdings_reconciliation_log.sql`, `db/migrations/016_market_calendar.sql`, `src/models/dtos.py`, `src/db/repository.py`
- **Tests in `tests/test_migrations.py` & `tests/test_repository.py`:**
  - `test_migration_015_016_execution`: Verifies migrations apply cleanly and create expected tables/columns/constraints.
  - `test_market_calendar_repository_crud`: Tests inserting, querying, and updating calendar entries.
  - `test_holdings_reconciliation_repository_append_only`: Verifies reconciliation logs insert and query with reason filtering.
  - `test_market_calendar_dto_validation`: Pydantic validation tests for session types (`CLOSED`, `MUHURAT`, `HALF_DAY`).

### 2.2 Market Hours & Special Sessions (Plan 03-02)
- **Files:** `src/ingestion/market_hours.py`, `src/config/nse_holidays.json`, `src/api/v1/blueprint.py`
- **Tests in `tests/test_market_hours.py` & `tests/test_api_v1.py`:**
  - `test_muhurat_special_session_trading_hours`: Uses `freezegun` at 18:30 IST on Diwali to verify `is_market_open()` returns `True`.
  - `test_muhurat_pre_and_post_market`: Verifies before 18:15 and after 19:15 on Diwali `is_market_open()` returns `False`.
  - `test_offline_fallback_json_when_db_down`: Patches `execute_sql` to throw an exception and verifies holiday detection works seamlessly from `nse_holidays.json`.
  - `test_get_calendar_api`: Tests `GET /api/v1/market/calendar` query with year/segment filters.
  - `test_post_calendar_api_auth`: Verifies `POST /api/v1/market/calendar` requires API key and updates database.

### 2.3 Kite Connect REST Quote Poller (Plan 03-03)
- **Files:** `src/ingestion/kite_quote_poller.py`
- **Tests in `tests/test_kite_quote_poller.py`:**
  - `test_poll_kite_quotes_batches_all_holdings`: Verifies multiple holding symbols are batched into a single `kite.quote()` call.
  - `test_poll_kite_quotes_updates_live_prices`: Asserts `live_prices` receives updated price, `source = 'kite'`, `is_stale = FALSE`.
  - `test_poll_kite_quotes_archives_to_history`: Asserts `price_history` receives snapshot.
  - `test_poll_kite_quotes_empty_portfolio`: Handles 0 active holdings gracefully without making broker calls.
  - `test_poll_kite_quotes_token_expired`: Asserts `TokenException` invokes `invalidate_token()` and logs alert.

### 2.4 Gatekeeper 60s Freshness & Fail-Closed On-Demand Refresh (Plan 03-04)
- **Files:** `src/execution/validators/slippage_check.py`, `src/execution/gatekeeper.py`
- **Tests in `tests/test_execution.py`:**
  - `test_slippage_fresh_price_passes`: Price updated < 60s ago within bound passes without broker fetch.
  - `test_slippage_stale_price_triggers_on_demand_refresh_success`: Price updated > 60s ago triggers `kite.ltp()`, updates `live_prices`, passes validation.
  - `test_slippage_stale_price_broker_failure_fails_closed`: Price updated > 60s ago, `kite.ltp()` throws network error -> order rejected (`passed = False`).
  - `test_slippage_missing_price_broker_timeout_fails_closed`: No price in `live_prices`, `kite.ltp()` times out -> order rejected (`passed = False`).
  - `test_slippage_off_market_hours_uses_close_price`: Stale timestamp outside market hours does not trigger broker fetch.

### 2.5 Holdings Reconciliation & Corporate Actions (Plan 03-05)
- **Files:** `src/ingestion/kite_sync.py`, `src/api/v1/blueprint.py`
- **Tests in `tests/test_kite_sync.py` & `tests/test_api_v1.py`:**
  - `test_reconcile_initial_sync`: Discovered holding logs `"INITIAL_SYNC"` and upserts into `user_holdings`.
  - `test_reconcile_t1_settlement`: T1 quantity settling into demat quantity logs `"T1_SETTLEMENT"`.
  - `test_reconcile_trade_fill`: Quantity increase matching executed buy in `order_audit_trail` logs `"TRADE_FILL"`.
  - `test_reconcile_corporate_action_split`: Quantity increase matching split in `corporate_actions` logs `"CORPORATE_ACTION_SPLIT"`.
  - `test_reconcile_unexplained_jump_alerts`: Quantity change without fills or splits logs `"DISCREPANCY"` and logs warning.
  - `test_get_reconciliation_logs_api`: Tests `GET /api/v1/holdings/reconciliation` with reason filtering.

---

## 3. Verification Protocol

1. Apply migrations `015` and `016` locally via `python -m db.run_migrations`.
2. Execute full test suite:
   ```powershell
   pytest tests/ -v
   ```
3. Run critical path coverage gate:
   ```powershell
   python scripts/check_critical_coverage.py
   ```
4. Run formatting and linting:
   ```powershell
   ruff check .
   ruff format --check .
   ```
5. Confirm zero regressions across existing 166 tests.
