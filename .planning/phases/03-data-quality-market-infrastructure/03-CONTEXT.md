# Phase 3: Data Quality & Market Infrastructure - Context

**Gathered:** 2026-09-27
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 3 establishes trustworthy, production-grade market data, portfolio reconciliation, and exchange trading infrastructure for PortfolioIQ.
This encompasses:
1. Deprecating Yahoo Finance for live market pricing in favor of direct, batched REST polling via Zerodha Kite Connect (`kite.ltp()` / `kite.quote()`).
2. Instituting a 60-second price freshness policy with an on-demand broker refresh mechanism in the Gatekeeper (failing closed if broker refresh fails).
3. Implementing post-sync holdings reconciliation between local database state and Kite Connect holdings, recording discrepancies in a dedicated `holdings_reconciliation_log` audit table and alerting on unexplained quantity jumps (splits, bonus shares).
4. Replacing hard-coded yearly holiday calendars with a database-backed `market_calendar` table supporting special trading sessions (e.g., Muhurat trading), with bundled static JSON fallback and dynamic API management endpoints.
5. Handling symbol renames, ISIN mapping, and delisted instruments.

</domain>

<decisions>
## Implementation Decisions

### Live Market Data Architecture
- **D-01:** Batch REST Polling via Kite Connect: Replace Yahoo Finance polling (`src/ingestion/yahoo_poller.py`) with a dedicated Kite market quote poller (`src/ingestion/kite_quote_poller.py`). During NSE market hours, poll `kite.ltp()` / `kite.quote()` every 10–30 seconds in a single batched API call for all portfolio holdings and watchlist symbols. Fully deprecate Yahoo Finance for execution prices and database writes; retain Yahoo Finance strictly as an optional historical backfill fallback if ever needed. — **Reversibility:** costly — changes live ingestion daemons and background scheduler jobs.

### Price Freshness Policy & Gatekeeper Behavior
- **D-02:** Staleness Threshold: Any price in `live_prices` with a `recorded_at` timestamp older than 60 seconds during active market hours is classified as stale (`is_stale = true`). Outside market hours, the previous session's closing price is valid and not marked stale.
- **D-03:** Synchronous On-Demand Refresh with Fail-Closed Fallback: In `src/execution/validators/slippage_check.py`, if an order's instrument price is stale (> 60s) or missing during market hours, the Gatekeeper triggers an immediate on-demand `kite.ltp([symbol])` quote fetch. If the fetch succeeds, the order is validated against the fresh live price and `live_prices` is updated. If the fetch fails, times out, or the broker is unreachable, the order is REJECTED (fail closed) and logged to the validation audit trail with reason `"Price is stale (>60s) and on-demand broker refresh failed"`. — **Reversibility:** costly — touches safety-critical execution validation chain.

### Holdings & Corporate Actions Reconciliation
- **D-04:** Authoritative Broker Sync with Reconciliation Audit Trail: Zerodha Kite (`kite.holdings()`) is the authoritative source of truth for settled equity holdings and T1 quantities. During each portfolio sync (`src/ingestion/kite_sync.py`), the system compares the broker response against the local `user_holdings` database state.
- **D-05:** Holdings Reconciliation Log: Introduce a new database migration creating `holdings_reconciliation_log` to record all detected discrepancies (instrument token, tradingsymbol, old quantity, new quantity, old average price, new average price, delta, reconciliation reason, timestamp). Reasons include `"T1_SETTLEMENT"`, `"TRADE_FILL"`, `"CORPORATE_ACTION_SPLIT"`, `"EXTERNAL_TRANSFER"`, `"INITIAL_SYNC"`.
- **D-06:** Unexplained Quantity Jump Alerts: When a holding's quantity changes without a corresponding internal trade execution record, flag it as a potential corporate action (split or bonus) and emit a high-severity alert/log.
- **D-07:** Canonical State Upsert: Once reconciliation is recorded, upsert the latest verified holdings into `user_holdings` so rebalancer, risk models, and valuation endpoints always operate on broker-verified positions.

### Exchange Trading Calendar & Special Sessions
- **D-08:** DB-Backed Market Calendar (`market_calendar`): Create a new database migration for `market_calendar` storing holiday dates, names, segment (`equity`), holiday types (full day closure, special session), and custom trading session windows (e.g. Muhurat trading opening and closing times).
- **D-09:** Bundled JSON Fallback: Provide a bundled static fallback file (`src/config/nse_holidays.json`) so the system operates reliably offline, in isolated CI test environments, or if remote sync is unreachable.
- **D-10:** API Management & Ingestion Integration: Expose `/api/v1/market/calendar` endpoints to view and update the calendar dynamically. Refactor `src/ingestion/market_hours.py` to query `market_calendar` (with in-memory caching and JSON fallback).

### Symbol Changes & Delisted Instruments
- **D-11:** Instrument Inactivation: When an instrument is delisted or suspended, flag `is_active = false` in `instruments_master` / `user_holdings`. Gatekeeper must reject any order for an inactive instrument.

### Downstream Agent Discretion
- Exact polling intervals (defaulting to 15 seconds) configured via `system_config` / `Settings`.
- Choice of SQL schema constraints and indexes for `market_calendar` and `holdings_reconciliation_log`.
- DTO model definitions and repository functions for new tables.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Execution & Architecture
- `.planning/ROADMAP.md` § Phase 3 — Baseline scope and deliverables for data quality & market infrastructure.
- `src/execution/validators/slippage_check.py` — Current slippage check validator to be updated with on-demand refresh and fail-closed logic.
- `src/execution/gatekeeper.py` — Gatekeeper validation pipeline.
- `src/ingestion/market_hours.py` — Current market hours and hardcoded holiday calendar to be replaced by DB-backed calendar.
- `src/ingestion/kite_sync.py` — Broker holdings and margins sync runner.
- `src/ingestion/yahoo_poller.py` — Legacy Yahoo Finance price poller to be deprecated.
- `src/config/settings.py` — Configuration settings and kill switches.

### Database & Schema
- `db/migrations/*.sql` — Existing 14 canonical database migrations.
- `src/db/repository.py` — Centralized typed persistence layer.
- `src/models/dtos.py` — Typed Pydantic DTO models.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/ingestion/kite_auth.py`: `get_authenticated_kite()` provides the authenticated Kite Connect client instance.
- `src/db/repository.py`: DTO repository pattern established in Phase 1 with session-managed transactions.
- `tests/conftest.py`: PostgreSQL test harness with migration runner and transactional savepoints.
- `scripts/check_critical_coverage.py`: Gate script enforcing coverage standards.

### Established Patterns
- Migration files in `db/migrations/` sequentially numbered (`015_...sql`, `016_...sql`), tracked via `schema_migrations` with SHA-256 checksums.
- Fail-closed execution safety: if market data or broker connectivity fails, orders are rejected, not allowed to pass.
- Pydantic DTOs for all database reads and writes.

### Integration Points
- `src/ingestion/kite_quote_poller.py` replaces `src/ingestion/yahoo_poller.py` in background polling and scheduler jobs.
- `src/execution/validators/slippage_check.py` connects to `get_authenticated_kite()` for on-demand quotes.
- `src/ingestion/market_hours.py` connects to `market_calendar` table.
- `src/api/v1/blueprint.py` exposes `/api/v1/market/calendar` and `/api/v1/holdings/reconciliation`.

</code_context>

<specifics>
## Specific Ideas
- Batch `kite.ltp(["NSE:INFY", "NSE:TCS", ...])` sends up to 500 symbols in a single HTTP request, keeping API usage well within Kite Connect's 1 req/sec rate limit.
- Muhurat trading (Diwali evening 1-hour special session) needs explicit open and close timestamps in `market_calendar`.

</specifics>

<deferred>
## Deferred Ideas
- WebSocket tick streaming via `KiteTicker` daemon deferred to a future scale/high-frequency milestone if sub-second latency is ever needed.
- Complex multi-broker reconciliation (e.g. Zerodha + Groww) deferred to future multi-broker milestone.

</deferred>

---

*Phase: 03-Data Quality & Market Infrastructure*
