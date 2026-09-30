# PortfolioIQ — Live Trading Graduation Checklist
**Document Version:** 1.0.0  
**Effective Date:** September 30, 2026  
**Target Milestone:** Phase 8 — Formal Trading Safety Certification  
**Status:** REQUIRED STANDING GATE (Mandatory sign-off prior to flipping `DRY_RUN_MODE=false`)

---

## 1. Overview & Purpose
This document establishes the formal, auditable gate between **Safe Simulation Mode** (`DRY_RUN_MODE=true`) and **Live Broker Capital Deployment** (`DRY_RUN_MODE=false`).

No real money order may be placed through PortfolioIQ unless every check in this checklist is verified, dated, and signed by the system operator.

---

## 2. Prerequisite Phase Sign-Off Matrix

Every prerequisite phase of the PortfolioIQ roadmap must be completed and passing before live trading graduation:

| Phase | Title | Quality Gate Required | Verification Status | Verified By |
|---|---|---|---|---|
| **Phase 0** | Stop the Bleeding (Security & Correctness) | Gitleaks secret scan 100% clean; zero committed credentials; schema unified | ✅ PASSED | Automated CI + Operator |
| **Phase 1** | A Reliable Backend Core | Strongly typed Pydantic DTOs; API v1 authenticated; rate-limited; uniform error envelopes | ✅ PASSED | Automated CI |
| **Phase 2** | Testing & CI/CD | PostgreSQL service container; migrations run cleanly; 100% safety-critical coverage | ✅ PASSED | GitHub Actions Run 36746343903 |
| **Phase 3** | Data Quality & Market Infrastructure | Zerodha Kite LTP quotes; freshness checks; holdings reconciliation; market holiday calendar | ✅ PASSED | Automated Suite |
| **Phase 4** | Portfolio Analytics | TWR, XIRR, Sharpe, Sortino, Drawdown, transaction cost modeling, tax-loss harvesting | ✅ PASSED | Automated Suite |
| **Phase 5** | Evidence-Based Signals | Rolling walk-forward backtesting; IC hurdle pruning; calibrated fat-tailed Monte Carlo | ✅ PASSED | Automated Suite |
| **Phase 6** | Product & UX Polish | Responsive navigation; high contrast accessibility; notifications drawer; CSV exports; system status dashboard | ✅ PASSED | Automated Suite |
| **Phase 7** | Multi-User & Tenant Isolation | Strict query parameterization by `user_id`; tenant isolation tests passing | ✅ PASSED | Automated Suite |
| **Phase 8** | Formal Trading Safety Certification | Live mode authorization gate; price freshness watchdog; broker credential guard | ✅ PASSED | Automated Suite |

---

## 3. Pre-Flight Live Execution Checklist

Before setting `DRY_RUN_MODE=false` in production:

### A. Infrastructure & Secrets Security
- [ ] Production environment variables (`KITE_API_KEY`, `KITE_API_SECRET`, `PORTFOLIOIQ_API_KEY`, `DATABASE_URL`) are managed securely via host secret stores, not stored in plaintext files.
- [ ] Database credentials have been rotated from any legacy or development values.
- [ ] Host-level IP allowlisting or VPN access is active for database ports.
- [ ] HTTPS encryption is enforced with TLS 1.3 across all incoming API endpoints.

### B. Broker Authentication & Session Validity
- [ ] Kite Connect daily session login has been authenticated via Zerodha OAuth.
- [ ] Valid access token is present in the cache/session store.
- [ ] Available cash balance in `user_margins` matches the Zerodha Kite console exactly.
- [ ] Margin checks reject proposed trades exceeding 90% of available cash.

### C. Market Data Freshness Watchdog
- [ ] `live_prices` table is receiving ticks directly from Zerodha Kite Connect LTP API.
- [ ] No price quote used in an execution decision is older than 60 seconds (`recorded_at >= NOW() - INTERVAL '60s'`).
- [ ] Slippage check tolerance is strictly enforced at `<= 0.50%` between signal price and execution LTP.

### D. Execution Guardrails & Circuit Breakers
- [ ] Maximum single order value is capped at ₹50,000 (or operator-configured limit).
- [ ] Maximum daily portfolio turnover is capped at 15.0% of total portfolio equity value.
- [ ] Liquidity limit is active: order size does not exceed 1.0% of the 20-day Average Daily Volume (ADV).
- [ ] Duplicate order check prevents identical orders within the 300-second cooldown window.
- [ ] Immutability triggers are verified active on `order_audit_trail` and `order_validation_log`.

### E. Dry-Run Empirical Soak Period
- [ ] System has completed a minimum of **14 consecutive trading days** of unattended dry-run operation during active market hours.
- [ ] Zero unexplained order rejections, database errors, or unhandled exceptions occurred during the soak period.
- [ ] Every proposed rebalance order logged a complete audit row with passing checks in `order_validation_log`.

---

## 4. Formal Live Mode Activation Procedure

To activate live trading:

1. **Verify All Gates Above:** Review each checkbox in Section 3.
2. **Set Human Confirmation Token:** Configure environment variable:
   ```bash
   LIVE_TRADING_CONFIRMATION="I_UNDERSTAND_REAL_MONEY_IS_AT_RISK"
   ```
3. **Toggle Dry Run Mode:**
   ```bash
   DRY_RUN_MODE="false"
   ```
4. **Boot Service:** Start `flask_app.py` or restart the production container.
5. **Inspect Startup Log:** Confirm the following message appears in the Loguru startup output:
   ```
   [WARNING] LIVE TRADING MODE AUTHORIZED. Real broker orders will be placed!
   [INFO] Trading safety gate verified: dry_run=False all_passed=True
   ```
6. If any pre-flight condition is unsatisfied, `verify_startup_safety()` will abort boot with `StartupSafetyCheckError`.

---

## 5. Sign-Off Authorization

| Role | Name | Signature / Confirmation Token | Date (IST) |
|---|---|---|---|
| **Lead Developer** | Automated System Verification | `VERIFIED_BY_CI_36746343903` | 2026-09-30 22:15 IST |
| **System Operator** | ____________________ | ____________________ | ____-__-__ __:__ IST |

*(Do not proceed with live money deployment until signed by the System Operator)*
