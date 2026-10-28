# Plan: Adopt the useful parts of four trading repos

## Context

You asked for the most useful skills, methods and techniques from four repos for PortfolioIQ. I read all four and checked each idea against the code:

- TradingAgents: landing page and repo summary.
- day-trader-skill: repo summary.
- Vibe-Trading: landing page plus the first 100k of a 419k-character README.
- Lean: landing page only.

Most of what they offer is already in PortfolioIQ, or doesn't fit an NSE delivery, end-of-day, advisory-first system. The useful findings are four gaps in PortfolioIQ's own code (below). This plan fixes those and declines the rest.

## Repo verdicts

| Repo | License | What it offers | Verdict |
|---|---|---|---|
| TauricResearch/TradingAgents | Apache-2.0 | LLM firm: analysts, bull/bear debate, trader, risk, PM. Memory with reflection, a point-in-time data rule, backtests | Mostly built already as `src/research/committee.py` (same pipeline, quick/deep model tiers, advisory only, budget, citation check, outcome settlement in `runs.py`). **Adopt:** point-in-time rule (T5), reflection (T6). **Decline:** LLM-made orders, multi-provider sprawl |
| QuantConnect/Lean | Apache-2.0 | Event-driven C#/Python engine with pluggable alpha, portfolio, risk and execution models, plus fee, slippage and fill models | **Adopt the concept only:** volume-based slippage (T2, T3). **Decline** the framework rewrite: this is a batch end-of-day system, Lean is a .NET stack, and I saw no NSE/India support |
| HKUDS/Vibe-Trading | MIT | Broad LLM trading platform: agent loop, 478-factor zoo, A-share/crypto/options engines, 18 brokers, chat channels, autonomous live LLM decisions | **Adopt:** run fingerprint (T4) as an idea. **Decline** the rest: out of market scope. Autonomous live LLM decisions conflict with the Phase 8 human-approval gate. A kill switch already exists here |
| Jakeschincariol/day-trader-skill | MIT | 11 Claude skills for US intraday trading via Alpaca: opening-range breakout, bracket orders, PDT rule | **Nothing new.** Sizing by max loss, a pre-trade PASS/BLOCK gate, and target-vs-stop simulation already exist (`trade_odds.py`, `gatekeeper.py`). Its strategies and data feeds don't apply to NSE |

No code is copied from any of these. The ideas are implemented fresh, as `committee.py` was. All four licenses permit that.

## Gaps confirmed in our code

1. **Signals fill at the same close that triggered them.** In `run_walk_forward_backtest` (`src/analytics/walk_forward.py`), the factors at bar *t* use close *t*, and the buy or sell is booked at close *t*. No order can be placed at a close that has already been seen, so the backtest is optimistic.
2. **No slippage in the signal backtest.** It charges statutory delivery fees only (`cost_calculator.py`). The allocation optimizer uses a flat 10 bps (`optimizer.py`, `_trade_cost_fraction`) and ignores trade size against liquidity. Volume is stored per bar (migration 021), so impact can be modelled.
3. **Backtest runs have no input fingerprint.** `backtest_runs` (migration 020) has no hash column, so two runs can't be shown to share the same data and settings.
4. **Committee backtests would leak future data.** Fundamentals are stored as timestamped snapshots (`fundamentals_snapshots.fetched_at`), starting from when collection began. A run dated before that must not get today's values. Prices are already as-of the snapshot date.

Already fine, no task needed: factors are causal (rolling windows, no negative shifts), a deployment kill switch exists, committee model tiers exist, the citation check exists, and the committee has a budget.

## Architecture decisions

- **Changes stay in existing modules.** No new engine, framework or config layer.
- **Decide at close *t*, fill at open *t+1*.** The same-close numbers are generated once for a before/after report by running current `main` in a `git worktree`. They are not kept as a product option.
- **Impact model is pure and causal.** Let p = trade value ÷ ADV, where ADV is the mean of Close×Volume over the 20 bars *before* the decision bar. Then `impact_bps = min(100, 50 × sqrt(p))`. Missing or zero ADV returns the 100 bps cap, which is the conservative choice. The constant 50 is set so that 1% of ADV costs 5 bps. That anchor is my assumption, not a calibrated value, and it is labelled as such in the code.
- **Allocation optimizer keeps its current timing and flat 10 bps** (your choice). It is recorded as a known gap.
- **Committee stays advisory.** Add a test that nothing under `src/research/` imports from `src/execution/`. I have not checked whether such a test already exists.
- **Branch:** `claude/upbeat-heisenberg-uyk4xf`. Push to `main` only when you ask.

## Dependency graph

```
T1 next-open fill ──┐
                    ├─► T3 impact in backtest ─► T4 fingerprint ─► CHECKPOINT A
T2 impact function ─┘

T5 point-in-time guard ─► T6 outcome feedback (gated) ─► CHECKPOINT B
```

## Task list

### Phase 1: Realistic signal backtest

- [x] **T1: Fill at next session's open** (size S, 2 files)
  - **Description:** A decision made on bar *t*'s close fills at bar *t+1*'s Open. A signal on the final bar of the test window produces no fill.
  - **Acceptance:**
    - [ ] A synthetic series where a signal fires at bar *t* fills at Open[*t+1*], not Close[*t*].
    - [ ] No fill is booked on the last bar of the window.
    - [ ] Existing tests are changed only where they encode same-close fills, and each changed expectation is listed in the PR.
  - **Verification:** `pytest tests/test_walk_forward.py -v`
  - **Files:** `src/analytics/walk_forward.py`, `tests/test_walk_forward.py`
  - **Depends on:** none

- [ ] **T2: Volume impact function** (size S, 2 files)
  - **Description:** Add `estimate_impact_bps(trade_value, adv_value)` to `cost_calculator.py`. It is a pure function with no DB access, following the formula in Architecture decisions.
  - **Acceptance:**
    - [ ] 1% of ADV gives 5 bps (±0.01).
    - [ ] The result is non-decreasing in trade size and never exceeds 100 bps.
    - [ ] Zero or missing ADV returns 100 bps.
  - **Verification:** `pytest tests/test_cost_calculator.py -v`
  - **Files:** `src/analytics/cost_calculator.py`, `tests/test_cost_calculator.py`
  - **Depends on:** none

- [ ] **T3: Apply impact in the signal backtest and report it** (size M, 3 files)
  - **Description:** Buys pay the next open plus impact. Sells receive the next open minus impact. ADV is computed causally from prior bars only. Report slippage drag in INR and as a percentage of gross P&L.
  - **Acceptance:**
    - [ ] On two synthetic stocks with identical prices but different volumes, the less liquid one shows lower net return.
    - [ ] The returned DTO includes slippage drag. It is persisted only if the existing repository save path already has a JSON field for it; otherwise it is returned in the API response only.
    - [ ] Before/after numbers for the same symbols as T1 are recorded (see Verification).
  - **Verification:** `pytest tests/test_walk_forward.py tests/test_cost_calculator.py -v`
  - **Files:** `src/analytics/walk_forward.py`, `src/models/dtos.py`, `tests/test_walk_forward.py`
  - **Depends on:** T1, T2

### Phase 2: Auditability

- [ ] **T4: Fingerprint each backtest run** (size M, 5 files)
  - **Description:** Store `inputs_hash` = sha256 of canonical JSON. The JSON covers symbol, model version, train/test/step windows, forward horizon, capital, risk-free rate, impact constant and cap, fill rule name, last bar date, row count, and a digest of the Open/Close/Volume values. Store it in a new nullable column via migration `031_backtest_inputs_hash.sql` (031 is the next free number; re-check before creating). Return it from the backtest API.
  - **Acceptance:**
    - [ ] Two runs on identical inputs give the same hash.
    - [ ] Changing any one listed input (a window, the cap, a single bar's close) changes the hash. Use a parametrized test.
    - [ ] The migration applies to a fresh database and to one with existing rows. Old rows read as NULL.
    - [ ] The API response includes `inputs_hash`.
  - **Verification:** `pytest tests/test_signal_recorder.py tests/test_migrations.py -v`
  - **Files:** `db/migrations/031_backtest_inputs_hash.sql` (new), `src/analytics/signal_recorder.py`, `src/db/repository.py`, `src/models/dtos.py`, `tests/test_signal_recorder.py`
  - **Depends on:** T3 (the hash must cover the fill rule and impact parameters)

### Checkpoint A: after T1–T4

- [ ] Full suite green in CI (Postgres service), `ruff check` and `ruff format --check` clean, coverage gates unchanged
- [ ] Before/after table reviewed with you. Results will probably drop, and that is the point
- [ ] Decide whether any backtest numbers already quoted in docs or `.planning/` get re-run or labelled as pre-fix

### Phase 3: Committee (you selected these; start only after Checkpoint A and a go-ahead)

- [ ] **T5: Point-in-time guard for committee snapshots** (size M, 3 files)
  - **Description:** For a past `as_of` date, use only `fundamentals_snapshots` rows with `fetched_at ≤ as_of`. If none exist, mark fundamentals as unavailable for that date instead of using today's values. Refuse committee backtests dated before a symbol's first fundamentals row.
  - **Acceptance:**
    - [ ] An `as_of` before the first row yields "unavailable", not the current value.
    - [ ] With two rows, an `as_of` between them uses the earlier row.
    - [ ] `prompt_hashes()` is unchanged, so live prompts are untouched.
  - **Verification:** `pytest tests/test_research.py -v`
  - **Files:** `src/research/fundamentals.py`, `src/research/snapshot.py`, `tests/test_research.py`
  - **Depends on:** none (no committee backtest exists yet)

- [ ] **T6: Feed settled outcomes into the next committee run** (size M, 3 files; gated)
  - **Description:** Include the last N settled runs for the same symbol in the PM's input as data, each with its as-of date, horizon returns and alpha vs NIFTY. Advisory only.
  - **Gate:** at least N settled runs per symbol (proposed N = 20, to confirm) and T5 complete.
  - **Acceptance:**
    - [ ] Only runs settled before the current `as_of` are included.
    - [ ] The no-path-to-orders test from Architecture decisions passes.
    - [ ] The worst-case budget check still passes with the extra context.
  - **Verification:** `pytest tests/test_research.py -v`
  - **Files:** `src/research/committee.py`, `src/research/runs.py`, `tests/test_research.py`
  - **Depends on:** T5

### Checkpoint B: after T5–T6

- [ ] Committee tests green, prompt hashes reviewed, budget behaviour confirmed
- [ ] Decide whether to extend the same changes to the allocation optimizer (currently out of scope)

## Explicitly not doing

- Vibe-Trading's autonomous live LLM trading, multi-market engines, 18 broker connectors, chat channels and 478-factor zoo. None fit NSE delivery, and autonomous live decisions conflict with the Phase 8 human-approval gate.
- Lean's event-driven framework and .NET stack. Only the slippage concept is adopted.
- day-trader-skill's strategies and data feeds (US intraday, Alpaca, PDT rule, options and shorting, SEC/Nasdaq/House Clerk). Its ideas are already covered.
- TradingAgents' multi-provider LLM setup. We already have one provider, a budget and a kill switch.

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Next-open fills and impact lower reported returns, which may be unwelcome | High | Before/after table at Checkpoint A, with the causes split out (timing vs impact) |
| Impact constant (50, cap 100) is uncalibrated | Medium | Labelled as an assumption in code; included in `inputs_hash`; show a sensitivity table at 0, 5, 10 and 25 bps |
| Migration number 031 taken by another branch | Low | Re-check the migrations folder right before creating the file |
| Some tests need Postgres. Locally, 11 tests already fail without a database (in `test_api_v1`, `test_onboarding_import`, and the rebalancer tests) | Medium | Run the suite in CI or with the docker compose database. Don't count those 11 as regressions |
| Reflection (T6) adds prompt cost and self-reinforcing noise | Medium | Gated on sample size and T5; advisory only; budget check |

## Open questions

1. **T6 gate:** Is N = 20 settled runs per symbol the right threshold?
2. **Impact anchor:** Is "1% of ADV costs 5 bps, capped at 100 bps" an acceptable starting assumption?
3. **Published numbers:** Do any Phase 5 verification docs quote backtest results that would change? I have not checked this yet. It should be settled at Checkpoint A.

## Verification (end to end)

- `pytest tests/test_walk_forward.py tests/test_cost_calculator.py tests/test_migrations.py tests/test_signal_recorder.py tests/test_research.py -q`
- Full suite, `ruff check`, `ruff format --check` and coverage gates in CI.
- Before/after report: check out current `main` into a `git worktree`. Run `run_walk_forward_backtest` on three NSE symbols with that version and with the branch. Tabulate CAGR, Sharpe, max drawdown, trade count and slippage drag. Put the table in the PR body, not in the repo.

## After approval

- Write this plan to `tasks/plan.md`.
- Write the checklist above to `tasks/todo.md`.
- `tasks/` does not exist yet, so there are no unchecked tasks to overwrite.
- No commits or pushes until you ask for them.
