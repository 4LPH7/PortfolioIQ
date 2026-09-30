---
phase: 06-product-ux-polish
plan: 05
status: complete
commits: 1
completed_at: 2026-09-30T20:12:00+05:30
---

# Plan 06-05: Dedicated Portfolio Timeline Page & Interactive Equity Curve Summary

## Overview

Plan 06-05 created the dedicated Portfolio Timeline page (`frontend/timeline.html`), rendering an interactive Plotly equity curve comparing cumulative unitized portfolio NAV progression against the NIFTY 50 TRI benchmark, annotated with event pin markers for cash inflows, withdrawals, and executed orders, accompanied by a chronological event log table and universal sidebar navigation integration.

## Key Accomplishments

1. **Portfolio Timeline View (`frontend/timeline.html`):**
   - Interactive Plotly chart with dual time-series: Portfolio Unit NAV (normalized to base 100 with subtle gradient area fill) and NIFTY 50 TRI (dashed benchmark line).
   - Event pin markers for cash deposits/dividends (green upward triangles), cash withdrawals (red downward triangles), and rebalance order executions (amber diamonds).
   - Timeframe range filtering buttons (`1M`, `3M`, `6M`, `1Y`, `ALL`) dynamically rescaling the equity curve and normalizing from the active start date.
   - KPI summary grid displaying Current Unit NAV (₹), Total Time-Weighted Return (TWR %), Benchmark Return (%), Strategy Alpha (%), and Maximum Drawdown (%).
   - Chronological portfolio event history table displaying dates, categories, action descriptions, amounts with currency formatting, and unit NAV values.
   - Direct CSV export integration (`data-export="timeline-events-table"`) utilizing `frontend/js/export.js`.
   - Built-in graceful synthetic demo baseline for environments where historical snapshots have not yet accumulated.

2. **Universal Sidebar Navigation Integration:**
   - Added `📅 Portfolio Timeline` (`timeline.html`) to the sidebar across all core views:
     - `frontend/index.html`
     - `frontend/analyzer.html`
     - `frontend/rebalance.html`
     - `frontend/tax.html`
     - `frontend/audit.html`
     - `frontend/settings.html`
     - `frontend/timeline.html`
   - Verified active state highlighting on `timeline.html` and inactive states on all other pages.

3. **Script Order & Dependencies:**
   - Ensured strict dependency loading sequence:
     `session.js` &rarr; `export.js` &rarr; `alerts.js` &rarr; `api.js` &rarr; inline application logic.
   - Bell notification drawer and toast systems fully bound and functional.

4. **Testing & Verification (`tests/test_timeline_page.py`):**
   - 9 automated unit and DOM validation tests checking:
     - Existence and structural completeness of `timeline.html`
     - Plotly library inclusion and script execution order
     - KPI cards and timeframe control IDs
     - Chart element and exportable data table binding
     - API integration paths (`/analytics/snapshots`, `/portfolio/cash-flows`, `/audit/orders`)
     - Universal sidebar links across all 7 views and active page assertions
   - Phase 6 test suite: 60/60 tests passed.
   - 0 Ruff lint or format errors.
   - 100% safety-critical path test coverage preserved.
