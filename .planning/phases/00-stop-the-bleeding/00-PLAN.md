# Phase 0 Plan: Stop the Bleeding (Security & Correctness)

## Overview
**Goal:** nothing embarrassing or dangerous is sitting in the repo. Timeframe: 1–3 days.
**Definition of Done:** `git log -p | gitleaks` comes back clean, every endpoint that touches the database returns real data instead of a 500, and no live order can be placed even if application code has a bug.

## 1. Secrets Management & Git History Scrubbing
- [ ] **1.1 Revoke KITE_API_KEY**: Revoke the currently exposed key on the Zerodha developer portal.
- [ ] **1.2 Rotate SUPABASE_KEY and DB passwords**: Reset the database password and API keys in the Supabase dashboard.
- [ ] **1.3 Scrub Git History**: Run `git filter-repo` or BFG to purge `.env` and `render.yaml` hardcoded secrets from all commits.
- [ ] **1.4 Move Secrets to Environment**: Ensure `KITE_API_KEY`, `KITE_API_SECRET`, `SUPABASE_KEY`, `DATABASE_URL`, and `FLASK_SECRET_KEY` are only ever read from `os.environ` and documented in `.env.example`.
- [ ] **1.5 Add Git Hooks**: Configure a `.pre-commit-config.yaml` using `detect-secrets` or `gitleaks` to block any future commits containing secrets.

## 2. Schema Reconciliation
- [ ] **2.1 Standardize Schema**: Enforce `supabase_schema.sql` as the ultimate source of truth.
- [ ] **2.2 Update Flask Endpoints**: Update all validation and audit queries in `flask_app.py` to match the exact table structures and column names in the schema.
- [ ] **2.3 Fix Gatekeeper Inserts**: Correct `insert` statements in `src/execution/gatekeeper.py` to align with the schema.
- [ ] **2.4 Fix Order Router Inserts**: Correct `insert` statements in `src/execution/order_router.py`.
- [ ] **2.5 Update Drift Detector**: Refactor `src/analytics/drift_detector.py` to use correct column references (change `tradingsymbol`/`min_weight_pct`/`max_weight_pct` to `instrument_token`/`target_weight_pct`/`drift_threshold_pct`).

## 3. Infrastructure & Safety Enforcement
- [ ] **3.1 Fix Scheduler Jobs**: Fix the `create_next_partition()` call in `src/scheduler/jobs.py` to accurately reference `create_price_partition(...)`.
- [ ] **3.2 Restrict CORS**: Update `flask_app.py` to read `ALLOWED_ORIGINS` from environment instead of allowing `*`.
- [ ] **3.3 Force DRY_RUN_MODE**: Hardcode or forcefully assert `DRY_RUN_MODE=true` at the application startup/infrastructure level so no live trades can be executed during this stabilization period.

## Verification
- Verify `git log -p | gitleaks` returns 0 leaks.
- Run test suite (or execute endpoints manually) to ensure 200 OK across the board.
- Verify `DRY_RUN_MODE` strictly prevents real order execution.
