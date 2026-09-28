# Phase 4: Portfolio Analytics That Actually Mean Something - Context

**Gathered:** 2026-09-28  
**Status:** Ready for planning  

<domain>
## Phase Boundary

Phase 4 moves PortfolioIQ past basic "P&L and a pie chart" into quantitative portfolio science and production-grade rebalancing.
This encompasses:
1. **Performance & Attribution Engine**: Time-Weighted Return (TWR) for strategy performance, Money-Weighted Return (XIRR) for personalized investor cash flows, Max Drawdown, Sharpe Ratio, Sortino Ratio, Beta, and Jensen's Alpha relative to a configurable benchmark (default: NIFTY 50 TRI, with NIFTY 500 TRI support).
2. **Historical NAV & Cash Flow Ledger**: A dedicated `portfolio_daily_snapshots` table tracking daily equity value, cash, and unitized NAV (base 100), paired with a `portfolio_cash_flows` ledger with automated margin delta detection and REST endpoints for external deposits/withdrawals.
3. **Execution-Constrained Rebalancer**: Introducing realistic trade sizing and allocation constraints to `src/analytics/rebalancer.py`:
   - Configurable minimum order size (default ₹2,000) to prevent fixed-fee commission drag (with full position liquidation exemption).
   - Configurable cash reserve buffer (default 2.0% of AUM or ₹5,000 floor).
   - Daily turnover cap (default 15% of AUM) prioritized by allocation drift severity.
   - Liquidity and volume protection (order size capped at <= 1.0% of 20-day ADV).
4. **Transaction Cost & Tax Optimization**:
   - Comprehensive Indian regulatory fee schedule modeling (Brokerage ₹0 for delivery, STT 0.1%, NSE turnover fee, SEBI charges, Stamp duty, GST 18%, DP charges ₹15.34 on sell).
   - Tax-loss harvesting logic (highest-cost / loss-first lot selection) combined with the 30-day "tax bomb" protection near the 365-day LTCG threshold.
   - Annual tracking of the ₹1.25L tax-free LTCG exemption pool and Q4 tax-gain harvesting suggestions.
   - Dual gross vs. net post-tax/fee return reporting with explicit basis-point drag attribution.

</domain>

<decisions>
## Implementation Decisions

### Performance Return Methodology & Benchmarks
- **D-01:** Dual Return Metrics: Implement both Time-Weighted Return (TWR) and Extended Internal Rate of Return (XIRR). TWR isolates stock selection and asset allocation performance from external cash flow timing; XIRR provides the investor's personalized rupee-weighted return. — **Reversibility:** costly — affects analytics API contracts and dashboard widgets.
- **D-02:** Configurable Benchmark Index: Default comparative benchmark is NIFTY 50 Total Return Index (TRI), with support for NIFTY 500 TRI for multi-cap portfolios. Benchmarks must use TRI (including dividends) for fair comparison.
- **D-03:** Risk-Free Rate Configuration: Use prevailing Indian 91-day / 364-day Treasury Bill yield (defaulting to 6.50% annualized, configurable via application settings) to calculate Sharpe and Sortino excess returns.
- **D-04:** Daily Snapshot Cadence & 30-Day Warmup: Volatility, Sharpe, Sortino, and Beta require a minimum of 30 trading days of historical NAV data. Portfolios with < 30 days display an "Insufficient history (<30d)" indicator instead of unstable annualized ratios; absolute P&L and TWR display immediately.

### Historical NAV Ledger & Cash Flows
- **D-05:** `portfolio_daily_snapshots` Table: Database migration creating an append-only daily snapshot table (`snapshot_date`, `user_id`, `total_equity_value`, `cash_balance`, `total_nav`, `unit_nav`, `daily_return_pct`, `benchmark_value`, `benchmark_daily_return_pct`, `net_external_flow`). Unitized NAV starts at 100.00 for clean geometric compounding. — **Reversibility:** one-way — database schema migration and foundational time-series ledger.
- **D-06:** `portfolio_cash_flows` Table: Migration creating an explicit ledger for external deposits, withdrawals, dividends, and fees. Morning sync inspects Kite available cash margin deltas; unexplained jumps trigger pending/auto cash flow records. Expose `POST /api/v1/portfolio/cash-flows` for manual management.
- **D-07:** Total Return Accounting: Cash dividends credit the cash balance and feed into total NAV return; stock splits and bonus shares update quantity and adjust tax-lot cost bases in `holding_tax_lots` without generating artificial return jumps.
- **D-08:** Smart Backfill Baseline: Reconstruct historical snapshots from existing `order_audit_trail` records and historical EOD closes where available; for untracked prior history, anchor Day 1 baseline to the first deployment date with initial capital = current portfolio valuation.

### Rebalancer Execution Constraints
- **D-09:** Minimum Trade Value Threshold: Orders with value `< min_trade_value` (default ₹2,000, configurable) are suppressed to avoid fee drag. Full position liquidations (100% sell of a holding or delisted stock) are explicitly exempt. — **Reversibility:** reversible — logic in `src/analytics/rebalancer.py`.
- **D-10:** Cash Reserve Buffer: Buy orders cannot reduce available cash below `max(cash_buffer_pct * AUM, cash_buffer_floor)` (default 2.0% of AUM or ₹5,000 floor).
- **D-11:** Daily Turnover Cap & Drift Prioritization: Limit total proposed order volume (buys + sells) to a maximum percentage of total AUM per run (default 15%). Prioritize orders by absolute allocation drift severity (`abs(target_weight - current_weight)`).
- **D-12:** Liquidity & ADV Guard: Proposed trade quantities are capped at `<= 1.0%` of the stock's 20-day Average Daily Volume (ADV) on NSE to prevent market impact and execution slippage.

### Transaction Cost & Tax-Aware Optimization
- **D-13:** Realistic Indian Fee Model: Integrate exact statutory and broker fee schedules in rebalancing simulations and realized return deductions:
  - Brokerage: ₹0 for delivery
  - STT: 0.1% on buy and 0.1% on sell
  - NSE Exchange Fee: 0.00297%
  - SEBI Turnover Fee: 0.0001%
  - Stamp Duty: 0.015% (buy only)
  - GST: 18% on (Brokerage + Exchange Fee + SEBI charges)
  - DP Charges: ₹15.34 per scrip on sell delivery
- **D-14:** Tax-Optimized Lot Selection: When selling to rebalance, sell lots with unrealized short-term losses first to harvest STCL offsets. For profitable lots, prioritize LTCG lots over STCG, while strictly deferring lots within 30 days of the 365-day LTCG threshold.
- **D-15:** ₹1.25L Annual LTCG Exemption Tracker: Track realized LTCG for the current financial year. In Q4, identify and recommend tax-gain harvesting opportunities to utilize any unused portion of the ₹1.25L tax-free threshold.
- **D-16:** Dual Pre-Tax vs. Post-Tax Analytics: Expose both Gross Return and Net Post-Tax/Fee Return with an explicit basis-point breakdown of fee drag (STT, brokerage/DP, realized taxes).

### the agent's Discretion
- Database index strategies and foreign key constraints on `portfolio_daily_snapshots` and `portfolio_cash_flows`.
- Exact mathematical optimization formulation in `rebalancer.py` (greedy heuristic vs quadratic programming).
- Specific UI formatting of performance charts and drag breakdown cards.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Core Analytics & Rebalancing
- `.planning/ROADMAP.md` § Phase 4 — High-level phase scope and requirements.
- `src/analytics/valuator.py` — Portfolio valuation engine, holding P&L, AUM calculations.
- `src/analytics/rebalancer.py` — Existing rebalance order generator to be upgraded with constraints & cost modeling.
- `src/analytics/tax_guard.py` — Existing STCG/LTCG tax lot classification and holding period logic.
- `src/analytics/drift_detector.py` — Drift detection logic and allocation thresholds.
- `src/db/repository.py` — Data repository layer for orders, holdings, and prices.
- `src/execution/gatekeeper.py` — Validation pipeline for proposed rebalance orders.

### Database Migrations
- `db/migrations/015_holdings_reconciliation_log.sql` — Preceding migration reference.
- `db/migrations/016_market_calendar.sql` — Preceding migration reference.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/analytics/tax_guard.py`: Contains `LTCG_HOLDING_DAYS = 365`, `STCG_TAX_RATE = 0.20`, `LTCG_TAX_RATE = 0.125`, and `LTCG_EXEMPTION = 125000`. Can be extended for tax-optimized lot selection and FY exemption tracking.
- `src/analytics/valuator.py`: `HoldingValuation` and `compute_portfolio_valuation()` provide clean inputs for daily snapshot computation.
- `src/analytics/drift_detector.py`: `detect_drift()` identifies drifted assets and severity to drive rebalancer prioritization.
- `src/config/settings.py`: `Settings` model using Pydantic for adding rebalancer constraint thresholds (`min_trade_value`, `cash_buffer_pct`, `turnover_cap_pct`).

### Established Patterns
- SQLAlchemy Core / parameterized queries with Pydantic DTOs in `src/db/repository.py`.
- Strict Decimal precision for prices, values, and tax calculations (`ROUND_HALF_UP`).
- IST timezone handling using `now_ist()`.

### Integration Points
- `scheduler/jobs.py`: Daily post-market EOD snapshot calculation job at 16:00 IST.
- `src/api/v1/blueprint.py`: New endpoints:
  - `GET /api/v1/analytics/performance` (TWR, XIRR, Sharpe, Sortino, Drawdown, Alpha, Beta)
  - `GET /api/v1/analytics/snapshots` (Historical NAV curve)
  - `GET /api/v1/analytics/tax-harvesting` (Loss harvesting & LTCG exemption opportunities)
  - `GET/POST /api/v1/portfolio/cash-flows` (Cash flow management)
  - `POST /api/v1/rebalance/preview` (Generate rebalance manifest with cost & tax impact)

</code_context>

<specifics>
## Specific Ideas

- Show TWR alongside NIFTY 50 TRI on the same normalized chart (base 100) to clearly show alpha generation over time.
- Provide a clear waterfall chart / badge showing: Gross Return -> Transaction Cost Drag -> Tax Drag -> Net Realized Return.
- Ensure rebalance orders clearly report why an order was scaled back (e.g. "Capped at 1% ADV: 850 shares" or "Skipped: order value ₹1,200 < min ₹2,000").

</specifics>

<deferred>
## Deferred Ideas

- Intraday tick-by-tick Sharpe ratio calculations (deferred to Phase 7: Scale).
- Machine-learning predictive alpha signals (Phase 5: Make the Signal Engine Evidence-Based).
- Multi-account / multi-user tax aggregation (Phase 7: Multi-User & Scale).

</deferred>

---

*Phase: 04-portfolio-analytics*  
*Context gathered: 2026-09-28*
