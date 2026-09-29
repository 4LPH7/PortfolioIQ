# PortfolioIQ Roadmap

A roadmap from the current early-stage prototype (~5/10 overall) to a production-grade, trustworthy personal trading platform.

## Phase 0: Stop the Bleeding (Security & Correctness)
**Status:** Complete
**Goal:** nothing embarrassing or dangerous is sitting in the repo. Timeframe: 1–3 days.

- [x] Revoke and rotate the exposed KITE_API_KEY in render.yaml immediately
- [x] Scrub credentials from git history (BFG Repo-Cleaner or git filter-repo / gitleaks CI check)
- [x] Move every secret (Kite key/secret, Supabase key, DB URL, Flask secret key) to Render/host-managed secret env vars (sync: false)
- [x] Rotate SUPABASE_KEY and any DB passwords that were ever committed, even historically
- [x] Add a .gitignore / pre-commit hook (e.g. detect-secrets or gitleaks) so this can't happen again
- [x] Reconcile the three schema definitions (Flask queries, supabase_schema.sql, execution modules) into one canonical schema — pick supabase_schema.sql as source of truth and update:
  - flask_app.py audit/validation queries
  - gatekeeper.py insert statements
  - order_router.py insert statements
  - drift_detector.py column references (tradingsymbol/min_weight_pct/max_weight_pct → instrument_token/target_weight_pct/drift_threshold_pct)
- [x] Fix the create_next_partition() call in scheduler/jobs.py to reference an actual function (create_price_partition(...))
- [x] Restrict CORS from origins="*" to an explicit allowlist read from ALLOWED_ORIGINS
- [x] Force DRY_RUN_MODE=true at the infrastructure level (env var, not just DB flag) until Phase 2 testing is complete

**Definition of done:** `git log -p | gitleaks` comes back clean, every endpoint that touches the database returns real data instead of a 500, and no live order can be placed even if application code has a bug. Verified in `00-VERIFICATION.md`.

## Phase 1: A Reliable Backend Core
**Status:** Complete
**Goal:** the backend is boring and predictable. Timeframe: 1–2 weeks.

- [x] Add a migration version table (e.g. Alembic or a simple schema_migrations table) so db/migrations/ is idempotent and re-runnable
- [x] Introduce typed SQLAlchemy models (or Pydantic DTOs) for every table — no more raw dict-shaped SQL results passed around
- [x] Centralize persistence into named functions instead of inline SQL scattered across modules:
  - `record_order_attempt(...)`
  - `record_validation_check(...)`
  - `record_broker_execution(...)`
  - `get_current_holdings(user_id) -> list[Holding]`
- [x] Add request/response schemas (Pydantic) for every Flask endpoint, with consistent error codes (`{"error": {"code": ..., "message": ...}}`)
- [x] Add structured logging with request/correlation IDs (Loguru is already a dependency — use it consistently)
- [x] Add real authentication (even a simple API-key-per-user scheme is fine at this stage) — no endpoint that syncs, rebalances, or places orders should be reachable unauthenticated
- [x] Add basic rate limiting (Flask-Limiter) on write endpoints
- [x] Version the API explicitly: `/api/v1/...`, so schema changes don't silently break the frontend

**Definition of done:** every endpoint has a typed contract, a bad request returns a structured 4xx instead of a stack trace, and no endpoint is callable without identifying who's calling it. Verified in `01-VERIFICATION.md`.

## Phase 2: Testing & CI/CD
**Status:** Complete
**Goal:** it's impossible to merge a schema-breaking change again. Timeframe: 1–2 weeks, ongoing after.

- [x] Set up pytest with a real PostgreSQL service container and nested savepoint rollbacks
- [x] Add a GitHub Actions workflow:
  - `ruff check .` and `ruff format --check .`
  - `pip-audit` dependency vulnerability scanning with pip cache
  - `pytest --cov` with 75% overall gate and 85% safety-critical enforcement (`scripts/check_critical_coverage.py`)
  - postgres service container + migration run (`db/run_migrations.py`)
  - docker build validation (`docker build -t portfolioiq:ci .`)
- [x] Make CI a required check on main (branch protection status checks documented)
- [x] Add a deployment smoke test (`scripts/smoke_test.py`) that hits `/api/v1/health` and `/api/v1/market/status` post-deploy and rolls back automatically to `HEAD~1` on failure
- [x] Track coverage over time; fail CI if safety-critical coverage regresses (100% achieved on execution, config, middleware)

**Definition of done:** All 166 tests pass cleanly with zero skipped and zero failed, safety-critical coverage is 100%, code formatting and linting is 100% clean under Ruff, and CI/CD enforces automated tests, migration checks, and post-deploy smoke tests with automated rollback. Verified in `02-VERIFICATION.md`.

## Phase 3: Data Quality & Market Infrastructure
**Status:** Complete
**Goal:** the numbers on screen are numbers you'd trust with real money. Timeframe: 1–2 weeks.

- [x] **Plan 03-01 (Wave 1):** Database Migrations (`015_holdings_reconciliation_log.sql`, `016_market_calendar.sql`), Static JSON Fallback (`src/config/nse_holidays.json`), Pydantic DTOs & Typed Repository Layer.
- [x] **Plan 03-02 (Wave 2):** Database-Backed Market Hours & Special Sessions Engine (`src/ingestion/market_hours.py`) with Caching & Offline JSON Fallback + Calendar REST API.
- [x] **Plan 03-03 (Wave 2):** Zerodha Kite Connect REST Batch Quote Poller Daemon (`src/ingestion/kite_quote_poller.py`) and Deprecation of Yahoo Finance Polling.
- [x] **Plan 03-04 (Wave 3):** Gatekeeper 60s Freshness Policy & Synchronous Fail-Closed On-Demand Broker Quote Refresh (`src/execution/validators/slippage_check.py`).
- [x] **Plan 03-05 (Wave 3):** Broker Holdings Reconciliation Runner (`src/ingestion/kite_sync.py`), Unexplained Quantity Jump Detection & Reconciliation Audit API.

**Definition of done:** All active equity holdings are polled via batched Kite REST quotes (< 7% rate limit consumption); Gatekeeper validates price freshness (< 60s) with fail-closed on-demand broker refresh; post-sync holdings reconciliation audits all settlement and corporate action transitions; exchange trading calendar is database-backed with Diwali Muhurat special session support and offline JSON fallback; all tests pass cleanly with >= 85% critical coverage. Verified in `03-VERIFICATION.md`.

## Phase 4: Portfolio Analytics That Actually Mean Something
**Status:** Complete
**Goal:** move past "P&L and a pie chart" into real portfolio science. Timeframe: 2–3 weeks.

- [x] **Plan 04-01 (Wave 1):** Historical NAV & Cash Flow Schema (`017_portfolio_snapshots.sql`, `018_portfolio_cash_flows.sql`), Pydantic DTOs & Typed Repository Layer.
- [x] **Plan 04-02 (Wave 2):** Quantitative Return & Attribution Engine (`src/analytics/performance.py`: TWR, XIRR solver, Sharpe, Sortino, Drawdown, Beta/Alpha).
- [x] **Plan 04-03 (Wave 2):** Daily Snapshot Recorder, Morning Cash Margin Delta Sync & Baseline Backfill (`src/analytics/snapshot_recorder.py`, `scheduler/jobs.py`).
- [x] **Plan 04-04 (Wave 3):** Indian Transaction Cost Model & Rebalancer Sizing Constraints (`src/analytics/cost_calculator.py` & `src/analytics/rebalancer.py`).
- [x] **Plan 04-05 (Wave 3):** Tax-Loss Harvesting Engine, 30-Day LTCG Lock & FY Exemption Tracker (`src/analytics/tax_guard.py`).
- [x] **Plan 04-06 (Wave 4):** Analytics REST Endpoints, Rebalance Preview & Full Verification (`src/api/v1/blueprint.py`).

**Definition of done:** True Time-Weighted Return (TWR) and Money-Weighted Return (XIRR) are tracked daily alongside NIFTY 50/500 TRI benchmarks; risk metrics (Sharpe, Sortino, Max Drawdown, Beta, Jensen's Alpha) are computed with a 30-day warmup gate; the rebalancer enforces realistic execution constraints (₹2,000 min trade, 2% cash buffer, 15% daily turnover cap, 1% ADV limit) and realistic Indian delivery fees (STT, DP charges, GST); capital gains tax-loss harvesting and annual ₹1.25L LTCG exemptions are automated; all tests pass cleanly with >= 85% critical coverage.

## Phase 5: Make the Signal Engine Evidence-Based
**Status:** Complete
**Goal:** stop calling heuristics "predictions." Timeframe: 3–4 weeks.

- [x] **Plan 05-01 (Wave 1):** Database Migrations (`019_signal_snapshots.sql`, `020_backtest_runs.sql`, `021_historical_daily_bars.sql`), Pydantic DTOs & Typed Repository Layer.
- [x] **Plan 05-02 (Wave 2):** Historical Price Caching & Incremental Bar Sync (`src/analytics/historical_cache.py`, `src/ingestion/ticker_map.py`).
- [x] **Plan 05-03 (Wave 2):** Quantitative Indicator Evaluator, Spearman Rank IC Engine & Dynamic IR Weighting (`src/analytics/indicator_evaluator.py`).
- [x] **Plan 05-04 (Wave 3):** Rolling Walk-Forward Backtester, Dual Baseline Attribution & Delivery Cost Net Returns (`src/analytics/walk_forward.py`).
- [x] **Plan 05-05 (Wave 3):** Calibrated Fat-Tailed Monte Carlo Simulation & Percentile Dispersion (`src/analytics/calibrated_monte_carlo.py`).
- [x] **Plan 05-06 (Wave 4):** Rebalancer Evidence Hurdle Gate & Fail-Closed Order Suppression (`src/analytics/rebalancer.py`).
- [x] **Plan 05-07 (Wave 4):** Signal Scheduler Jobs, REST Endpoints & Systematic Terminology Migration (`src/scheduler/jobs.py`, `src/api/v1/blueprint.py`).

**Definition of done:** Technical indicators are evaluated independently via out-of-sample Spearman rank IC with negative-alpha/insignificant indicators automatically pruned; rolling walk-forward backtests (252-train / 63-test) evaluate strategy net returns against dual baselines (Stock B&H and NIFTY 50 TRI B&H) with realistic Indian transaction costs; Monte Carlo simulations use Student's t distribution with empirical coverage calibration (80%/95% cones) and dispersion percentiles (P10..P90) with point estimates banned; rebalancer strictly suppresses trade proposals on signals tagged as `UNPROVEN_NOISE`; signal snapshots track 5d, 20d, and 60d forward returns; all tests pass cleanly with >= 85% critical coverage. Verified in `05-VERIFICATION.md`.

## Phase 6: Product & UX Polish
**Status:** Planned
**Goal:** it feels like a finished product, not a prototype. Timeframe: 2–3 weeks.

- [ ] **Plan 06-01 (Wave 1):** Backend Auth Verification, Alert Aggregator & System Telemetry REST APIs (`src/api/v1/blueprint.py`, `src/models/dtos.py`).
- [ ] **Plan 06-02 (Wave 1):** Client Session Management, Auto-Inactivity Lock, Universal CSV Exporter & Toast System (`frontend/js/session.js`, `frontend/js/export.js`, `frontend/js/api.js`).
- [ ] **Plan 06-03 (Wave 2):** 3-Step Guided Onboarding Wizard & CSV Portfolio Holdings Importer (`frontend/onboarding.html`, `POST /api/v1/holdings/import-csv`).
- [ ] **Plan 06-04 (Wave 2):** In-App Notification Bell Drawer & Real-Time Alert Poller (`frontend/js/alerts.js`, `frontend/css/main.css`).
- [ ] **Plan 06-05 (Wave 3):** Dedicated Portfolio Timeline Page & Interactive Equity Curve with Event Pins (`frontend/timeline.html`).
- [ ] **Plan 06-06 (Wave 3):** User-Facing Live System Status Dashboard (`frontend/status.html`).
- [ ] **Plan 06-07 (Wave 4):** Printable Monthly Performance & Tax Statement Report & CSV Export Buttons (`frontend/report.html`, `frontend/css/main.css`).
- [ ] **Plan 06-08 (Wave 4):** Responsive Mobile Navigation & WCAG 2.1 AA Accessibility Pass (`frontend/css/main.css`, `tests/test_a11y_and_structure.py`).

**Definition of done:** Authentication gates UI access behind a Master Key/PIN with 30-minute inactivity auto-lock; new portfolios can be ingested via live Zerodha sync or CSV drag-and-drop wizard; alerts for drift, stale prices, tax loss opportunities, and system health are surfaced via topbar notification drawer and toasts; all tables support instant CSV export; a formal print-ready monthly performance & tax statement is available; timeline renders NAV equity curve vs NIFTY 50 TRI with event markers; system status exposes real-time telemetry; mobile navigation is fully responsive and WCAG 2.1 AA accessibility standards are met; all tests pass cleanly with >= 85% critical coverage.

## Phase 7: Multi-User & Scale
**Status:** Unplanned
**Goal:** the schema's multi-user design stops being theoretical.

- Map authenticated identity → user_id everywhere.
- Enforce row-level security in Postgres.
- Isolate broker sessions, holdings, and margins per user.
- Add tenant-aware tests.

## Phase 8: Formal Trading Safety Certification
**Status:** Unplanned
**Goal:** a documented, auditable answer to "why is it safe to let this place real orders?"

- Write a startup health check that refuses to boot in live mode if unsafe.
- Require 100% test coverage on: gatekeeper validators, order router, audit logging.
- Add a manual "graduation checklist" document.
- Add real-time alerting if the audit trail insert ever fails.
- Run at least 2–4 weeks of dry-run operation against real market data with zero unexplained failures.
