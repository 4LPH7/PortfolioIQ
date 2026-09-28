---
phase: 04-portfolio-analytics
plan: 04
status: complete
commits: 1
completed_at: 2026-09-28T17:01:00Z
---

# Plan 04-04: Indian Transaction Cost Model & Rebalancer Sizing Constraints Summary

## Overview

Plan 04-04 upgraded PortfolioIQ's rebalancing engine with statutory Indian equity delivery cost modeling (`src/analytics/cost_calculator.py`), configurable execution constraints (`src/config/settings.py`), and robust order sizing controls (`src/analytics/rebalancer.py`).

## Key Accomplishments

1. **Indian Delivery Transaction Cost Engine (`src/analytics/cost_calculator.py`):**
   - Implemented `compute_indian_delivery_charges()` modeling:
     - Brokerage: Rs 0.00 (Zero brokerage on equity delivery).
     - Securities Transaction Tax (STT): 0.1% on buy and sell delivery (rounded to nearest rupee).
     - Exchange Turnover Fee: 0.00297% (NSE).
     - SEBI Turnover Charges: 0.0001% (Rs 10 / crore).
     - Stamp Duty: 0.015% on BUY delivery only (rounded to nearest rupee).
     - GST: 18% on service components (Brokerage + Exchange Fee + SEBI Fee).
     - Depository Participant (DP) Charges: Rs 15.34 (Rs 13.00 + 18% GST) on SELL delivery per scrip per day.
   - Computes drag in basis points (`cost_drag_bps`) and net settlement amounts.
   - Verified against Zerodha brokerage calculator test vectors for buys and sells.

2. **Rebalancer Application Settings (`src/config/settings.py`):**
   - Added strongly-typed, validated rebalancer configuration fields:
     - `rebalancer_min_trade_value`: Decimal("2000.00")
     - `rebalancer_cash_buffer_pct`: Decimal("0.02") (2% of AUM)
     - `rebalancer_cash_buffer_floor`: Decimal("5000.00") (Rs 5,000 floor)
     - `rebalancer_turnover_cap_pct`: Decimal("0.15") (15% daily turnover cap)
     - `rebalancer_adv_limit_pct`: Decimal("0.01") (1% of 20-day ADV)

3. **Constrained Rebalancer Engine (`src/analytics/rebalancer.py`):**
   - Attached `IndianTradeCost` to each generated `RebalanceOrder`.
   - Prioritized candidate drift signals by absolute drift magnitude descending ($|\text{drift\_pct}|$) so severe misallocations are fixed first.
   - Enforced daily turnover cap ($0.15 \times \text{AUM}$), halting order generation and setting `turnover_cap_reached = True` once exhausted.
   - Enforced cash reserve buffer ($\max(0.02 \times \text{AUM}, \text{Rs 5,000})$), preventing buy orders from eroding liquidity.
   - Enforced minimum trade size filter (Rs 2,000 threshold), while explicitly exempting 100% position liquidations to avoid orphaned positions.
   - Enforced liquidity guard capping orders at $\le 1.0\%$ of 20-day ADV.
   - Added convenience alias `generate_rebalance_orders()` and enhanced `get_rebalance_summary()` with itemized cost breakdowns.

4. **Testing and Verification:**
   - Authored `tests/test_cost_calculator.py` (5 tests) and `tests/test_rebalancer_constraints.py` (6 tests).
   - Updated `tests/test_config.py` with rebalancer settings assertions.
   - All 222 tests in test suite passed cleanly.
   - Maintained 100% coverage across all safety-critical paths (`scripts/check_critical_coverage.py`).
