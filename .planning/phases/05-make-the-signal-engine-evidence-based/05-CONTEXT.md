# Phase 5: Make the Signal Engine Evidence-Based - Context

**Gathered:** 2026-09-28  
**Status:** Ready for planning  

<domain>
## Phase Boundary

Phase 5 transforms PortfolioIQ's signal engine from heuristic technical indicators into a rigorous, evidence-based quantitative system.
This encompasses:
1. **Signal Snapshot & Forward Outcome Storage**:
   - Storing daily end-of-day (EOD) snapshots of all generated signals, underlying indicators, and model versions in a dedicated `signal_snapshots` table.
   - Automatically calculating and updating multi-horizon forward returns (5, 20, and 60 trading days) against both the individual asset and the NIFTY 50 TRI benchmark, isolating excess alpha.
2. **Walk-Forward Backtesting Engine**:
   - Implementing rolling walk-forward cross-validation (252 trading days train / 63 trading days test) across historical datasets to prevent data snooping and lookahead bias.
   - Performing independent indicator evaluation using Spearman rank Information Coefficient (IC) and p-values, automatically pruning unproven or negative-alpha indicators ($IC \le 0$ or $p > 0.05$) and dynamically weighting valid indicators.
3. **Evidence Hurdles & Rebalancer Gating**:
   - Benchmarking signal strategies against dual baselines: Stock Buy-and-Hold (timing skill) and Market Buy-and-Hold NIFTY 50 TRI (market alpha).
   - Introducing an evidence hurdle gate classifying signals as `PROVEN_EDGE` vs `UNPROVEN_NOISE`.
   - Restricting the rebalancer (`src/analytics/rebalancer.py`) from executing or proposing trades unless a signal meets the `PROVEN_EDGE` criteria.
4. **Calibrated Monte Carlo & Terminology Migration**:
   - Replacing uncalibrated Gaussian simulations with fat-tailed distributions (Student's $t$ / historical empirical bootstrap) calibrated against realized out-of-sample prediction intervals (80% and 95% confidence bands).
   - Eliminating single point forecasts ("Target Price") in favor of probabilistic dispersion percentiles (P10, P50, P90).
   - Migrating API and UI terminology from "predictions" / "heuristics" to "quantitative signals" (`/api/v1/signals`, `HoldingSignalDTO`).

</domain>

<decisions>
## Implementation Decisions

### Signal History & Forward Outcome Storage
- **D-01:** `signal_snapshots` Table Schema: Store daily EOD holding snapshots in a dedicated table:
  - Columns: `id`, `snapshot_date`, `user_id`, `tradingsymbol`, `model_version`, `composite_score`, `signal_label`, `indicators` (JSONB), `current_price`, `benchmark_price`, `status`, `created_at`.
  - Unique constraint on `(user_id, tradingsymbol, snapshot_date, model_version)` to guarantee deterministic, replayable evaluation.
  - — **Reversibility:** one-way — database migration establishing foundational ground truth dataset.
- **D-02:** Multi-Horizon Forward Return Tracking: Capture realized forward returns at 3 standard institutional trading horizons:
  - 5 trading days (~1 week) for short-term momentum/mean-reversion.
  - 20 trading days (~1 month) for medium-term drift.
  - 60 trading days (~1 quarter) for quarterly rebalance alignment.
  - Each horizon stores: `stock_forward_return`, `benchmark_forward_return` (NIFTY 50 TRI), and `excess_return` (alpha).
  - An automated scheduled job populates forward returns once the horizon days have elapsed.

### Walk-Forward Backtesting & Independent Indicator Evaluation
- **D-03:** Rolling Walk-Forward Backtest Engine: Run walk-forward simulation using 252-day (~1 year) rolling in-sample training/calibration windows and 63-day (~1 quarter) out-of-sample testing windows, stepped forward across historical daily prices.
  - Zero lookahead bias: parameters and indicator weights calibrated strictly on in-sample window $t - 252$ to $t$, evaluated strictly on out-of-sample window $t$ to $t + 63$.
- **D-04:** Independent Indicator Evaluation (Spearman Rank IC):
  - Every technical and statistical indicator (RSI, MACD, Moving Average crossover, Bollinger Bands, Volume trend, etc.) is evaluated independently before any composite score is formed.
  - Metric: Spearman rank correlation between indicator value and subsequent forward return (Information Coefficient, IC), along with two-tailed t-test p-value.
  - Pruning rule: Any indicator with mean out-of-sample $IC \le 0$ or $p > 0.05$ receives zero weight (pruned).
  - Dynamic weighting: Remaining indicators are weighted proportional to their out-of-sample Information Ratio ($IR = \text{mean}(IC) / \text{std}(IC)$).

### Evidence Hurdles & Rebalancer Gating
- **D-05:** Dual Baseline Benchmark Comparison:
  - Baseline 1 (Timing Skill): Buy-and-Hold the same stock over the evaluation period. Tests whether the signal produces better risk-adjusted return than simply holding the equity.
  - Baseline 2 (Market Alpha): Buy-and-Hold NIFTY 50 TRI over the evaluation period. Tests whether the strategy beats the market index.
- **D-06:** Signal Status & Rebalancer Gating (`PROVEN_EDGE` vs `UNPROVEN_NOISE`):
  - Categorize signals based on empirical hurdle:
    - `PROVEN_EDGE`: Statistically significant positive out-of-sample IC ($p < 0.05$), positive annualized alpha over both baselines, and positive win-rate/profit-factor.
    - `UNPROVEN_NOISE`: Signals failing any of the hurdle criteria.
  - **Rebalancer Gate**: The rebalancer (`src/analytics/rebalancer.py`) MUST NOT generate order proposals based on signals tagged as `UNPROVEN_NOISE`. Orders are only synthesized for assets with `PROVEN_EDGE` or rule-based portfolio rebalance drift triggers.

### Monte Carlo Calibration & Terminology Migration
- **D-07:** Fat-Tailed Monte Carlo Simulation & Percentile Dispersion:
  - Deprecate normal/Gaussian random walk assumptions. Model price distributions using Student's $t$ distribution (fitted degrees of freedom) or historical bootstrap with volatility clustering.
  - Calibrate prediction cones against historical empirical coverage: 80% cones must contain realized prices ~80% of the time, and 95% cones must contain realized prices ~95% of the time.
  - Ban point estimates: Remove single "Target Price" outputs. Expose probabilistic dispersion percentiles: P10 (pessimistic / 10th percentile), P50 (median / 50th percentile), P90 (optimistic / 90th percentile).
- **D-08:** Systematic UI and API Terminology Migration:
  - Deprecate `/api/v1/analysis` in favor of `/api/v1/signals` (maintain `/api/v1/analysis` as backward-compatible alias).
  - Rename `HoldingPrediction` DTO to `HoldingSignalDTO`.
  - Rebrand frontend and report copy from "AI Prediction" or "Price Target" to "Quantitative Signal Engine", accompanied by explicit out-of-sample calibration disclosures and backtest performance summaries.

### the agent's Discretion
- Mathematical libraries: Use `numpy`, `scipy.stats` for Spearman IC, t-distribution fitting, and Student-t random variate generation.
- Historical data caching: Implement local caching / parquet or database storage for historical price data to prevent hitting external rate limits during walk-forward backtests.
- Database index strategies on `signal_snapshots` and backtest metric run logs.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Signal Generation & Existing Heuristics
- `src/analytics/predictor.py` — Current heuristic signal engine (RSI, MACD, moving averages, Gaussian Monte Carlo) to be refactored into the calibrated quantitative signal engine.
- `src/analytics/rebalancer.py` — Rebalance order generator that must integrate the `PROVEN_EDGE` signal gate.
- `src/analytics/performance.py` — Benchmark and return attribution calculations established in Phase 4.
- `src/models/dtos.py` — Data contracts (`HoldingSignalDTO`, `SignalSnapshotDTO`, `BacktestResultDTO`).
- `src/db/repository.py` — Persistence methods for snapshots, backtest runs, and forward return updating.

### Database Migrations
- `db/migrations/017_portfolio_snapshots.sql` — Preceding migration reference.
- `db/migrations/018_portfolio_cash_flows.sql` — Preceding migration reference.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/analytics/performance.py`: Contains standardized return formulas, benchmark compounding, and Sharpe/Sortino calculations that can evaluate backtest trading strategies.
- `src/analytics/cost_calculator.py`: Indian transaction cost model (STT, DP charges, GST) can be plugged directly into the backtest engine to compute net realized alpha rather than gross paper gains.
- `src/analytics/snapshot_recorder.py`: Established cron-compatible recorder pattern that can be extended for EOD signal snapshots.

### Established Patterns
- SQLAlchemy Core / parameterized queries with Pydantic DTOs in `src/db/repository.py`.
- Strict Decimal precision for monetary amounts; standard floating point for statistical scores/probabilities (`mode="json"` serialization aware).
- Fail-closed safety architecture: If signal verification or backtest evidence is absent, default to `UNPROVEN_NOISE` and block order creation.

### Integration Points
- `src/api/v1/blueprint.py`: New `/api/v1/signals` routes, `/api/v1/signals/backtest` execution and report endpoints, aliasing legacy `/api/v1/analysis`.
- `scheduler/jobs.py`: Daily post-market EOD signal snapshot recorder job and forward return maturation calculator.

</code_context>

<specifics>
## Specific Ideas

- Provide a visual calibration plot (Reliability Diagram) or empirical coverage badge (e.g. "80% Cone Empirical Coverage: 81.2% across 500 test windows").
- For each indicator in the composite, display its individual Spearman IC, p-value, and contribution weight in the signal breakdown card.
- In rebalance previews, flag any trade suggestion originating from signals with a badge: `PROVEN EDGE (IC: +0.082, p=0.012)` or suppress if `UNPROVEN NOISE`.

</specifics>

<deferred>
## Deferred Ideas

- Deep learning / LSTM sequence models (requires high-frequency tick data and GPU pipelines; out of scope for personal swing portfolio).
- Alternative data (social sentiment, news NLP) — keep strictly to price/volume and fundamental data until core quantitative signals are validated.
- Cross-asset multi-factor portfolio optimization (Barra-style risk model deferred to Phase 7: Scale).

</deferred>

---

*Phase: 05-make-the-signal-engine-evidence-based*  
*Context gathered: 2026-09-28*
