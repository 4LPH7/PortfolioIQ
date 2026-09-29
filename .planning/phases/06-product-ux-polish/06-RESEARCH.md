# Phase 6: Product & UX Polish - Technical Research

**Researched:** 2026-09-29  
**Status:** Complete  

## Executive Summary

Phase 6 delivers a cohesive, production-grade user experience for PortfolioIQ across its web interfaces, client telemetry, and reporting pipelines. It bridges the gap between high-powered backend analytics and an intuitive, accessible, and delightful end-user platform.

---

## 1. Authentication & Session Management Architecture

### Current State
- API routes are protected by `@require_api_key` in `src/api/middleware.py`, validating the `X-API-Key` header against `settings.portfolioiq_api_key`.
- `frontend/js/api.js` currently stores the key in `localStorage` under `portfolioiq_api_key`, with a fallback to `dev-secret-key`.
- There is no authentication check on page load, no auto-lock on inactivity, and no user-facing login form.

### Target Architecture
1. **Backend Verification Route**:
   - `POST /api/v1/auth/verify`: Accepts `{"api_key": "..."}` or checks header `X-API-Key`. Returns `{"ok": true, "status": "authenticated"}` on success, or 401 `{"error": {"code": "UNAUTHORIZED", "message": "Invalid API Key"}}`.
2. **Client Session Manager (`frontend/js/session.js`)**:
   - Uses `sessionStorage` for storing the active session token (scoped to browser tab lifetime, preventing leakage across sessions).
   - Implements an inactivity detector: Tracks `mousemove`, `keydown`, `click`, and `scroll`.
   - After 30 minutes of inactivity, clears `sessionStorage` and triggers the session lock dialog.
3. **Session Gate UI**:
   - A modal `<dialog id="session-modal">` included in all HTML pages.
   - If `sessionStorage.getItem("portfolioiq_session")` is absent or expired, the dialog is opened via `.showModal()`, preventing interaction with background elements.
   - Clean numeric/text PIN input with "Unlock Portfolio" button and error feedback.

---

## 2. Onboarding & Initial Portfolio Import Wizard

### Target Flow (`frontend/onboarding.html`)
A 3-step progressive wizard:
1. **Step 1: Broker & Execution Environment**:
   - Select between "Zerodha Kite Connect" (Live API) and "Paper Trading / Offline Mode".
   - Test broker connectivity and validate access token.
2. **Step 2: Holdings Ingestion**:
   - Option A: Click "Sync Live from Zerodha" (invoking `/api/v1/holdings/sync`).
   - Option B: Drag-and-drop CSV file uploader parsing Zerodha Holdings / Tradebook format.
   - Backend endpoint: `POST /api/v1/holdings/import-csv` parsing CSV rows into `user_holdings`.
3. **Step 3: Asset Allocation Targets**:
   - Sliders / inputs for equity targets (e.g. 80%), cash buffer (min 2% / ₹5,000), and max drift threshold (default 5.0%).
   - Persists targets into `app_config` table via existing repository methods.

---

## 3. In-App Notification Center & Alerting System

### Taxonomy of Alerts
| Type | Severity | Condition | Action |
|---|---|---|---|
| `DRIFT` | Warning / Critical | Holding allocation drift $> 5\%$ (Warning) or $> 10\%$ (Critical) | Quick link to `rebalance.html` |
| `PRICE_STALE` | Warning | Price quote timestamp $> 60\text{s}$ old during market hours | Quick link to trigger refresh |
| `TAX` | Info / Action | Unharvested loss lots ($> ₹5,000$) or lot within 30d of LTCG threshold | Quick link to `tax.html` |
| `SYSTEM` | Critical / Warning | Zerodha token expired or database connection issue | Link to `settings.html` / `status.html` |

### Architecture
- **Backend API**: `GET /api/v1/alerts`: Aggregates active drift alerts from `drift_detector.py`, price staleness from `live_prices`, tax harvesting opportunities from `tax_guard.py`, and system health.
- **Frontend Notification Drawer**:
  - Bell icon `🔔` in top navbar with real-time unread badge counter.
  - Clicking bell opens an accessible off-canvas drawer (`<div id="notification-drawer" role="dialog" aria-label="Notifications">`).
  - Filter tabs: `All`, `Drift`, `Price`, `Tax`, `System`.
  - Dismiss single alert or "Clear All" with persistent dismissed ID tracking in `localStorage`.
- **Toasts**: Floating container (`<div id="toast-container" aria-live="polite">`) rendering non-intrusive notification bubbles with 4s auto-fade.

---

## 4. Multi-Format Data Export & Reporting Hub

### 1. Client-Side Universal CSV Exporter (`frontend/js/export.js`)
- Standardized utility:
  ```javascript
  function exportDataToCSV(filename, headers, rows) {
    const csvContent = [
      headers.map(h => `"${h.replace(/"/g, '""')}"`).join(","),
      ...rows.map(row => row.map(cell => `"${String(cell ?? '').replace(/"/g, '""')}"`).join(","))
    ].join("\n");
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    link.click();
    URL.revokeObjectURL(link.href);
  }
  ```
- Export buttons added to:
  - `index.html`: "Export Holdings CSV"
  - `rebalance.html`: "Export Rebalance Plan CSV"
  - `tax.html`: "Export Tax Lots CSV"
  - `analyzer.html`: "Export Backtest Runs CSV"
  - `audit.html`: "Export Audit Trail CSV"

### 2. Print-Ready Performance & Tax Statement (`frontend/report.html`)
- Dedicated printable monthly statement layout.
- High-fidelity print styles (`@media print`):
  - Hides sidebars, navigation, refresh buttons, and action bars.
  - Page header: PortfolioIQ logo, user identifier, report generation date, and portfolio AUM.
  - Section 1: Executive Performance Summary (TWR, XIRR, Sharpe, Sortino, Drawdown vs NIFTY 50 TRI).
  - Section 2: Realized & Unrealized Capital Gains (STCG, LTCG vs ₹1.25L exemption, tax-loss harvest savings).
  - Section 3: Rebalance and Transaction Cost Attribution (STT, DP charges, GST).
  - Section 4: Mandatory SEBI algorithmic trading and financial disclaimer.

---

## 5. Portfolio Timeline & Interactive Equity Curve

### Page Architecture (`frontend/timeline.html`)
- Displays cumulative unitized NAV curve alongside NIFTY 50 TRI benchmark curve fetched from `GET /api/v1/analytics/snapshots`.
- Interactive Plotly chart with custom marker pins:
  - 🟢 **Deposit Pin**: Green arrow up at snapshot date when `net_external_flow > 0`.
  - 🔴 **Withdrawal Pin**: Red arrow down when `net_external_flow < 0`.
  - ⚖️ **Rebalance Pin**: Blue diamond on dates when orders were executed from `broker_executions`.
  - 🛡️ **Tax Harvest Pin**: Purple badge on dates with tax-loss harvest transactions.
- Historical event table listing all historical events with dates, types, amounts, and notes.

---

## 6. User-Facing System Status Dashboard

### Page Architecture (`frontend/status.html`)
- Real-time system telemetry page querying `GET /api/v1/system/status`:
  - **API Engine**: Health status, round-trip latency (measured in ms via client ping).
  - **Zerodha Kite Broker**: Auth status, session token validity, and time remaining until 06:00 IST daily expiration.
  - **PostgreSQL Database**: Connection pool status, active connections, total tables, and last migration applied.
  - **Market Status**: NSE market open/closed status, countdown timer to 09:15 IST opening or 15:30 IST closing, and next holiday name from `market_calendar`.
  - **Background Scheduler**: Status of cron jobs (`daily_eod_snapshot` at 16:00, `daily_signals` at 16:15, `mature_forward_returns` at 16:30, `partition_maintenance` at 00:00).

---

## 7. Mobile Navigation & Accessibility (WCAG 2.1 AA)

### Responsive Navigation
- In `frontend/css/main.css`:
  - On screens $< 768\text{px}$:
    - Sidebar converts into an off-canvas drawer (`transform: translateX(-100%)`).
    - Top bar adds a hamburger menu button `☰` to slide out sidebar with smooth CSS transitions.
    - Bottom navigation bar fixed to screen bottom (`🏠 Dashboard`, `🔬 Signals`, `⚖️ Rebalance`, `🛡️ Tax`, `☰ More`).
- High-contrast visual tokens:
  - Surface: `#0f172a` (slate-900), Card: `#1e293b` (slate-800), Border: `#334155` (slate-700).
  - Text primary: `#f8fafc` (contrast ratio 15.8:1 against slate-900).
  - Text secondary: `#94a3b8` (contrast ratio 5.1:1, exceeds 4.5:1 minimum).
  - Focus outlines: `outline: 2px solid #38bdf8; outline-offset: 2px;` for all keyboard focusable controls.

---

## 8. Wave Decomposition & Implementation Plan

- **Wave 1: Core Backend Endpoints & Client Utilities**
  - **Plan 06-01**: Auth verification, alerts aggregation, and system telemetry REST endpoints (`src/api/v1/blueprint.py`, `src/models/dtos.py`).
  - **Plan 06-02**: Client session management, auto-inactivity lock, universal CSV exporter, and toast system (`frontend/js/session.js`, `frontend/js/export.js`, `frontend/js/api.js`).
- **Wave 2: Onboarding & Alerting Center**
  - **Plan 06-03**: 3-step onboarding wizard and CSV holdings importer (`frontend/onboarding.html`, `POST /api/v1/holdings/import-csv`).
  - **Plan 06-04**: In-app notification bell drawer and real-time alert polling engine across all frontend pages (`frontend/css/main.css`, `frontend/js/alerts.js`).
- **Wave 3: Portfolio Timeline & Status Dashboard**
  - **Plan 06-05**: Dedicated Portfolio Timeline page with interactive equity curve and event markers (`frontend/timeline.html`).
  - **Plan 06-06**: User-facing live System Status dashboard (`frontend/status.html`).
- **Wave 4: Reporting & UX / Accessibility Pass**
  - **Plan 06-07**: Printable Monthly Performance & Tax Statement report template (`frontend/report.html`) and universal CSV export integration across all data tables.
  - **Plan 06-08**: Mobile responsive navigation (drawer + bottom bar) and WCAG 2.1 AA accessibility audit & test verification.
