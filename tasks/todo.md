# TODO: Adopt the useful parts of four trading repos

Full plan, acceptance criteria and risks: [plan.md](plan.md)

## Phase 1: Realistic signal backtest
- [x] T1: Fill at next session's open (`walk_forward.py`): S, no deps
- [x] T2: Volume impact function `estimate_impact_bps` (`cost_calculator.py`): S, no deps
- [x] T3: Apply impact in signal backtest and report slippage drag: M, needs T1 + T2

## Phase 2: Auditability
- [x] T4: `inputs_hash` per backtest run (migration 031, re-check number): M, needs T3

## Checkpoint A
- [ ] Full suite green in CI, ruff clean, coverage gates unchanged
- [ ] Before/after table (main worktree vs branch, 3 NSE symbols) reviewed with user
- [ ] Decide on re-running or labelling backtest numbers quoted in docs

## Phase 3: Committee (start only after Checkpoint A and a go-ahead)
- [ ] T5: Point-in-time guard for committee fundamentals: M, no deps
- [ ] T6: Feed settled outcomes into next committee run: M, needs T5, gated on N settled runs (N = 20 proposed)

## Checkpoint B
- [ ] Committee tests green, prompt hashes reviewed, budget confirmed
- [ ] Decide whether to extend fixes to the allocation optimizer

## Open questions
- [ ] Confirm T6 gate N = 20
- [ ] Confirm impact anchor: 1% of ADV = 5 bps, cap 100 bps
- [ ] Check Phase 5 docs for backtest numbers that will change
