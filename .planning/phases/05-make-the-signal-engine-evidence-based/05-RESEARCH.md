# Phase 5 Research: Make the Signal Engine Evidence-Based

**Target Phase:** Phase 5: Make the Signal Engine Evidence-Based  
**Context Reference:** `.planning/phases/05-make-the-signal-engine-evidence-based/05-CONTEXT.md`  
**Discussion Reference:** `.planning/phases/05-make-the-signal-engine-evidence-based/05-DISCUSSION-LOG.md`  
**Target Architecture:** Python 3.12, Flask, PostgreSQL / Supabase, SQLAlchemy Core, Pydantic v2, Scipy 1.17, Numpy 2.3, Pandas 3.0  

---

## 1. Executive Summary & Quantitative Architecture

Phase 5 transitions PortfolioIQ's signal generation from heuristic rules into an empirical, statistical quantitative engine. Rather than assuming technical indicators work or using arbitrary static weights, every indicator is evaluated independently via out-of-sample Spearman rank Information Coefficients (IC). Lookahead bias is eliminated via rolling walk-forward cross-validation (252-day train / 63-day test). Monte Carlo simulations are upgraded from normal distributions to fat-tailed Student's $t$ distributions calibrated against realized historical prediction intervals. Finally, signals are gated by empirical performance hurdles (`PROVEN_EDGE` vs `UNPROVEN_NOISE`), preventing the rebalancer from executing trades on unproven noise.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       Quantitative Signal Architecture                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  [Historical Price & Benchmark Cache]                                       │
│       │ (Incremental EOD Bars: Equities + NIFTY 50 TRI)                    │
│       ▼                                                                     │
│  [Rolling Walk-Forward Engine]                                              │
│       ├── In-Sample Calibration (252 days)                                  │
│       └── Out-of-Sample Evaluation (63 days) ──► Zero Lookahead Bias        │
│             │                                                               │
│             ├─► Independent Indicator Evaluation                            │
│             │     ├── Spearman Rank IC & p-value                            │
│             │     ├── Pruning Gate: Mean IC <= 0 or p > 0.05 ──► Weight = 0 │
│             │     └── Dynamic Weighting: IR = mean(IC) / std(IC)            │
│             │                                                               │
│             ├─► Dual Baseline Comparison                                    │
│             │     ├── Baseline 1: Stock Buy & Hold (Timing Skill)           │
│             │     └── Baseline 2: NIFTY 50 TRI B&H (Market Alpha)           │
│             │                                                               │
│             └─► Calibrated Fat-Tailed Monte Carlo                           │
│                   ├── Student's t MLE Parameter Fit (df, loc, scale)        │
│                   ├── Empirical Coverage Calibration (80% / 95% Cones)      │
│                   └── Dispersion Percentiles: P10, P25, P50, P75, P90       │
│                                                                             │
│  [Evidence Hurdle Classifier]                                               │
│       ├── PROVEN_EDGE: Out-of-sample alpha, IC > 0, p < 0.05, WinRate >= 50% │
│       └── UNPROVEN_NOISE: Fails any hurdle ──► Rebalancer SUPPRESSED        │
│                                                                             │
│  [Persistence & API Layer]                                                  │
│       ├── signal_snapshots: Daily EOD state + 5d, 20d, 60d Forward Returns  │
│       ├── backtest_runs & indicator_evaluations: Walk-forward audit trail   │
│       └── REST API: /api/v1/signals/* (aliasing legacy /api/v1/analysis)    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Database Schema & Migrations (`019_signal_snapshots.sql`, `020_backtest_runs.sql`)

### 2.1 Migration `019_signal_snapshots.sql`
Tracks daily End-of-Day (EOD) signal states, underlying indicators, model versions, and multi-horizon forward returns (5, 20, 60 trading days) for individual stocks, the NIFTY 50 TRI benchmark, and excess alpha.

```sql
-- ============================================================
-- Migration 019: Signal Snapshots & Multi-Horizon Forward Outcomes
-- Append-only time-series tracking daily quantitative signal states,
-- underlying indicator metrics, and realized forward returns (5d, 20d, 60d).
-- ============================================================

CREATE TABLE IF NOT EXISTS signal_snapshots (
    id                          BIGSERIAL PRIMARY KEY,
    snapshot_date               DATE NOT NULL,
    user_id                     TEXT NOT NULL DEFAULT 'default',
    tradingsymbol               TEXT NOT NULL,
    model_version               TEXT NOT NULL DEFAULT 'v1.0.0',

    -- Signal valuation & state
    current_price               NUMERIC(15, 2) NOT NULL,
    benchmark_price             NUMERIC(15, 2), -- NIFTY 50 TRI closing price
    composite_score             NUMERIC(5, 2) NOT NULL, -- 0.00 to 100.00
    signal_label                TEXT NOT NULL, -- 'STRONG_BUY', 'BUY', 'HOLD', 'SELL', 'STRONG_SELL'
    status                      TEXT NOT NULL DEFAULT 'PENDING', -- 'PROVEN_EDGE', 'UNPROVEN_NOISE', 'PENDING'

    -- Indicators payload (JSONB)
    -- Stores breakdown: {"rsi": {"value": 28.4, "score": 80.0, "ic": 0.075, "p_value": 0.018, "weight": 0.35, "pruned": false}, ...}
    indicators                  JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Calibrated Monte Carlo dispersion percentiles (JSONB)
    -- Stores: {"p10": 102.5, "p25": 108.0, "p50": 114.2, "p75": 121.0, "p90": 128.5, "df": 4.2, "coverage_80": 81.5}
    monte_carlo                 JSONB DEFAULT '{}'::jsonb,

    -- Multi-Horizon Forward Return Tracking (Realized Ex-Post)
    -- 5 Trading Days (~1 week)
    return_5d_stock             NUMERIC(8, 4),
    return_5d_benchmark         NUMERIC(8, 4),
    excess_return_5d            NUMERIC(8, 4),
    realized_5d_at              DATE,

    -- 20 Trading Days (~1 month)
    return_20d_stock            NUMERIC(8, 4),
    return_20d_benchmark        NUMERIC(8, 4),
    excess_return_20d           NUMERIC(8, 4),
    realized_20d_at             DATE,

    -- 60 Trading Days (~1 quarter)
    return_60d_stock            NUMERIC(8, 4),
    return_60d_benchmark        NUMERIC(8, 4),
    excess_return_60d           NUMERIC(8, 4),
    realized_60d_at             DATE,

    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Constraints
    CONSTRAINT uq_signal_snapshots UNIQUE (user_id, tradingsymbol, snapshot_date, model_version),
    CONSTRAINT chk_signal_status CHECK (status IN ('PROVEN_EDGE', 'UNPROVEN_NOISE', 'PENDING')),
    CONSTRAINT chk_composite_score CHECK (composite_score >= 0.00 AND composite_score <= 100.00),
    CONSTRAINT chk_positive_price CHECK (current_price > 0)
);

-- Indexes for deterministic queries and time-series replay
CREATE INDEX IF NOT EXISTS idx_signal_snapshots_lookup
    ON signal_snapshots (user_id, tradingsymbol, snapshot_date DESC);

CREATE INDEX IF NOT EXISTS idx_signal_snapshots_date
    ON signal_snapshots (snapshot_date DESC);

CREATE INDEX IF NOT EXISTS idx_signal_snapshots_status
    ON signal_snapshots (status);

CREATE INDEX IF NOT EXISTS idx_signal_snapshots_indicators_gin
    ON signal_snapshots USING GIN (indicators);
```

### 2.2 Migration `020_backtest_runs.sql`
Stores rolling walk-forward backtest executions, out-of-sample strategy performance net of Indian transaction costs, dual baseline metrics, and individual indicator statistical validity evaluations.

```sql
-- ============================================================
-- Migration 020: Walk-Forward Backtest Runs & Indicator Evaluations
-- Stores rolling walk-forward simulation runs, out-of-sample performance,
-- dual-baseline comparisons, and independent indicator validation metrics.
-- ============================================================

CREATE TABLE IF NOT EXISTS backtest_runs (
    id                          BIGSERIAL PRIMARY KEY,
    run_id                      UUID NOT NULL DEFAULT gen_random_uuid(),
    tradingsymbol               TEXT NOT NULL,
    model_version               TEXT NOT NULL DEFAULT 'v1.0.0',

    -- Partition windows
    train_start_date            DATE NOT NULL,
    train_end_date              DATE NOT NULL,
    test_start_date             DATE NOT NULL,
    test_end_date               DATE NOT NULL,
    train_window_days           INT NOT NULL DEFAULT 252,
    test_window_days            INT NOT NULL DEFAULT 63,
    total_folds                 INT NOT NULL DEFAULT 1,

    -- Out-of-Sample Strategy Performance (Net of Indian Transaction Costs)
    strategy_cagr               NUMERIC(8, 4),
    strategy_sharpe             NUMERIC(8, 4),
    strategy_sortino            NUMERIC(8, 4),
    strategy_max_drawdown       NUMERIC(8, 4),
    strategy_win_rate           NUMERIC(8, 4),
    strategy_profit_factor      NUMERIC(8, 4),
    total_trades                INT NOT NULL DEFAULT 0,

    -- Baseline 1: Stock Buy-and-Hold (Timing Skill)
    stock_cagr                  NUMERIC(8, 4),
    stock_sharpe                NUMERIC(8, 4),
    stock_max_drawdown          NUMERIC(8, 4),

    -- Baseline 2: Market NIFTY 50 TRI Buy-and-Hold (Market Alpha)
    benchmark_cagr              NUMERIC(8, 4),
    benchmark_sharpe            NUMERIC(8, 4),
    benchmark_max_drawdown      NUMERIC(8, 4),

    -- Excess Attribution
    excess_cagr_vs_stock        NUMERIC(8, 4),
    excess_cagr_vs_benchmark    NUMERIC(8, 4),
    total_cost_drag_bps         NUMERIC(8, 2) DEFAULT 0.00,

    -- Hurdle Classification
    status                      TEXT NOT NULL DEFAULT 'UNPROVEN_NOISE',
    passed_hurdle               BOOLEAN NOT NULL DEFAULT FALSE,
    hurdle_details              JSONB DEFAULT '{}'::jsonb,

    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Constraints
    CONSTRAINT uq_backtest_runs UNIQUE (tradingsymbol, model_version, train_start_date, test_end_date),
    CONSTRAINT chk_backtest_status CHECK (status IN ('PROVEN_EDGE', 'UNPROVEN_NOISE'))
);

CREATE INDEX IF NOT EXISTS idx_backtest_runs_symbol_date
    ON backtest_runs (tradingsymbol, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_backtest_runs_status
    ON backtest_runs (status);


CREATE TABLE IF NOT EXISTS indicator_evaluations (
    id                          BIGSERIAL PRIMARY KEY,
    backtest_run_id             BIGINT NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
    indicator_name              TEXT NOT NULL,

    -- Statistical Information Coefficient
    in_sample_ic                NUMERIC(8, 4),
    in_sample_p_value           NUMERIC(8, 4),
    out_sample_ic               NUMERIC(8, 4),
    out_sample_p_value          NUMERIC(8, 4),
    mean_ic                     NUMERIC(8, 4),
    std_ic                      NUMERIC(8, 4),
    information_ratio           NUMERIC(8, 4),

    -- Dynamic Weight & Pruning
    weight                      NUMERIC(8, 4) NOT NULL DEFAULT 0.0000,
    is_pruned                   BOOLEAN NOT NULL DEFAULT TRUE,
    prune_reason                TEXT,

    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_indicator_eval UNIQUE (backtest_run_id, indicator_name)
);

CREATE INDEX IF NOT EXISTS idx_indicator_evaluations_run
    ON indicator_evaluations (backtest_run_id);
```

---

## 3. Walk-Forward Backtesting & Independent Indicator Evaluation

### 3.1 Mathematical Formulation: Spearman Rank Information Coefficient (IC)
Let $\{S_{i, t}\}_{t=1}^W$ be the normalized directional factor score generated by indicator $i$ at daily close $t$ ($S_{i, t} \in [-1.0, +1.0]$, where $+1$ is maximum bullish and $-1$ is maximum bearish).  
Let $\{R_{t \to t+h}\}_{t=1}^W$ be the subsequent forward return over horizon $h$ (default: $h=20$ trading days):
$$R_{t \to t+h} = \frac{P_{t+h} - P_t}{P_t}$$

The Information Coefficient (IC) is the Spearman rank correlation:
$$\text{IC}_{i} = \rho_{\text{spearman}}\left(\text{rank}(S_{i, t}), \text{rank}(R_{t \to t+h})\right) = 1 - \frac{6 \sum_{t=1}^W d_t^2}{W(W^2 - 1)}$$
with two-tailed student-t p-value $p_i$.

### 3.2 Independent Pruning & Dynamic IR Weighting
For indicator $i$ evaluated across $K$ out-of-sample walk-forward test folds:
$$\overline{\text{IC}}_i = \frac{1}{K}\sum_{k=1}^K \text{IC}_{i, k}, \quad \sigma(\text{IC}_i) = \sqrt{\frac{1}{K-1} \sum_{k=1}^K (\text{IC}_{i, k} - \overline{\text{IC}}_i)^2}$$
$$\text{Information Ratio (IR)}_i = \frac{\overline{\text{IC}}_i}{\sigma(\text{IC}_i)}$$

**Pruning Rule:**  
An indicator is pruned (forced weight = 0) if:
$$\overline{\text{IC}}_i \le 0 \quad \text{OR} \quad p_i > 0.05$$

**Dynamic Weighting:**  
For the subset of valid (unpruned) indicators $\mathcal{V}$:
$$w_i = \begin{cases} \frac{\text{IR}_i}{\sum_{j \in \mathcal{V}} \text{IR}_j} & \text{if } i \in \mathcal{V} \text{ and } \sum_{j \in \mathcal{V}} \text{IR}_j > 0 \\ 0 & \text{otherwise} \end{cases}$$
If $\mathcal{V} = \emptyset$, all weights $w_i = 0$, composite score defaults to neutral (50.0), and signal status is classified as `UNPROVEN_NOISE`.

### 3.3 Rolling Walk-Forward Cross-Validation Algorithm & Transaction Cost Simulation
```python
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import numpy as np
import pandas as pd
from scipy import stats

from src.analytics.cost_calculator import compute_indian_delivery_charges

@dataclass
class IndicatorFoldResult:
    name: str
    ic: float
    p_value: float

@dataclass
class IndicatorSummary:
    name: str
    mean_ic: float
    std_ic: float
    ir: float
    p_value: float
    weight: float
    is_pruned: bool
    prune_reason: str | None = None

def compute_indicator_ic(
    scores: pd.Series,
    forward_returns: pd.Series,
) -> tuple[float, float]:
    """Compute Spearman Rank IC and p-value between indicator score and forward return."""
    valid = pd.concat([scores, forward_returns], axis=1).dropna()
    if len(valid) < 15:
        return 0.0, 1.0
    res = stats.spearmanr(valid.iloc[:, 0], valid.iloc[:, 1])
    ic = float(res.statistic) if not np.isnan(res.statistic) else 0.0
    p_val = float(res.pvalue) if not np.isnan(res.pvalue) else 1.0
    return ic, p_val

def run_walk_forward_backtest(
    df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    train_window: int = 252,
    test_window: int = 63,
    forward_horizon: int = 20,
) -> dict:
    """
    Rolling 252-day train / 63-day test walk-forward simulation.
    Guarantees strict zero-lookahead bias.
    """
    total_bars = len(df)
    min_required = train_window + test_window + forward_horizon
    if total_bars < min_required:
        raise ValueError(f"Insufficient bars: {total_bars} < {min_required}")

    # Compute standardized indicator scores [-1.0, +1.0]
    close = df["Close"]
    fwd_ret = close.pct_change(forward_horizon).shift(-forward_horizon)

    indicators_scores = {
        "RSI": (50.0 - df["RSI"]) / 50.0,
        "MACD": np.tanh(df["MACD_HIST"] / df["MACD_HIST"].rolling(60).std().replace(0, 1.0)),
        "BOLLINGER": 1.0 - 2.0 * df["PERCENT_B"],
        "LR_MOMENTUM": np.tanh(df["LR_SLOPE_TSTAT"] / 2.0),
    }

    fold_results: dict[str, list[float]] = {k: [] for k in indicators_scores}
    fold_pvals: dict[str, list[float]] = {k: [] for k in indicators_scores}

    test_slices = []
    step = test_window

    for start_idx in range(0, total_bars - train_window - test_window + 1, step):
        train_end = start_idx + train_window
        test_end = train_end + test_window

        # Out-of-sample test window
        for name, score_s in indicators_scores.items():
            test_score = score_s.iloc[train_end:test_end]
            test_fwd = fwd_ret.iloc[train_end:test_end]
            ic, p = compute_indicator_ic(test_score, test_fwd)
            fold_results[name].append(ic)
            fold_pvals[name].append(p)

        test_slices.append((train_end, test_end))

    # Evaluate mean IC, IR, and dynamic weights
    evaluations: list[IndicatorSummary] = []
    valid_irs: dict[str, float] = {}

    for name in indicators_scores:
        ics = fold_results[name]
        mean_ic = float(np.mean(ics)) if ics else 0.0
        std_ic = float(np.std(ics, ddof=1)) if len(ics) > 1 else 1e-4
        mean_p = float(np.mean(fold_pvals[name])) if fold_pvals[name] else 1.0
        ir = mean_ic / std_ic if std_ic > 1e-6 else 0.0

        is_pruned = False
        reason = None
        if mean_ic <= 0:
            is_pruned = True
            reason = f"Non-positive out-of-sample IC ({mean_ic:.4f})"
        elif mean_p > 0.05:
            is_pruned = True
            reason = f"Statistical insignificance (p={mean_p:.4f} > 0.05)"

        if not is_pruned and ir > 0:
            valid_irs[name] = ir

        evaluations.append(
            IndicatorSummary(
                name=name,
                mean_ic=round(mean_ic, 4),
                std_ic=round(std_ic, 4),
                ir=round(ir, 4),
                p_value=round(mean_p, 4),
                weight=0.0,
                is_pruned=is_pruned,
                prune_reason=reason,
            )
        )

    # Dynamic Weighting
    sum_ir = sum(valid_irs.values())
    for ev in evaluations:
        if not ev.is_pruned and sum_ir > 0:
            ev.weight = round(valid_irs[ev.name] / sum_ir, 4)

    return {
        "evaluations": evaluations,
        "test_slices": test_slices,
    }
```

---

## 4. Calibrated Fat-Tailed Monte Carlo Simulation

### 4.1 Student's $t$ Distribution Parameter Fitting
Standard normal distributions severely underestimate tail risks in Indian equity delivery. We model daily log returns $r_t = \ln(P_t / P_{t-1})$ using a 3-parameter Student's $t$ distribution:
$$r_t \sim t_{\nu}(\mu, s)$$
Parameters fitted via maximum likelihood:
`df_fit, loc_fit, scale_fit = scipy.stats.t.fit(r_t)`
- $\nu$ (`df`): Degrees of freedom (typically $\nu \in [3.0, 6.0]$ for Indian scrips).
- $\mu$ (`loc`): Daily location / drift.
- $s$ (`scale`): Scale parameter.

### 4.2 Empirical Coverage Calibration (Conformal Scaling)
To verify prediction intervals, we test whether historical realized forward prices fall inside the model's confidence intervals.
For nominal coverage $1 - \alpha = 0.80$ (central 80% interval $[P_{10}, P_{90}]$):
1. Compute theoretical critical value: $q_{\text{theo}} = t_{\nu}^{-1}(0.90)$.
2. Compute empirical absolute standardized residuals:
$$e_t = \frac{|r_t - \mu|}{s}$$
3. Extract empirical 80th percentile $q_{\text{emp}} = \text{Percentile}_{80}(e_t)$.
4. Scale adjustment factor:
$$c = \max\left(1.0, \frac{q_{\text{emp}}}{q_{\text{theo}}}\right)$$
5. Adjusted scale: $s_{\text{calibrated}} = s \cdot c$.

```python
def compute_calibrated_monte_carlo(
    df: pd.DataFrame,
    horizon_days: int = 30,
    simulations: int = 1000,
    seed: int = 42,
) -> dict:
    """
    Fat-tailed Student-t Monte Carlo simulation with empirical coverage calibration.
    Bans single point 'Target Price' in favor of probabilistic percentiles (P10..P90).
    """
    np.random.seed(seed)
    close = df["Close"].dropna()
    log_returns = np.log(close / close.shift(1)).dropna().values

    # Fit Student's t distribution
    df_fit, loc_fit, scale_fit = stats.t.fit(log_returns)
    df_fit = max(2.5, min(float(df_fit), 30.0))  # bounded for numerical stability

    # Empirical coverage calibration (80% target)
    theo_q80 = float(stats.t.ppf(0.90, df=df_fit))
    std_residuals = np.abs((log_returns - loc_fit) / scale_fit)
    emp_q80 = float(np.percentile(std_residuals, 80))
    scale_multiplier = max(1.0, emp_q80 / theo_q80 if theo_q80 > 0 else 1.0)
    calibrated_scale = scale_fit * scale_multiplier

    current_price = float(close.iloc[-1])
    shocks = stats.t.rvs(
        df=df_fit,
        loc=loc_fit,
        scale=calibrated_scale,
        size=(simulations, horizon_days),
    )
    paths = current_price * np.exp(np.cumsum(shocks, axis=1))

    final_prices = paths[:, -1]
    p10 = float(np.percentile(final_prices, 10))
    p25 = float(np.percentile(final_prices, 25))
    p50 = float(np.percentile(final_prices, 50))
    p75 = float(np.percentile(final_prices, 75))
    p90 = float(np.percentile(final_prices, 90))

    # Fan chart percentile series
    fan_dates = [(pd.Timestamp.now() + pd.Timedelta(days=i + 1)).strftime("%Y-%m-%d") for i in range(horizon_days)]
    fan_p10 = [round(float(np.percentile(paths[:, i], 10)), 2) for i in range(horizon_days)]
    fan_p50 = [round(float(np.percentile(paths[:, i], 50)), 2) for i in range(horizon_days)]
    fan_p90 = [round(float(np.percentile(paths[:, i], 90)), 2) for i in range(horizon_days)]

    return {
        "current_price": round(current_price, 2),
        "horizon_days": horizon_days,
        "degrees_of_freedom": round(df_fit, 2),
        "scale_multiplier": round(scale_multiplier, 3),
        "empirical_coverage_80": round((emp_q80 / theo_q80) * 80.0, 1),
        "p10": round(p10, 2),
        "p25": round(p25, 2),
        "p50": round(p50, 2),
        "p75": round(p75, 2),
        "p90": round(p90, 2),
        "prob_profit": round(float(np.mean(final_prices > current_price) * 100), 1),
        "fan_dates": fan_dates,
        "fan_p10": fan_p10,
        "fan_p50": fan_p50,
        "fan_p90": fan_p90,
    }
```

---

## 5. Historical Price Ingestion & Caching Strategy

To prevent external network latency and `yfinance` HTTP 429 rate limit errors during intensive walk-forward backtests, implement `src/analytics/historical_cache.py`:

1. **Table Schema `historical_daily_bars`**:
   - `(tradingsymbol, bar_date, open, high, low, close, volume)` with primary key `(tradingsymbol, bar_date)`.
2. **Incremental Cache Synchronization**:
   - Query DB for `max(bar_date)` for requested scrip.
   - If `max(bar_date) == last_completed_trading_day`, return cached DataFrame immediately (0 ms network cost).
   - If stale or missing, query `yfinance` starting from `max(bar_date) + 1 day` through today, batch insert newly retrieved bars, and return the combined cached series.
3. **Benchmark Asset Support**:
   - Cache NIFTY 50 Total Return Index (`^NSEI` or `NIFTYBEES.NS`) under standard symbol `NIFTY 50 TRI`.

---

## 6. Evidence Hurdle Gate & Rebalancer Integration

### 6.1 Hurdle Criteria (`PROVEN_EDGE` vs `UNPROVEN_NOISE`)
A quantitative signal model for a scrip is assigned one of three operational statuses:
1. `PROVEN_EDGE`: Must strictly pass ALL 4 quantitative hurdles in out-of-sample walk-forward testing:
   - **Hurdle 1 (Predictive Power):** Mean out-of-sample Information Coefficient $\overline{\text{IC}} > 0$ and $p < 0.05$.
   - **Hurdle 2 (Stock Timing Skill):** Strategy net annualized return exceeds Stock Buy-and-Hold: $\text{CAGR}_{\text{strategy, net}} > \text{CAGR}_{\text{stock}}$.
   - **Hurdle 3 (Market Excess Alpha):** Strategy net annualized return exceeds NIFTY 50 TRI: $\text{CAGR}_{\text{strategy, net}} > \text{CAGR}_{\text{benchmark}}$.
   - **Hurdle 4 (Trade Quality):** Win rate $\ge 50\%$, Profit Factor $> 1.0$, and out-of-sample trade count $\ge 5$.
2. `UNPROVEN_NOISE`: Fails any hurdle.
3. `PENDING`: Insufficient historical sample bars (< 378 trading days) or backtest not yet executed.

### 6.2 Fail-Closed Rebalancer Enforcement
In `src/analytics/rebalancer.py`:
- **Strategic Rebalance Orders (`SECTOR_DRIFT`, `CONCENTRATION_BREACH`, `HOLDING_DRIFT`):**
  Portfolio drift reduction orders are preserved. The order is annotated with `evidence_status` and `evidence_badge` for user transparency.
- **Tactical Signal Orders / Tilts:**
  Any trade generated from tactical signal scoring is strictly blocked if `status != "PROVEN_EDGE"`.
- In `RebalancePlan`, track:
  `orders_suppressed_unproven_noise: int = 0`
  Orders suppressed log an explicit audit warning:
  `"[GATE] Suppressed order for {symbol}: Unproven signal (Failed Hurdle)."`

---

## 7. API Contracts & Terminology Migration

### 7.1 Routes & Aliasing
- Primary Endpoint: `GET /api/v1/signals/<symbol>`
- Backward-Compatible Alias: `GET /api/v1/analysis/<symbol>` (adds HTTP header `Warning: 299 - "Deprecated endpoint. Use /api/v1/signals/<symbol> instead."`)
- Bulk Portfolio Signals: `GET /api/v1/signals`
- Walk-Forward Trigger: `POST /api/v1/signals/backtest`
- Signal History & Outcomes: `GET /api/v1/signals/history`

### 7.2 Pydantic DTOs (`src/models/dtos.py`)
- `IndicatorEvaluationDTO`: Individual indicator statistical performance (mean IC, std IC, IR, p-value, weight, prune reason).
- `CalibratedMonteCarloDTO`: Student-t degrees of freedom, empirical coverage, percentiles (P10, P25, P50, P75, P90).
- `BacktestRunDTO`: Full out-of-sample walk-forward summary, dual baselines, cost drag bps, hurdle classification.
- `SignalSnapshotDTO`: Time-series snapshot record with multi-horizon realized returns.
- `HoldingSignalDTO`: Unified holding response replacing `HoldingPrediction`.

---

## 8. Wave Decomposition Plan (Plan 05-01 through Plan 05-06)

- **Plan 05-01 (Wave 1): Database Migrations & Data Layer**
  - Implement `019_signal_snapshots.sql` and `020_backtest_runs.sql`.
  - Add `historical_daily_bars` schema and local caching layer (`src/analytics/historical_cache.py`).
  - Add Pydantic DTOs and typed repository persistence methods.

- **Plan 05-02 (Wave 2): Quantitative Indicator Evaluator & Information Coefficient Engine**
  - Refactor technical indicators in `src/analytics/predictor.py` into normalized score generators.
  - Implement Spearman rank IC calculation with `scipy.stats.spearmanr`.
  - Implement independent pruning ($IC \le 0$ or $p > 0.05$) and Information Ratio dynamic weighting.

- **Plan 05-03 (Wave 2): Rolling Walk-Forward Backtester & Dual Baselines**
  - Implement rolling 252-day train / 63-day test walk-forward cross-validation.
  - Compute dual baseline performance: Stock Buy-and-Hold (timing skill) and NIFTY 50 TRI (market alpha).
  - Simulate strategy net returns factoring Indian delivery transaction costs (`cost_calculator.py`).

- **Plan 05-04 (Wave 3): Calibrated Fat-Tailed Monte Carlo Simulation**
  - Implement Student's $t$ MLE parameter fitting (`scipy.stats.t.fit`).
  - Implement empirical coverage calibration against historical prediction intervals (80% and 95% bands).
  - Replace point "Target Price" forecasts with P10, P25, P50, P75, P90 percentile dispersion.

- **Plan 05-05 (Wave 3): Rebalancer Evidence Hurdle Gate & Order Suppression**
  - Implement `PROVEN_EDGE` vs `UNPROVEN_NOISE` hurdle evaluation.
  - Integrate fail-closed order suppression in `src/analytics/rebalancer.py` for unproven signals.
  - Expose evidence status, badges, and suppression counters in `RebalancePlan`.

- **Plan 05-06 (Wave 4): Schedulers, REST API & Systematic Terminology Migration**
  - Implement daily EOD signal snapshot recorder and forward return maturation jobs in `scheduler/jobs.py`.
  - Expose `/api/v1/signals/*` endpoints and deprecate `/api/v1/analysis` with backward-compatible alias.
  - Migrate internal and external terminology from "prediction" to "signal".
  - Full end-to-end verification and safety-critical coverage check.
