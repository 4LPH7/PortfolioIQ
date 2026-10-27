"""
Research committee runs: an atomic daily quota, idempotent requests, one background thread per
run, a sweep for runs that never finished, and settlement of each rating against later returns.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import UTC, datetime, timedelta

import pandas as pd
from loguru import logger
from sqlalchemy import text

from src.db.connection import execute_sql, get_db_session

CALL_TIMEOUT_SEC = 180.0
MIN_SCORED = 15  # compute_spearman_ic's own minimum
SUMMARY_COLS = """id, tradingsymbol, status, run_date, data_as_of, cost_usd::float AS cost_usd,
    budget_usd::float AS budget_usd, error, decision->>'rating' AS rating,
    (decision->>'conviction')::int AS conviction, grounding->'rate' AS grounding_rate,
    alpha_20d::float AS alpha_20d, created_at, finished_at"""


class QuotaError(Exception):
    pass


def research_enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _cfg() -> dict:
    rows = execute_sql("SELECT key, value FROM system_config WHERE key LIKE 'research_%'")
    c = {r["key"]: r["value"] for r in rows}
    return {
        "quick": c.get("research_model_quick", "claude-opus-5-5"),
        "deep": c.get("research_model_deep", "claude-opus-5-5"),
        "daily_limit": int(c.get("research_daily_run_limit", 5)),
        "budget": float(c.get("research_max_cost_usd_per_run", 1.0)),
        "timeout_min": int(c.get("research_run_timeout_min", 15)),
    }


def _today() -> str:
    from src.ingestion.market_hours import today_ist

    return str(today_ist())


def _sweep(session, user_id: str, timeout_min: int) -> int:
    # ponytail: age-based only, so a run orphaned by a restart shows "running" until the timeout
    return session.execute(
        text(
            """UPDATE research_runs SET status = 'abandoned', finished_at = NOW(),
                   error = COALESCE(error, 'Did not finish within the time limit')
               WHERE user_id = :u AND status IN ('queued', 'running')
                 AND created_at < NOW() - make_interval(mins => :m)"""
        ),
        {"u": user_id, "m": timeout_min},
    ).rowcount


def sweep_abandoned(user_id: str = "default") -> int:
    with get_db_session() as s:
        return _sweep(s, user_id, _cfg()["timeout_min"])


def enqueue_run(user_id: str, symbol: str, idempotency_key: str) -> tuple[dict, bool]:
    """
    (run, created). One transaction under a per-user advisory lock: a repeated key returns its
    run, otherwise the quota is checked and the row inserted, so two racing requests cannot
    both pass the check. Raises QuotaError.
    """
    cfg = _cfg()
    with get_db_session() as s:
        s.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"research:{user_id}"})
        found = (
            s.execute(
                text(
                    f"SELECT {SUMMARY_COLS} FROM research_runs "
                    "WHERE user_id = :u AND idempotency_key = :k"
                ),
                {"u": user_id, "k": idempotency_key},
            )
            .mappings()
            .first()
        )
        if found:
            return dict(found), False
        _sweep(s, user_id, cfg["timeout_min"])
        counts = (
            s.execute(
                text(
                    """SELECT
                         COUNT(*) FILTER (WHERE status IN ('queued', 'running')) AS active,
                         COUNT(*) FILTER (WHERE run_date = :d AND NOT
                             (status IN ('failed', 'abandoned') AND cost_usd = 0)) AS today
                       FROM research_runs WHERE user_id = :u"""
                ),
                {"u": user_id, "d": _today()},
            )
            .mappings()
            .one()
        )
        if counts["active"]:
            raise QuotaError("A research run is already in progress. Wait for it to finish.")
        if counts["today"] >= cfg["daily_limit"]:
            raise QuotaError(f"The daily limit of {cfg['daily_limit']} research runs is used up.")
        row = (
            s.execute(
                text(
                    f"""INSERT INTO research_runs
                          (user_id, idempotency_key, tradingsymbol, run_date, budget_usd)
                        VALUES (:u, :k, :sym, :d, :b)
                        RETURNING {SUMMARY_COLS}"""
                ),
                {
                    "u": user_id,
                    "k": idempotency_key,
                    "sym": symbol,
                    "d": _today(),
                    "b": cfg["budget"],
                },
            )
            .mappings()
            .one()
        )
        return dict(row), True


def start_run(run_id: int) -> None:
    threading.Thread(
        target=execute_run, args=(run_id,), daemon=True, name=f"research-{run_id}"
    ).start()


def _jsonb(value) -> str | None:
    return None if value is None else json.dumps(value, sort_keys=True, default=str)


def _save(run_id: int, out: dict) -> None:
    execute_sql(
        """UPDATE research_runs SET stages = CAST(:stages AS JSONB), calls = CAST(:calls AS JSONB),
               cost_usd = :cost WHERE id = :id""",
        {
            "id": run_id,
            "stages": _jsonb(out.get("stages", {})),
            "calls": _jsonb(out.get("calls", [])),
            "cost": out.get("cost_usd", 0),
        },
    )


def _finish(run_id: int, status: str, error: str | None, out: dict | None = None) -> None:
    out = out or {}
    if out:
        _save(run_id, out)
    # A run the sweep already marked abandoned keeps that status; its cost is still recorded.
    execute_sql(
        """UPDATE research_runs SET status = :st, error = :err, finished_at = NOW(),
               decision = CAST(:decision AS JSONB), grounding = CAST(:grounding AS JSONB)
           WHERE id = :id AND status = 'running'""",
        {
            "id": run_id,
            "st": status,
            "err": error,
            "decision": _jsonb(out.get("decision")),
            "grounding": _jsonb(out.get("grounding")),
        },
    )


def _build_snapshot(user_id: str, symbol: str) -> dict:
    from src.analytics.historical_cache import get_or_sync_historical_bars
    from src.analytics.valuator import compute_portfolio_valuation
    from src.research.fundamentals import latest_fundamentals
    from src.research.snapshot import build_snapshot

    bars = get_or_sync_historical_bars(symbol, lookback_days=400)
    held = next(
        (h for h in compute_portfolio_valuation(user_id).holdings if h.tradingsymbol == symbol),
        None,
    )
    holding = (
        {"weight_pct": float(held.weight_pct), "unrealised_pnl_pct": float(held.unrealised_pnl_pct)}
        if held
        else None
    )
    return build_snapshot(symbol, bars, holding, latest_fundamentals(symbol), datetime.now(UTC))


def execute_run(run_id: int, client=None) -> None:
    """Runs on its own thread. Every outcome, including a crash, ends in a terminal status."""
    import anthropic

    from src.research.committee import CommitteeConfig, prompt_hashes, run_committee
    from src.research.snapshot import SNAPSHOT_SCHEMA_VERSION

    claimed = execute_sql(
        """UPDATE research_runs SET status = 'running', started_at = NOW()
           WHERE id = :id AND status = 'queued'
           RETURNING user_id, tradingsymbol, budget_usd::float AS budget_usd, created_at""",
        {"id": run_id},
    )
    if not claimed:
        return
    row = claimed[0]
    cfg = _cfg()
    try:
        snapshot = _build_snapshot(row["user_id"], row["tradingsymbol"])
    except Exception as exc:
        logger.warning("Research run {}: snapshot failed: {}", run_id, exc)
        _finish(run_id, "failed", f"Could not build the data snapshot: {exc}")
        return

    models = {"analysts": cfg["quick"], "debate": cfg["quick"], "portfolio_manager": cfg["deep"]}
    execute_sql(
        """UPDATE research_runs SET snapshot = CAST(:snap AS JSONB), snapshot_schema_version = :v,
               data_as_of = :asof, price_at_run = :price, models = CAST(:models AS JSONB),
               prompt_hashes = CAST(:hashes AS JSONB)
           WHERE id = :id""",
        {
            "id": run_id,
            "snap": _jsonb(snapshot),
            "v": SNAPSHOT_SCHEMA_VERSION,
            "asof": snapshot["data_as_of"],
            "price": snapshot["data"]["price"]["last_close"]["value"],
            "models": _jsonb(models),
            "hashes": _jsonb(prompt_hashes()),
        },
    )

    # Leave room for one in-flight call to time out before the sweep's limit is reached
    created = row["created_at"]
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    limit = created + timedelta(minutes=cfg["timeout_min"])
    remaining = (limit - datetime.now(UTC)).total_seconds() - CALL_TIMEOUT_SEC - 20
    committee_cfg = CommitteeConfig(
        quick_model=cfg["quick"],
        deep_model=cfg["deep"],
        budget_usd=float(row["budget_usd"] or cfg["budget"]),
        deadline=time.monotonic() + remaining,
    )
    try:
        client = client or anthropic.Anthropic(
            timeout=anthropic.Timeout(CALL_TIMEOUT_SEC, connect=10.0), max_retries=2
        )
        out = run_committee(snapshot, client, committee_cfg, lambda o: _save(run_id, o))
    except Exception as exc:  # a bug, not an API outcome: keep the row from staying "running"
        logger.exception("Research run {} crashed: {}", run_id, exc)
        _finish(run_id, "failed", f"Internal error: {type(exc).__name__}")
        return
    _finish(run_id, out["status"], out.get("error"), out)
    logger.info(
        "Research run {} for {}: {} (${})",
        run_id,
        row["tradingsymbol"],
        out["status"],
        out["cost_usd"],
    )


# ── Reading runs ──────────────────────────────────────────────


def list_runs(user_id: str, symbol: str | None = None, limit: int = 20) -> list[dict]:
    return execute_sql(
        f"""SELECT {SUMMARY_COLS} FROM research_runs
            WHERE user_id = :u AND (CAST(:sym AS TEXT) IS NULL OR tradingsymbol = :sym)
            ORDER BY created_at DESC LIMIT :n""",
        {"u": user_id, "sym": symbol, "n": limit},
    )


def get_run(user_id: str, run_id: int) -> dict | None:
    rows = execute_sql(
        f"""SELECT {SUMMARY_COLS}, models, prompt_hashes, snapshot_schema_version, snapshot,
                   stages, decision, grounding, calls, price_at_run::float AS price_at_run,
                   fwd_return_5d::float AS fwd_return_5d, fwd_return_20d::float AS fwd_return_20d,
                   fwd_return_60d::float AS fwd_return_60d, alpha_5d::float AS alpha_5d,
                   alpha_60d::float AS alpha_60d, started_at
            FROM research_runs WHERE user_id = :u AND id = :id""",
        {"u": user_id, "id": run_id},
    )
    return rows[0] if rows else None


def usage_today(user_id: str) -> dict:
    cfg = _cfg()
    rows = execute_sql(
        """SELECT COUNT(*) AS n FROM research_runs WHERE user_id = :u AND run_date = :d
             AND NOT (status IN ('failed', 'abandoned') AND cost_usd = 0)""",
        {"u": user_id, "d": _today()},
    )
    recent = execute_sql(
        """SELECT cost_usd::float AS c FROM research_runs
           WHERE user_id = :u AND status = 'completed' ORDER BY created_at DESC LIMIT 10""",
        {"u": user_id},
    )
    return {
        "runs_today": int(rows[0]["n"]),
        "daily_limit": cfg["daily_limit"],
        "budget_usd": cfg["budget"],
        "models": {"quick": cfg["quick"], "deep": cfg["deep"]},
        # Measured from past runs; None until one has completed
        "estimated_cost_usd": round(sum(r["c"] for r in recent) / len(recent), 4)
        if recent
        else None,
        "estimate_runs": len(recent),
    }


# ── Settlement and scorecard ──────────────────────────────────

SIGN = {"Buy": 1, "Overweight": 1, "Hold": 0, "Underweight": -1, "Sell": -1}


def settle_research_runs() -> dict[str, int]:
    """Forward 5/20/60-day return and alpha against NIFTY 50 TRI, from the snapshot's last bar."""
    from src.analytics.historical_cache import get_or_sync_historical_bars
    from src.analytics.signal_recorder import forward_return

    settled = {"5d": 0, "20d": 0, "60d": 0}
    try:
        bench = get_or_sync_historical_bars("NIFTY 50 TRI")
    except Exception as exc:
        logger.warning("Benchmark bars unavailable, research settlement deferred: {}", exc)
        return settled
    pending = execute_sql(
        """SELECT id, tradingsymbol, data_as_of, fwd_return_5d, fwd_return_20d, fwd_return_60d
           FROM research_runs WHERE status = 'completed' AND data_as_of IS NOT NULL
             AND (fwd_return_5d IS NULL OR fwd_return_20d IS NULL OR fwd_return_60d IS NULL)"""
    )
    for run in pending:
        try:
            bars = get_or_sync_historical_bars(run["tradingsymbol"])
        except Exception as exc:
            logger.warning("Research settlement: no bars for {}: {}", run["tradingsymbol"], exc)
            continue
        for h in (5, 20, 60):
            if run[f"fwd_return_{h}d"] is not None:
                continue
            stock = forward_return(bars, run["data_as_of"], h)
            index = forward_return(bench, run["data_as_of"], h)
            if stock is None or index is None:
                continue
            execute_sql(
                f"UPDATE research_runs SET fwd_return_{h}d = :r, alpha_{h}d = :a WHERE id = :id",
                {"id": run["id"], "r": round(stock, 6), "a": round(stock - index, 6)},
            )
            settled[f"{h}d"] += 1
    logger.info("Research settlement: {}", settled)
    return settled


def scorecard(user_id: str) -> dict:
    """Average 20-day alpha by rating, and whether signed conviction ranks forward alpha."""
    from src.analytics.indicator_evaluator import compute_spearman_ic

    rows = execute_sql(
        """SELECT decision->>'rating' AS rating, (decision->>'conviction')::int AS conviction,
                  alpha_20d::float AS alpha
           FROM research_runs
           WHERE user_id = :u AND status = 'completed' AND alpha_20d IS NOT NULL""",
        {"u": user_id},
    )
    df = pd.DataFrame(rows, columns=["rating", "conviction", "alpha"])
    by_rating = {
        rating: {"n": int(len(g)), "avg_alpha_20d_pct": round(float(g["alpha"].mean()) * 100, 2)}
        for rating, g in df.groupby("rating")
    }
    ic = {"value": None, "p_value": None, "n_obs": len(df), "status": "insufficient_data"}
    if len(df) >= MIN_SCORED:
        signed = df["rating"].map(SIGN).fillna(0) * df["conviction"]
        value, p = compute_spearman_ic(signed, df["alpha"])
        ic.update(value=round(value, 3), p_value=round(p, 4), status="ok")
    return {"settled_runs": len(df), "by_rating": by_rating, "conviction_ic_20d": ic}
