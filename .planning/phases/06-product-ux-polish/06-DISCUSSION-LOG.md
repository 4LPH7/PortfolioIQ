# Phase 6: Product & UX Polish — Discussion Log

**Date:** 2026-09-29  
**Phase:** 06-product-ux-polish  
**Status:** Completed  

---

## Gray Areas & Decisions

### 1. Authentication & Session Management
- **Question:** How should authentication and session management be presented to the user on the frontend?
- **Options Presented:**
  1. *(Selected)* Dedicated Login screen + session token: Clean login page asking for Master Key/PIN, storing in `sessionStorage` with auto-lock after 30 min of inactivity.
  2. Cookie-based session: Backend `/api/v1/auth/login` issuing an HTTP-only secure cookie, supported by a standard login/logout flow.
  3. Navbar Quick-Unlock modal: Keep API key in `localStorage`, but lock the UI behind a quick PIN unlock modal upon tab opening.
- **Decision:** Dedicated Login screen asking for Master Key/PIN, stored in `sessionStorage` with automatic 30-minute inactivity lock. Backend provides `POST /api/v1/auth/verify`.

### 2. Onboarding & Initial Portfolio Import
- **Question:** How should the initial user onboarding and portfolio import experience be structured?
- **Options Presented:**
  1. *(Selected)* 3-Step Guided Wizard: (1) Broker API Connect or Demo Mode, (2) Holdings import (Zerodha sync or CSV upload), (3) Set target allocation & risk profile.
  2. One-Click Kite Sync: Directly authenticate Kite session and auto-import all holdings, with fallback to CSV upload in Settings.
  3. Dual Mode Wizard: Choose at welcome between 'Live Zerodha Broker Connect' and 'Offline / CSV Tracking Mode'.
- **Decision:** 3-step wizard guiding the user through credential verification, holdings ingestion (live Kite or CSV drag-and-drop), and target allocation setup.

### 3. Alerting System & Notifications
- **Question:** How should alerts (drift thresholds, stale quotes, broker token expiry, tax harvesting) be surfaced to the user?
- **Options Presented:**
  1. *(Selected)* In-app Notification Drawer (Bell icon with unread count, filterable by Severity: Drift, Price Stale, Tax, System) + Toast banners.
  2. In-app Notification Drawer + Telegram Bot / Webhook integration for remote alerts.
  3. Dashboard Banner bar: Prominent alert strip across top of dashboard for active issues, plus dismissible toast popups.
- **Decision:** In-app Notification Drawer with badge count and filtering by category (`DRIFT`, `PRICE_STALE`, `TAX`, `SYSTEM`), coupled with floating toast banners.

### 4. Data Export & Reporting Formats
- **Question:** What export formats and reporting capabilities should be provided?
- **Options Presented:**
  1. *(Selected)* Multi-format Export Hub: Instant CSV download buttons on all data tables + Clean print-ready HTML/PDF Portfolio Summary & Tax Report statement.
  2. Server-generated PDF and Excel: Backend endpoints producing formal branded monthly statements.
  3. Client-side CSV only: Keep it lightweight with instant CSV download for Holdings, Rebalance Plan, and Tax Lots.
- **Decision:** Universal instant client-side CSV downloads on all tables plus a high-fidelity print-ready HTML/PDF report template with performance metrics, tax harvesting summary, and disclaimers.

### 5. Timeline / History View & System Status
- **Question:** How should the Portfolio Timeline view and System Status page be structured?
- **Options Presented:**
  1. *(Selected)* Dedicated Portfolio Timeline page (Equity curve vs NIFTY 50 TRI, cash flow event pins, rebalance markers) + System Status page (API latency, Kite session, DB, Market hours countdown).
  2. Timeline integrated directly into Dashboard as an expandable tab + System Status as a slide-out drawer from the navbar.
  3. System Status page only with live engine metrics, keeping Timeline history within the existing Audit Log page.
- **Decision:** Dedicated Portfolio Timeline page with benchmark comparison and event pins, and a dedicated System Status dashboard with real-time connectivity and scheduler diagnostics.

### 6. Mobile Navigation & Accessibility
- **Decision:** Mobile drawer / bottom navigation bar for viewports < 768px, and WCAG 2.1 AA compliant color contrast, focus outlines, and screen reader ARIA annotations.
