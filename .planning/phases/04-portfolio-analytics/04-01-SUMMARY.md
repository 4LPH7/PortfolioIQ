---
phase: 04-portfolio-analytics
plan: 01
status: complete
commits: 1
completed_at: 2026-09-28T09:50:00Z
---

# Plan 04-01: Historical NAV Ledger & Cash Flow Schema Summary

## Overview

Plan 04-01 established the foundational PostgreSQL schemas, typed Pydantic DTO models, and session-managed database repository functions required for quantitative portfolio performance tracking and tax-lot accounting in Phase 4.

## Key Accomplishments

1. **Database Migrations:**
   - `017_portfolio_snapshots.sql`: Created `portfolio_daily_snapshots` table supporting GIPS-compliant unitized NAV tracking (`unit_nav` starting base 100), benchmark tracking (`benchmark_name`, `benchmark_value`), and fee drag attribution columns (`stt_drag_bps`, `fee_drag_bps`, `tax_drag_bps`).
   - `018_portfolio_cash_flows.sql`: Created `portfolio_cash_flows` ledger for explicit tracking of external deposits, withdrawals, dividends, and charges, recording unit impacts and audit sources.
2. **Pydantic DTO Models (`src/models/dtos.py`):**
   - Added `PortfolioDailySnapshotDTO` and `CreateSnapshotDTO`.
   - Added `PortfolioCashFlowDTO` and `CreateCashFlowDTO`.
   - Added `PerformanceMetricsDTO`, `TaxHarvestingOpportunityDTO`, and `TaxHarvestingSummaryDTO`.
3. **Repository Methods (`src/db/repository.py`):**
   - `record_daily_snapshot()`: Upserts daily snapshots by `(user_id, snapshot_date)`.
   - `get_daily_snapshots()`: Retrieves chronological daily snapshot series.
   - `record_cash_flow()`: Appends cash flow records.
   - `get_cash_flows()`: Queries cash flows ordered by date.
   - `get_realized_ltcg_ytd()`: Queries accumulated realized LTCG for the current financial year.
4. **Verification & Testing:**
   - Extended `tests/test_migrations.py` with `test_phase_4_table_structures`.
   - Extended `tests/test_repository.py` with snapshot, cash flow, and LTCG tests.
   - Verified clean application of migrations `017` and `018` against PostgreSQL.
   - All 197 tests passing with 100% safety-critical coverage.
