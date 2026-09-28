# Phase 05: Make the Signal Engine Evidence-Based - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.  
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-09-28  
**Phase:** 05-make-the-signal-engine-evidence-based  
**Areas discussed:** Signal History & Forward Outcome Storage, Walk-Forward Backtesting Engine, Evidence Hurdles & Benchmark Baselines, Monte Carlo Calibration & UI Terminology Migration  

---

## 1. Signal History & Forward Outcome Storage

| Option | Description | Selected |
|--------|-------------|----------|
| **Dedicated `signal_snapshots` Table** | Stores daily EOD signal state, model version, composite score, signal label, indicators JSONB, and benchmark price | ✓ |
| **Lightweight Log in `order_audit_trail`** | Only record signals when orders are placed | |
| **JSON Flat Files on Disk** | Dump daily signal files to disk | |

| Option | Description | Selected |
|--------|-------------|----------|
| **Multi-Horizon Tracking (5, 20, 60 Trading Days)** | Captures realized forward returns across ~1w, ~1m, ~1q alongside NIFTY 50 TRI and excess alpha | ✓ |
| **Single Fixed Horizon (20 Days Only)** | Only track ~1 month forward return | |
| **Daily Rolling Horizon** | Record continuous forward returns every day | |

**Notes:** User chose 1A + 2A for comprehensive, institutional-grade auditability and multi-horizon performance validation.

---

## 2. Walk-Forward Backtesting Engine

| Option | Description | Selected |
|--------|-------------|----------|
| **Rolling 252-Train / 63-Test Windows** | 1-year in-sample calibration and 1-quarter out-of-sample evaluation stepped forward across historical daily prices | ✓ |
| **Expanding Window** | Anchored start date with expanding in-sample window | |
| **Single Static Train/Test Split** | Standard 70/30 chronological split | |

| Option | Description | Selected |
|--------|-------------|----------|
| **Independent Indicator Evaluation (Spearman IC & Pruning)** | Rank IC and p-values for each indicator; prune $IC \le 0$ or $p > 0.05$; weight composite by out-of-sample IC/IR | ✓ |
| **Static Fixed Weights** | Manually set fixed heuristic weights for RSI, MACD, MA crossover | |
| **Equal Weighting** | Give all indicators identical 1/N weights without validation | |

**Notes:** User chose 1A + 2A to enforce zero-lookahead walk-forward testing and statistical rigor for indicator inclusion.

---

## 3. Evidence Hurdles & Benchmark Baselines

| Option | Description | Selected |
|--------|-------------|----------|
| **Dual Baseline (Stock Buy-and-Hold + NIFTY 50 TRI)** | Measures market-timing skill against the asset itself AND market alpha against broad index | ✓ |
| **NIFTY 50 TRI Only** | Benchmark against the market index only | |
| **Risk-Free Rate Only** | Compare against 91-day T-Bill rate only | |

| Option | Description | Selected |
|--------|-------------|----------|
| **Gated Signal Status (`PROVEN_EDGE` vs `UNPROVEN_NOISE`)** | Rebalancer strictly blocked from proposing trades on signals unless backed by `PROVEN_EDGE` | ✓ |
| **Informational UI Warning Only** | Display warnings on unproven signals but allow rebalancer to trade | |
| **No Gating** | Generate trades on heuristic signals regardless of evidence | |

**Notes:** User chose 1A + 2A to prevent deploying capital on unproven heuristic signals, enforcing a strict fail-closed safety gate.

---

## 4. Monte Carlo Calibration & UI Terminology Migration

| Option | Description | Selected |
|--------|-------------|----------|
| **Fat-Tailed Simulation (Student's t / Bootstrap) & Calibrated Cones** | Model tail risk with empirical coverage calibration (80%, 95%); ban point "Target Price" in favor of P10, P50, P90 | ✓ |
| **Standard Gaussian Normal Simulation** | Standard geometric Brownian motion with point target price | |
| **No Simulation** | Remove Monte Carlo entirely | |

| Option | Description | Selected |
|--------|-------------|----------|
| **Systematic Terminology Migration** | Rename `/api/v1/analysis` -> `/api/v1/signals` (with alias), `HoldingSignalDTO`, "Quantitative Signal Engine" | ✓ |
| **Retain "Prediction" Terminology** | Continue calling outputs predictions | |
| **Internal Refactor Only** | Keep external API and UI labels unchanged | |

**Notes:** User chose 1A + 2A for full alignment with quantitative realities, eliminating deceptive "prediction" language and uncalibrated point targets.

---

## Deferred Ideas

- Deep learning / LSTM sequence models (requires high-frequency tick data and GPU pipelines; out of scope for personal swing portfolio).
- Alternative data (social sentiment, news NLP) — keep strictly to price/volume and fundamental data until core quantitative signals are validated.
- Cross-asset multi-factor portfolio optimization (Barra-style risk model deferred to Phase 7: Scale).
