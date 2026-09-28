# Phase 4: Portfolio Analytics That Actually Mean Something — Validation Strategy

**Created:** 2026-09-28  
**Status:** Active  
**Objective:** Establish test suites, mathematical verification criteria, and coverage thresholds for Phase 4 quantitative analytics and rebalancing constraints.

---

## 1. Quality Gates & Validation Architecture

| Gate | Tool / Runner | Threshold / Requirement | Fail Action |
|---|---|---|---|
| **Syntax & Linter** | `ruff check .` | 0 errors, 0 warnings | Block commit |
| **Formatting** | `ruff format --check .` | 0 reformats needed | Block commit |
| **Test Suite** | `pytest tests/` | 100% pass, 0 failed, 0 skipped when DB reachable | Block merge |
| **Critical Path Coverage** | `scripts/check_critical_coverage.py` | $\ge 85.0\%$ coverage on `src/execution/`, `src/config/`, `src/api/middleware.py` | Pipeline fail |
| **Overall Core Coverage** | `pytest --cov=src` | $\ge 75.0\%$ overall coverage | Pipeline fail |
| **Migration Integrity** | `tests/test_migrations.py` | Checksums match, clean forward application of 017 and 018 | Block migration |

---

## 2. Test Matrix by Deliverable

### 2.1 Schema & Cash Flow Ledger (Plan 04-01)
- **Files:** `db/migrations/017_portfolio_snapshots.sql`, `db/migrations/018_portfolio_cash_flows.sql`, `src/models/dtos.py`, `src/db/repository.py`
- **Tests in `tests/test_migrations.py` & `tests/test_repository.py`:**
  - `test_migration_017_018_execution`: Clean application of snapshots and cash flows tables with constraints and indexes.
  - `test_portfolio_snapshots_repository_crud`: Record and query daily snapshots in date range with unit NAV checks.
  - `test_portfolio_cash_flows_repository_crud`: Insert and query deposits/withdrawals/dividends.

### 2.2 Quantitative Return & Risk Engine (Plan 04-02)
- **Files:** `src/analytics/performance.py`
- **Tests in `tests/test_performance.py`:**
  - `test_twr_unitization_isolated_from_cash_flows`: Large deposit intraday does not artificially inflate or deflate TWR.
  - `test_xirr_numerical_solver_accuracy`: Solves XIRR against known financial cash-flow benchmarks (matches Excel XIRR to 4 decimal places).
  - `test_xirr_handles_irregular_intervals_and_edge_cases`: Zero flows, single flow, all negative flows return None cleanly.
  - `test_sharpe_and_sortino_calculation_annualized`: Verifies excess return, $\sqrt{252}$ scaling, and Sortino downside deviation.
  - `test_warmup_period_gate`: < 30 trading days returns `is_warmup_period = True` with None for Sharpe/Sortino/Beta.
  - `test_max_drawdown_and_hwm_tracking`: Peak-to-trough series with recovery dates accurately identified.
  - `test_beta_and_jensens_alpha_vs_nifty`: Rolling regression covariance/variance produces expected Beta and annualized Alpha.

### 2.3 Daily Snapshot Recorder & Scheduler (Plan 04-03)
- **Files:** `src/analytics/snapshot_recorder.py`, `scheduler/jobs.py`
- **Tests in `tests/test_snapshot_recorder.py`:**
  - `test_record_eod_snapshot_computes_daily_return`: Correctly links prior day's unit NAV and EOD portfolio valuation.
  - `test_detect_cash_margin_deltas_records_auto_flow`: Sudden uninvested cash increase creates an `AUTO_MARGIN_SYNC` flow.
  - `test_smart_backfill_from_audit_trail`: Backfills historical snapshots from first trade execution date.

### 2.4 Indian Transaction Cost Model & Sizing Constraints (Plan 04-04)
- **Files:** `src/analytics/cost_calculator.py`, `src/analytics/rebalancer.py`
- **Tests in `tests/test_rebalancer_constraints.py` & `tests/test_cost_calculator.py`:**
  - `test_indian_delivery_charges_accuracy`: Matches Zerodha brokerage calculator for buy and sell delivery to the rupee (STT, DP charges, GST).
  - `test_min_trade_value_suppresses_small_orders`: Orders < ₹2,000 are omitted from manifest.
  - `test_full_liquidation_bypasses_min_trade_threshold`: 100% position exit allows orders < ₹2,000.
  - `test_cash_buffer_preserves_uninvested_cushion`: Buy orders capped so cash does not drop below 2% or ₹5,000.
  - `test_turnover_cap_prioritizes_largest_drift`: Caps total trade volume at 15% AUM, prioritizing top drift signals.
  - `test_liquidity_cap_respects_adv_limit`: Order quantities capped at $\le 1.0\%$ of 20-day ADV.

### 2.5 Tax-Loss Harvesting & FY LTCG Exemption (Plan 04-05)
- **Files:** `src/analytics/tax_guard.py`
- **Tests in `tests/test_tax_guard.py`:**
  - `test_tax_optimized_lot_selection_prefers_losses`: Sells short-term loss lots before profit lots.
  - `test_near_ltcg_lock_defers_lots_within_30_days`: Prevents selling lots aged 335-364 days.
  - `test_annual_ltcg_exemption_tracker`: Accumulates realized LTCG toward the ₹1.25L threshold.
  - `test_q4_tax_gain_harvesting_recommendations`: Recommends harvesting appreciated LTCG lots when remaining exemption > 0 in Q4.

### 2.6 REST APIs & Drag Attribution (Plan 04-06)
- **Files:** `src/api/v1/blueprint.py`
- **Tests in `tests/test_api_v1.py`:**
  - `test_get_performance_api_authorized`: 200 with full performance attribution payload.
  - `test_get_snapshots_time_series_api`: 200 with equity curve series.
  - `test_get_tax_harvesting_api`: 200 with harvesting recommendations.
  - `test_post_rebalance_preview_api`: 200 with cost breakdown and drag attribution.

---

## 3. Verification Protocol

1. Apply migrations `017` and `018` locally via `python -m db.run_migrations`.
2. Run unit and integration tests:
   ```powershell
   pytest tests/test_performance.py tests/test_snapshot_recorder.py tests/test_rebalancer_constraints.py tests/test_cost_calculator.py tests/test_tax_guard.py tests/test_api_v1.py -v
   ```
3. Run critical path coverage gate:
   ```powershell
   python scripts/check_critical_coverage.py
   ```
4. Verify formatting and linting:
   ```powershell
   ruff check .
   ruff format --check .
   ```
5. Confirm zero regressions across all existing Phase 0-3 test suites.
