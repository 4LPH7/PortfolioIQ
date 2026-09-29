---
phase: 05-make-the-signal-engine-evidence-based
plan: 06
status: complete
commits: 1
completed_at: 2026-09-29T13:45:00Z
---

# Plan 05-06: Rebalancer Evidence Hurdle Gate & Fail-Closed Order Suppression Summary

## Overview

Plan 05-06 implemented the fail-closed quantitative evidence hurdle gate in the PortfolioIQ rebalancing engine (`src/analytics/rebalancer.py`) and authoring test suites (`tests/test_rebalancer.py` and `tests/test_rebalance_evidence_gate.py`). It enforces the 4 quantitative evidence hurdles required to classify a scrip's quantitative signal model as `PROVEN_EDGE` versus `UNPROVEN_NOISE`, strictly suppresses tactical trade order generation for unproven signals, preserves strategic asset allocation drift orders with transparent evidence badges, and tracks `orders_suppressed_unproven_noise` metrics (D-06).

## Key Accomplishments

1. **Quantitative Evidence Hurdle Classifier (`evaluate_evidence_hurdles`):**
   - **Hurdle 1 (Predictive Power):** Out-of-sample Spearman rank Information Coefficient $\overline{\text{IC}} > 0$ with statistical significance $p < 0.05$ on at least one unpruned indicator.
   - **Hurdle 2 (Stock Timing Skill):** Strategy net annualized return exceeds Stock Buy-and-Hold: $\text{CAGR}_{\text{strategy, net}} > \text{CAGR}_{\text{stock}}$.
   - **Hurdle 3 (Market Excess Alpha):** Strategy net annualized return exceeds NIFTY 50 TRI benchmark: $\text{CAGR}_{\text{strategy, net}} > \text{CAGR}_{\text{benchmark}}$.
   - **Hurdle 4 (Trade Quality):** Win rate $\ge 50\%$, Profit Factor $> 1.0$, and total out-of-sample trades $\ge 5$.
   - Returns classification status (`PROVEN_EDGE`, `UNPROVEN_NOISE`, `PENDING`), user-facing badge string, and detailed boolean check breakdown.

2. **Rebalancer Evidence Gate & Tactical Tilt Integration (`generate_rebalance_plan`):**
   - Updated `RebalanceOrder` with `evidence_status` (default `"PENDING"`) and `evidence_badge`.
   - Updated `OrderReason` enum with `OrderReason.TACTICAL_SIGNAL = "TACTICAL_SIGNAL"`.
   - Updated `RebalancePlan` to track `orders_suppressed_unproven_noise: int = 0`.
   - **Fail-Closed Suppression:** Tactical trade proposals driven by signals tagged as `UNPROVEN_NOISE` are strictly blocked, incrementing `orders_suppressed_unproven_noise` and logging `[GATE] Suppressed order for {symbol}: Unproven signal (Failed Hurdle).`
   - **Strategic Drift Preservation:** Core portfolio rebalancing orders (`SECTOR_DRIFT`, `HOLDING_DRIFT`, `CONCENTRATION_BREACH`) are preserved and annotated with evidence status and badges for full audit transparency.
   - Optional `suppress_unproven_buys: bool` flag allows conservative portfolios to block even drift buys into unproven scrips.

3. **Summary & Serialization Updates (`get_rebalance_summary`):**
   - Exposed `orders_suppressed_unproven_noise` in the API and dashboard JSON rebalance manifest.
   - Serialized `evidence_status` and `evidence_badge` on every proposed order.

4. **Comprehensive Test Suite & Quality Gates:**
   - Authored `tests/test_rebalance_evidence_gate.py` with 11 tests covering each of the 4 hurdles individually, fail-closed tactical suppression, tactical approval for proven edge, strategic drift badge preservation, and summary serialization.
   - Authored `tests/test_rebalancer.py` with 7 tests validating data structures, enum values, resolution lookups, and aliases.
   - 312 total tests passing in 15.93s.
   - 89.96% overall test coverage (above 75% requirement).
   - 100% safety-critical coverage maintained across execution, config, and middleware.
   - Zero Ruff lint or format issues across 149 files.
