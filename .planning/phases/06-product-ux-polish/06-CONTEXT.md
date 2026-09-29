# Phase 6: Product & UX Polish - Context

**Gathered:** 2026-09-29  
**Status:** Ready for planning  

<domain>
## Phase Boundary

Phase 6 elevates PortfolioIQ from an algorithmic developer prototype into a finished, polished, and responsive institutional-grade application.
This encompasses:
1. **Authentication & Session Management**:
   - Clean Login/Unlock screen prompting for Master Key/PIN.
   - Session tokens managed via `sessionStorage` with automatic lock after 30 minutes of inactivity.
   - Dual-surface enforcement across static web UI (`frontend/`) and Streamlit dashboard (`ui/`).
2. **Onboarding & Initial Portfolio Import**:
   - 3-step onboarding wizard for first-time setup: (1) Broker credentials test (Kite Connect or Demo/Offline), (2) Holdings import (Zerodha sync or CSV upload), (3) Target Asset Allocation & risk profile definition.
3. **In-App Notification Center & Alerting**:
   - Navbar Notification Drawer (bell icon with unread badge) categorized by alert severity (Drift Warning, Price Stale, Tax Action, System Health).
   - Dismissible, non-blocking toast notifications for real-time alerts.
4. **Data Export & Reporting Hub**:
   - Instant client-side CSV downloads for all core tables (Holdings, Rebalance Manifest, Tax Lots, Audit Trail, Backtests).
   - Clean print-ready HTML/PDF report template for Monthly Portfolio Performance & Capital Gains Tax Statement.
5. **Portfolio Timeline & Historical Equity Curve**:
   - Dedicated Timeline view rendering interactive portfolio NAV equity curve vs NIFTY 50 TRI benchmark.
   - Interactive event markers along the equity curve for cash flows (deposits/withdrawals), rebalance executions, and tax-loss harvests.
6. **User-Facing System Status Page**:
   - Dedicated system health page showing API roundtrip latency, Kite broker session status & token expiry countdown, Database connection pool health, Market hours timer, and APScheduler background job states.
7. **Mobile Navigation & Accessibility**:
   - Fully responsive layout with mobile drawer / bottom navigation bar for mobile viewports (< 768px).
   - WCAG 2.1 AA accessibility compliance: semantic HTML5 landmarks, ARIA labels on dynamic drawers and canvas charts, keyboard navigation tab stops, and color contrast validation.

</domain>

<decisions>
## Implementation Decisions

### Authentication & Session Management
- **D-01:** Dedicated Login Screen with Session Storage:
  - Implement a modern modal/page gating access until valid `X-API-Key` / Master PIN is supplied.
  - Store validated credential in `sessionStorage` (cleared on browser tab close) with a 30-minute inactivity timer that prompts for PIN re-entry.
  - Backend endpoint `POST /api/v1/auth/verify` to validate API key without mutating state.
  - — **Reversibility:** reversible — UI session gating backed by existing API key middleware.

### Onboarding & Portfolio Import
- **D-02:** 3-Step Guided Onboarding Wizard:
  - Step 1: Connect Broker (Zerodha Kite API credentials test or Demo/Paper Trading toggle).
  - Step 2: Ingest Holdings (Live Kite holdings sync or Drag-and-Drop Zerodha Tradebook/Holdings CSV parser).
  - Step 3: Portfolio Configuration (Target asset allocation weights, cash buffer %, drift threshold %).
  - Store completion state in `app_config` so onboarding only runs once or when reset from Settings.
  - — **Reversibility:** reversible — wraps existing ingestion and config repository methods.

### In-App Notification Center & Alerting
- **D-03:** Notification Bell Drawer & Alert Taxonomy:
  - Notification icon in header with unread badge counter.
  - Filterable by categories:
    - `DRIFT`: Assets exceeding allocation drift threshold (> 5% deviation).
    - `PRICE_STALE`: Holdings with prices older than 60s during market hours.
    - `TAX`: Unharvested loss lots or lots within 30 days of the 365-day LTCG threshold.
    - `SYSTEM`: Broker token expiration, database disconnections, or sync failures.
  - Dismissible toast notifications for high-priority real-time triggers.
  - — **Reversibility:** reversible — UI state engine fed by existing `/api/v1/alerts` or client-side evaluation.

### Data Export & Reporting Hub
- **D-04:** Multi-Format Client-Side & Print-Ready Export:
  - Instant CSV export for tables: Holdings, Rebalance Proposals, Tax Lots, Execution Audit Log, and Walk-Forward Backtest Runs.
  - Print-ready HTML/PDF stylesheet (`@media print`) rendering a formal, branded "Monthly Portfolio & Tax Performance Statement" with disclaimer, TWR/XIRR, drawdowns, and LTCG tax bracket usage.
  - — **Reversibility:** reversible — presentation and client-side data serialization.

### Portfolio Timeline & System Status
- **D-05:** Interactive Timeline View:
  - Render historical unitized NAV curve alongside NIFTY 50 TRI benchmark using Chart.js / Plotly.
  - Interactive pins on the timeline indicating: External Cash Flows (Deposit/Withdrawal), Executed Rebalance Orders, and Tax-Loss Harvests.
- **D-06:** User-Facing System Status Page:
  - Display live engine diagnostics: API Health & Latency, Kite Connect Session Status, Database Connection Pool, Market Open/Closed Countdown, and Scheduler Cron Status.
  - — **Reversibility:** reversible — connects to `/api/v1/health`, `/api/v1/market/status`, and scheduler telemetry.

### Mobile Navigation & Accessibility
- **D-07:** Responsive Layout & Mobile Navigation:
  - Mobile bottom navigation bar / slide-out drawer on screens < 768px.
  - Sticky header with quick portfolio equity summary and notification bell.
- **D-08:** Accessibility (WCAG 2.1 AA):
  - High-contrast color tokens adhering to 4.5:1 text contrast ratios.
  - Focus outlines and keyboard accessibility (`Enter`/`Space`/`Esc`) for all modals, drawers, and tabs.
  - Accessible names and ARIA live regions for dynamically updating prices and alert counts.

### the agent's Discretion
- Exact CSS layout styling, animations, and micro-interactions adhering to existing PortfolioIQ design language (dark mode, clean typography, financial slate accents).
- Client-side CSV generation helper implementation (lightweight vanilla JS utility).
- Print CSS stylesheet structuring for crisp A4 paper pagination.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Frontend Codebase
- `frontend/index.html` — Main dashboard view (equity curve, allocation, holding table).
- `frontend/analyzer.html` — Quantitative signal view (Student's t cones, indicator breakdown, backtests).
- `frontend/rebalance.html` — Rebalance order preview and execution manifest.
- `frontend/tax.html` — Tax-loss harvesting, lot explorer, and FY exemption tracker.
- `frontend/settings.html` — Configuration, API keys, and system preferences.
- `frontend/audit.html` — Immutable order execution log and gatekeeper audit trail.
- `frontend/css/main.css` — Core styling, component classes, color tokens, and layout.
- `frontend/js/api.js` — Client API wrapper, authentication headers, error envelopes.

### Backend APIs & Telemetry
- `src/api/v1/blueprint.py` — Flask API v1 routes, auth middleware, and error handlers.
- `src/api/middleware.py` — API key validation and correlation IDs.
- `src/scheduler/jobs.py` — Background job schedules and execution tracking.
- `src/ingestion/market_hours.py` — Market hours, trading holiday calendar, and countdowns.
- `src/ingestion/kite_auth.py` — Zerodha Kite Connect session management and tokens.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `frontend/js/api.js`: Centralized fetch wrapper already injects `X-API-Key` and `X-Request-ID`. We can extend it with session timeout checks, token refresh, and alert polling.
- `frontend/css/main.css`: Comprehensive dark-mode design system with card primitives, badges, buttons, and tables.
- `src/api/v1/blueprint.py`: Contains endpoints for portfolio valuation, performance, snapshots, tax harvesting, rebalancing, and quantitative signals.

### Established Patterns
- Pydantic DTO serialization with `mode="json"`.
- Uniform error envelope: `{"error": {"code": "...", "message": "...", "details": ...}}`.
- Fail-closed execution and validation gates.

</code_context>
