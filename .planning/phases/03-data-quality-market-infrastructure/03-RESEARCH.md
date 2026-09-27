# Phase 3: Data Quality & Market Infrastructure — Technical Research

**Researched:** 2026-09-27  
**Status:** Complete  
**Objective:** Replace fragile Yahoo Finance scraping with direct Zerodha Kite Connect REST quote polling, enforce a 60-second price freshness gatekeeper policy with synchronous on-demand broker refresh, implement post-sync holdings reconciliation with audit logging, and introduce a database-backed exchange trading calendar with special session (Diwali Muhurat) support and offline fallback.

---

## 1. Live Market Data Architecture (`kite.ltp` & `kite.quote`)

### 1.1 Python Syntax & Mechanics
Using the official `kiteconnect.KiteConnect` library, quotes can be polled synchronously in batches using either `kite.ltp()` or `kite.quote()`. Both accept lists of instrument identifiers formatted as `"EXCHANGE:TRADINGSYMBOL"`:

```python
from src.ingestion.kite_auth import get_authenticated_kite

kite = get_authenticated_kite()

# Lightweight last-traded price lookup:
ltp_data = kite.ltp(["NSE:INFY", "NSE:TCS", "NSE:RELIANCE"])

# Comprehensive market quote lookup (includes OHLC, volume, circuit limits):
quote_data = kite.quote(["NSE:INFY", "NSE:TCS", "NSE:RELIANCE"])
```

### 1.2 Response Structures & Data Types

#### `kite.ltp()` Response Structure
```python
{
    "NSE:INFY": {
        "instrument_token": 408065,  # int
        "last_price": 1782.50,  # float
    },
    "NSE:TCS": {"instrument_token": 2953217, "last_price": 4200.00},
}
```

#### `kite.quote()` Response Structure
```python
{
    "NSE:INFY": {
        "instrument_token": 408065,  # int
        "timestamp": datetime.datetime(2026, 9, 27, 15, 29, 59),  # datetime
        "last_price": 1782.50,  # float
        "last_quantity": 10,  # int
        "last_trade_time": datetime.datetime(2026, 9, 27, 15, 29, 58),
        "average_price": 1780.20,  # float (volume weighted average)
        "volume": 2541234,  # int (cumulative day volume)
        "buy_quantity": 41234,  # int
        "sell_quantity": 56123,  # int
        "ohlc": {
            "open": 1770.00,  # float
            "high": 1795.00,  # float
            "low": 1765.00,  # float
            "close": 1768.40,  # float (previous session close)
        },
        "net_change": 14.10,  # float (last_price - prev_close)
        "lower_circuit_limit": 1591.50,  # float
        "upper_circuit_limit": 1945.20,  # float
    }
}
```

### 1.3 Rate Limits & Batch Polling Economics
- **Kite Connect Rate Limit**: **1 request per second** on quote endpoints (`/quote`, `/quote/ltp`).
- **Batch Size Limit**: Up to **500 instruments** per request.
- **PortfolioIQ Polling Overhead**:
  - Portfolio holdings: ~10–50 stocks.
  - Polling interval: 15–30 seconds during active market hours.
  - Consumption rate: 1 request every 15–30 seconds = $0.033\text{ to }0.067\text{ req/sec}$ ($< 7\%$ of Zerodha's limit).
  - Risk of HTTP 429 rate limiting is virtually eliminated.

### 1.4 Ingestion Daemon Design (`src/ingestion/kite_quote_poller.py`)
- Reads active holdings (`quantity + t1_quantity > 0`) from `user_holdings`.
- Batches all symbols into a single `kite.quote()` call.
- Writes to `live_prices` via atomic UPSERT (`source = 'kite'`, `is_stale = FALSE`, `last_updated = NOW()`).
- Appends historical snapshot to `price_history` for analytics.
- On `TokenException` (HTTP 403), calls `invalidate_token()` to trigger re-auth alerts.
- Fully deprecates `src/ingestion/yahoo_poller.py` for live prices.

---

## 2. Price Freshness Policy & Gatekeeper On-Demand Refresh

### 2.1 Staleness Thresholds
- **Market Hours**: A price is stale if:
  1. `is_stale == TRUE` in `live_prices`, OR
  2. No price entry exists for `instrument_token`, OR
  3. $(\text{now\_ist}() - \text{last\_updated}).\text{total\_seconds}() > 60$ seconds.
- **Off-Market Hours**: The previous session's closing price is valid and not marked stale.

### 2.2 Synchronous On-Demand Refresh & Fail-Closed Enforcement
In `src/execution/validators/slippage_check.py`:
- If an order's instrument price is missing or stale during market hours, immediately execute `kite.ltp([f"{order.exchange}:{order.tradingsymbol}"])`.
- If on-demand refresh succeeds:
  - Upsert fresh quote into `live_prices` (`last_price`, `source = 'kite'`, `is_stale = FALSE`, `last_updated = NOW()`).
  - Continue slippage calculation using the fresh price.
- If on-demand refresh fails (broker timeout, network error, invalid price):
  - **Fail closed**: Reject order (`passed = False`).
  - Validation message: `"Price is stale (>60s) and on-demand broker refresh failed for <tradingsymbol>"`.
  - Gatekeeper writes rejection to `order_validation_log`.

---

## 3. Holdings & Corporate Actions Reconciliation

### 3.1 Broker State Representation (`kite.holdings()`)
- `quantity`: Settled delivery quantity in demat account.
- `t1_quantity`: Shares bought in previous trading session awaiting settlement ($T+1$).
- $\text{Total Effective Holdings} = \text{quantity} + \text{t1\_quantity}$.

### 3.2 Schema: `015_holdings_reconciliation_log.sql`
```sql
CREATE TABLE IF NOT EXISTS holdings_reconciliation_log (
    id                      BIGSERIAL PRIMARY KEY,
    user_id                 TEXT NOT NULL DEFAULT 'default',
    instrument_token        INTEGER NOT NULL
                            REFERENCES instrument_master(instrument_token) ON DELETE RESTRICT,
    tradingsymbol           TEXT NOT NULL,
    old_quantity            INTEGER NOT NULL,
    new_quantity            INTEGER NOT NULL,
    old_avg_price           NUMERIC(15, 2),
    new_avg_price           NUMERIC(15, 2),
    delta_quantity          INTEGER NOT NULL,
    reconciliation_reason   TEXT NOT NULL
                            CHECK (reconciliation_reason IN (
                                'T1_SETTLEMENT',
                                'TRADE_FILL',
                                'CORPORATE_ACTION_SPLIT',
                                'CORPORATE_ACTION_BONUS',
                                'EXTERNAL_TRANSFER',
                                'INITIAL_SYNC',
                                'DISCREPANCY'
                            )),
    detected_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_recon_log_user_token
    ON holdings_reconciliation_log (user_id, instrument_token, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_recon_log_reason
    ON holdings_reconciliation_log (reconciliation_reason, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_recon_log_detected
    ON holdings_reconciliation_log (detected_at DESC);
```

### 3.3 Reconciliation Algorithm in `src/ingestion/kite_sync.py`
1. Fetch all local positions for user from `user_holdings`.
2. Fetch current holdings from `kite.holdings()`.
3. For each broker holding:
   - Calculate $\text{new\_total} = \text{quantity} + \text{t1\_quantity}$.
   - If not in local DB: log `"INITIAL_SYNC"`.
   - If in local DB and $\text{delta} == 0$:
     - If $\text{old\_t1} > 0$ and $\text{new\_t1} == 0$: log `"T1_SETTLEMENT"`.
   - If $\text{delta} \neq 0$:
     - Check `order_audit_trail` for filled orders since `last_synced_at`. If fills match $\text{delta}$: log `"TRADE_FILL"`.
     - Check `corporate_actions` for recent splits/bonuses. If match: log `"CORPORATE_ACTION_SPLIT"` or `"CORPORATE_ACTION_BONUS"`.
     - Otherwise: log `"DISCREPANCY"` and emit high-severity alert.
   - Upsert verified holding into `user_holdings`.
4. For any local holding completely missing from broker response:
   - Check if sold via internal trade fill; otherwise log `"DISCREPANCY"`.
   - Zero out position (`quantity = 0`, `t1_quantity = 0`).

---

## 4. Exchange Trading Calendar & Special Sessions

### 4.1 Schema: `016_market_calendar.sql`
```sql
ALTER TABLE market_calendar
    ADD COLUMN IF NOT EXISTS segment TEXT NOT NULL DEFAULT 'equity',
    ADD COLUMN IF NOT EXISTS is_trading_holiday BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS special_session_open TIME,
    ADD COLUMN IF NOT EXISTS special_session_close TIME,
    ADD COLUMN IF NOT EXISTS description TEXT;

-- Seed Diwali Muhurat 2026 Special Session (18:15 to 19:15 IST)
UPDATE market_calendar
SET is_trading_holiday   = FALSE,
    special_session_open  = '18:15:00',
    special_session_close = '19:15:00',
    description           = 'Diwali Laxmi Pujan (Muhurat Trading evening session 18:15-19:15 IST)'
WHERE holiday_date = '2026-11-08' AND exchange = 'NSE';

-- Update descriptions for full holidays
UPDATE market_calendar
SET description = holiday_name || ' (Exchange Closed)'
WHERE description IS NULL AND is_trading_holiday = TRUE;

CREATE INDEX IF NOT EXISTS idx_calendar_segment_date
    ON market_calendar (segment, holiday_date);
```

### 4.2 Static Fallback Calendar (`src/config/nse_holidays.json`)
Bundled with codebase containing all 2026 NSE holidays and Muhurat special trading hours so the system functions identically during offline tests, CI containers, or temporary database outages.

### 4.3 Refactored `src/ingestion/market_hours.py`
- Caches yearly calendar records in-memory (`@lru_cache`).
- Tries DB query first; falls back cleanly to `src/config/nse_holidays.json` on exception.
- Inspects special session trading windows:
  - If date has `special_session_open` and `special_session_close`, market is open within that window even on holidays or weekends.
- Exposes `get_special_session_hours(date)`, `is_market_open(dt)`, `is_holiday(date)`, `seconds_until_market_open()`, and `get_market_status()`.

---

## 5. API Endpoints & Repository Layer

### 5.1 Repository Functions (`src/db/repository.py`)
- `get_market_calendar_entries(year, segment) -> list[MarketCalendarDTO]`
- `upsert_market_calendar_entry(entry: CreateMarketCalendarDTO) -> MarketCalendarDTO`
- `get_holdings_reconciliation_logs(user_id, limit, reason) -> list[HoldingsReconciliationDTO]`

### 5.2 API Blueprint Endpoints (`src/api/v1/blueprint.py`)
- `GET /api/v1/market/calendar` — Query holiday calendar with optional `?year=2026&segment=equity` filter.
- `POST /api/v1/market/calendar` — Authenticated upsert of holiday or special trading session.
- `GET /api/v1/holdings/reconciliation` — Authenticated query of reconciliation audit logs with optional `?limit=50&reason=DISCREPANCY` filter.

---

## 6. Recommended Wave Decomposition

- **Plan 03-01:** Migrations 015 & 016, Static Fallback JSON, DTOs & Repository Methods.
- **Plan 03-02:** DB-Backed Market Hours & Special Sessions Engine + Calendar API Routes.
- **Plan 03-03:** Zerodha Kite REST Batch Quote Poller Daemon (`kite_quote_poller.py`).
- **Plan 03-04:** Gatekeeper 60s Freshness Policy & Fail-Closed On-Demand Refresh (`slippage_check.py`).
- **Plan 03-05:** Broker Holdings Reconciliation, Corporate Actions Jump Detection & Audit API.
