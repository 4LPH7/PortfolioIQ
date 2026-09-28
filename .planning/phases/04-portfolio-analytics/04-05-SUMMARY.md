---
phase: 04-portfolio-analytics
plan: 05
status: complete
commits: 1
completed_at: 2026-09-28T17:06:00Z
---

# Plan 04-05: Tax-Loss Harvesting Engine, 30-Day LTCG Lock & FY Exemption Tracker Summary

## Overview

Plan 04-05 upgraded PortfolioIQ's tax management engine in `src/analytics/tax_guard.py` with tax-optimized lot selection, strict near-LTCG conversion protection (30-day lock), annual Indian Financial Year (April 1 to March 31) realized LTCG ledger tracking toward the ₹1.25L tax-free threshold, and proactive Q4 tax-gain harvesting advisory generation.

## Key Accomplishments

1. **Tax-Optimized Lot Selection (`select_tax_optimized_lots`):**
   - Replaced naive FIFO lot execution with a post-tax compound return maximization hierarchy:
     1. **Tier 1 (STCL):** Short-Term Capital Loss lots, prioritized by highest % loss first to maximize harvested 20% tax shields.
     2. **Tier 2 (LTCL):** Long-Term Capital Loss lots, prioritized by highest % loss first (12.5% tax shield).
     3. **Tier 3 (LTCG):** Long-Term Capital Gain lots, prioritized by lowest gain % first (12.5% tax rate).
     4. **Tier 4 (STCG):** Short-Term Capital Gain lots (`days_to_ltcg > 30`), lowest gain % first (20% tax rate).
     5. **Tier 5 (Near-LTCG Deferral):** Profitable lots aged 335-365 days (`days_to_ltcg <= 30`) are strictly deferred to prevent the 7.5% tax penalty (20% STCG vs 12.5% LTCG), unless a 100% position exit (`is_full_liquidation == True`) is explicitly authorized.
   - Computes estimated P&L and tax liability for each allocated lot.

2. **Indian Financial Year Boundaries (`get_financial_year_bounds`):**
   - Accurately computes Indian tax financial year start/end dates (April 1 - March 31) and formatted labels (e.g. `FY 2025-26`, `FY 2026-27`) across calendar transitions.

3. **Annual Tax Harvesting & Exemption Tracker (`get_annual_tax_harvesting_summary`):**
   - Connects to `get_realized_ltcg_ytd()` to track realized LTCG against the statutory ₹1.25L tax-free threshold.
   - Detects all unrealized loss lots across holdings and generates `LOSS_HARVEST` recommendations with estimated tax savings (20% for STCL, 12.5% for LTCL).
   - Generates `NEAR_LTCG_DEFER` warnings for profitable holdings approaching the 365-day cutoff.
   - In Q4 (Jan 1 - Mar 31), if remaining LTCG exemption is positive, proposes proactive `GAIN_HARVEST` trades up to the remaining exemption pool to harvest gains tax-free.

4. **Comprehensive Unit Testing (`tests/test_tax_guard.py`):**
   - Authored 7 comprehensive unit test cases covering FY boundary switching, lot hierarchy sorting, Near-LTCG deferral, Q4 gain harvesting with remaining vs exhausted exemptions, loss harvesting savings, and dashboard summaries.
   - All 229 tests in the test suite passed cleanly with 100% coverage on safety-critical paths.
