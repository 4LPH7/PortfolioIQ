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
**Status:** Unplanned
**Goal:** the numbers on screen are numbers you'd trust with real money. Timeframe: 1–2 weeks.

- Stop using Yahoo Finance for execution-critical prices — switch to Zerodha's own market data for slippage checks, order pricing, and market-status confirmation.
- Add a price freshness policy.
- Add post-sync holdings reconciliation.
- Add corporate-action reconciliation tests.
- Replace hard-coded yearly NSE/BSE holiday calendars with a maintained, exchange-sourced calendar.
- Add handling for symbol changes and delisted instruments.

## Phase 4: Portfolio Analytics That Actually Mean Something
**Status:** Unplanned
**Goal:** move past "P&L and a pie chart" into real portfolio science. Timeframe: 2–3 weeks.

- Add to analytics: TWR, IRR, CAGR, Sharpe, Sortino, max drawdown, Beta, alpha, Realized P&L, Tax/expense adjusted returns.
- Add to rebalancer: Transaction cost modeling, Tax-aware rebalancing, Minimum trade value, Liquidity constraints, Cash reserve constraints.

## Phase 5: Make the Signal Engine Evidence-Based
**Status:** Unplanned
**Goal:** stop calling heuristics "predictions." Timeframe: 3–4 weeks.

- Build a historical dataset of past signals and outcomes.
- Run walk-forward backtests.
- Evaluate indicators independently before trusting composite.
- Compare composite performance against a naive benchmark (buy-and-hold NIFTY 50).
- Calibrate probability outputs.
- Store model/version metadata with every signal.
- Rename UI language from "prediction" to "signal".
- Never present Monte Carlo percentile bands as forecasts without calibration against realized outcomes.

## Phase 6: Product & UX Polish
**Status:** Unplanned
**Goal:** it feels like a finished product, not a prototype. Timeframe: 2–3 weeks.

- Add login/session management.
- Add onboarding + initial portfolio import.
- Add a portfolio timeline / history view.
- Add alerting for drift thresholds, stale prices, or failed syncs.
- Add export to CSV/Excel/PDF.
- Fix mobile navigation.
- Accessibility pass.
- Add a user-facing system status page.

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
