# Phase 3: Data Quality & Market Infrastructure - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-09-27
**Phase:** 3-Data Quality & Market Infrastructure
**Areas discussed:** Live Market Data Architecture, Price Freshness Policy & Gatekeeper Behavior, Holdings & Corporate Actions Reconciliation, Exchange Trading Calendar & Special Sessions

---

## Live Market Data Architecture

| Option | Description | Selected |
|---|---|---|
| Batch REST Polling via Kite Connect | Poll `kite.ltp()` / `kite.quote()` every 10–30s during market hours in a single batched call; fully deprecate Yahoo Finance for live data. | ✓ |
| Persistent WebSocket Streaming via KiteTicker | Run a dedicated background WebSocket daemon to stream live ticks continuously into PostgreSQL/memory during market hours. | |
| Hybrid Sourcing | Use Kite Connect REST strictly for execution/gatekeeper checks, while keeping Yahoo Finance for dashboard polling to minimize broker API calls. | |

**User's choice:** Batch REST Polling via Kite Connect (`kite.ltp()` / `kite.quote()`) in batched calls every 10–30s during market hours.
**Notes:** Completely eliminates reliance on Yahoo Finance scraping for live pricing and database updates, staying well within Kite Connect rate limits (1 req/sec).

---

## Price Freshness Policy & Gatekeeper Behavior

| Option | Description | Selected |
|---|---|---|
| Synchronous On-Demand Refresh with Fail-Closed Fallback | If price > 60s old, trigger an immediate on-demand `kite.ltp()` fetch; if refresh fails or broker is unreachable, reject the order. | ✓ |
| Strict Instant Fail-Closed | Reject the order immediately if database price is > 60s old; do not perform inline network calls inside the gatekeeper pipeline. | |
| Configurable Grace Period | Allow orders with prices up to 180s old with a logged warning; reject only when exceeding 180s. | |

**User's choice:** Synchronous On-Demand Refresh with Fail-Closed Fallback.
**Notes:** If local database prices are older than 60s during trading hours, Gatekeeper attempts an immediate inline broker quote fetch. If the fetch fails, it fails closed by rejecting the order.

---

## Holdings & Corporate Actions Reconciliation

| Option | Description | Selected |
|---|---|---|
| Authoritative Broker Sync with Reconciliation Audit Trail | Treat Kite as source of truth; upsert `user_holdings`, detect deltas, log discrepancies to a new `holdings_reconciliation_log`, and alert on unexplained quantity jumps (splits/bonuses). | ✓ |
| Strict Safety Lock on Discrepancy | If broker holdings differ from local DB without matching internal trade logs, freeze automated trading until manual admin reconciliation. | |
| Simple Replace | Overwrite local holdings directly on each sync without persisting reconciliation audit logs or alerts. | |

**User's choice:** Authoritative Broker Sync with Reconciliation Audit Trail.
**Notes:** Kite holdings response is authoritative. Discrepancies (T1 settlement, splits, external trades) are logged to `holdings_reconciliation_log`. Unexplained quantity jumps trigger high-severity corporate action alerts.

---

## Exchange Trading Calendar & Special Sessions

| Option | Description | Selected |
|---|---|---|
| DB-Backed Market Calendar with Remote Sync & JSON Fallback | Store holidays/special sessions in a `market_calendar` table, sync via API/curated JSON, with a bundled config fallback and API query/update endpoints. | ✓ |
| Repository JSON Config File | Store holidays and special session hours in a versioned `config/market_calendar.json` in git, loaded into memory at startup without DB tables. | |
| Third-Party Python Package | Integrate a specialized calendar library (such as `pandas_market_calendars` or `exchange_calendars`) to handle NSE/BSE schedules. | |

**User's choice:** DB-Backed Market Calendar with Remote Sync & JSON Fallback.
**Notes:** Eliminates hard-coded Python holiday lists. Allows managing Muhurat special trading sessions via database and REST API, with bundled fallback JSON for offline reliability.

---

## the agent's Discretion

- Polling frequency default: 15 seconds during active market hours.
- Database index choices on `market_calendar` (unique on date) and `holdings_reconciliation_log` (index on instrument_token, timestamp).
- DTO model architecture in `src/models/dtos.py` following Phase 1 patterns.

---

## Deferred Ideas

- WebSocket tick streaming via `KiteTicker` daemon deferred to a future scale/high-frequency milestone if sub-second latency is ever needed.
- Multi-broker portfolio aggregation deferred to future milestone.
