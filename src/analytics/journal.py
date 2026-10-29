"""
Decision journal: what you took or skipped, and how it turned out against the index.

Each entry captures the scanner rank and status, the last committee rating and the price at the
time, server-side, so a later review judges the decision against what was known then. Settlement
uses the same forward-return maths as signal tracking (adjusted closes, NIFTY 50 TRI). A summary is
only given once there are 15 settled decisions; before that it says there is not enough data.

Information only: a journal entry never places, sizes or blocks an order.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from src.analytics.signal_recorder import forward_return
from src.db.connection import execute_sql

ACTIONS = ("took", "skipped", "watching")
MAX_REASON = 500
HORIZON = 20
MIN_SETTLED = 15
TOP_N = 100
BENCHMARK = "NIFTY 50 TRI"
COLS = """id, tradingsymbol, action, side, reason, context, price_at_decision, decided_on,
    fwd_return_20d::float AS fwd_return_20d, alpha_20d::float AS alpha_20d, settled_at, created_at"""


def check_entry(action: str, reason: str) -> tuple[str, str]:
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {', '.join(ACTIONS)}")
    reason = (reason or "").strip()
    if len(reason) > MAX_REASON:
        raise ValueError(f"reason must be at most {MAX_REASON} characters")
    return action, reason


def _scanner_context(symbol: str) -> dict | None:
    from src.analytics.scanner import rank_lookup

    hit = rank_lookup().get(symbol)
    return {"rank": hit["rank"], "of": hit["of"], "status": hit["status"]} if hit else None


def _committee_context(symbol: str) -> dict | None:
    rows = execute_sql(
        """SELECT id, decision->>'rating' AS rating, (decision->>'conviction')::int AS conviction
           FROM research_runs WHERE tradingsymbol = :s AND status = 'completed'
           ORDER BY created_at DESC LIMIT 1""",
        {"s": symbol},
    )
    if not rows:
        return None
    r = rows[0]
    return {"run_id": r["id"], "rating": r["rating"], "conviction": r["conviction"]}


def _price_now(symbol: str) -> float | None:
    rows = execute_sql(
        """SELECT lp.last_price::float AS p FROM live_prices lp
           JOIN instrument_master im USING (instrument_token) WHERE im.tradingsymbol = :s""",
        {"s": symbol},
    )
    return rows[0]["p"] if rows else None


def build_context(symbol: str, user_id: str = "default") -> dict:
    """What was known about this stock right now. Never taken from the client."""
    return {
        "scanner": _scanner_context(symbol),
        "committee": _committee_context(symbol),
        "price": _price_now(symbol),
    }


def record(
    user_id: str,
    symbol: str,
    action: str,
    side: str | None,
    reason: str,
    context: dict,
    price: float | None,
) -> dict:
    action, reason = check_entry(action, reason)
    rows = execute_sql(
        f"""INSERT INTO decision_journal
              (user_id, tradingsymbol, action, side, reason, context, price_at_decision)
            VALUES (:u, :s, :a, :side, :reason, CAST(:ctx AS JSONB), :price)
            RETURNING {COLS}""",
        {
            "u": user_id,
            "s": symbol,
            "a": action,
            "side": side,
            "reason": reason,
            "ctx": _json(context),
            "price": price,
        },
    )
    return rows[0]


def _json(value: dict) -> str:
    import json

    return json.dumps(value, sort_keys=True, default=str)


def list_entries(user_id: str, limit: int = 200) -> list[dict]:
    return execute_sql(
        f"SELECT {COLS} FROM decision_journal WHERE user_id = :u ORDER BY created_at DESC LIMIT :n",
        {"u": user_id, "n": limit},
    )


def _bars_for(symbol: str, lookback_days: int = 400):
    from src.analytics.historical_cache import get_or_sync_historical_bars

    return get_or_sync_historical_bars(symbol, lookback_days=lookback_days)


def settled_returns(stock_bars, index_bars, start: date) -> dict[str, float | None]:
    stock = forward_return(stock_bars, start, HORIZON)
    index = forward_return(index_bars, start, HORIZON)
    if stock is None or index is None:
        return {"fwd_return_20d": None, "alpha_20d": None}
    return {"fwd_return_20d": round(stock, 6), "alpha_20d": round(stock - index, 6)}


def settle(user_id: str) -> int:
    """Fill the 20-day return for entries whose horizon has passed. Returns how many settled."""
    pending = execute_sql(
        """SELECT id, tradingsymbol, decided_on FROM decision_journal
           WHERE user_id = :u AND settled_at IS NULL AND decided_on <= :cut""",
        {"u": user_id, "cut": date.today()},
    )
    if not pending:
        return 0
    index = _bars_for(BENCHMARK)
    done = 0
    for row in pending:
        try:
            got = settled_returns(_bars_for(row["tradingsymbol"]), index, row["decided_on"])
        except Exception:
            continue
        if got["alpha_20d"] is None:
            continue
        execute_sql(
            """UPDATE decision_journal SET fwd_return_20d = :r, alpha_20d = :a, settled_at = :now
               WHERE id = :id""",
            {
                "id": row["id"],
                "r": got["fwd_return_20d"],
                "a": got["alpha_20d"],
                "now": datetime.now(UTC),
            },
        )
        done += 1
    return done


def _avg_pct(values: list[float]) -> float | None:
    return round(sum(values) / len(values) * 100, 2) if values else None


def summarise(rows: list[dict]) -> dict:
    """Average 20-day alpha for took vs skipped, and by scanner rank. Needs MIN_SETTLED decisions."""
    decided = [
        r for r in rows if r["action"] in ("took", "skipped") and r.get("alpha_20d") is not None
    ]
    out = {"settled": len(decided), "min_settled": MIN_SETTLED}
    if len(decided) < MIN_SETTLED:
        out.update(
            status="insufficient_data",
            took_avg_alpha_20d_pct=None,
            skipped_avg_alpha_20d_pct=None,
            by_rank={},
        )
        return out

    def alphas(pred):
        return [r["alpha_20d"] for r in decided if pred(r)]

    top = alphas(lambda r: r.get("scanner_rank") is not None and r["scanner_rank"] <= TOP_N)
    rest = alphas(lambda r: r.get("scanner_rank") is not None and r["scanner_rank"] > TOP_N)
    out.update(
        status="ok",
        took_avg_alpha_20d_pct=_avg_pct(alphas(lambda r: r["action"] == "took")),
        skipped_avg_alpha_20d_pct=_avg_pct(alphas(lambda r: r["action"] == "skipped")),
        by_rank={
            "top_100": {"n": len(top), "avg_alpha_20d_pct": _avg_pct(top)},
            "outside_top_100": {"n": len(rest), "avg_alpha_20d_pct": _avg_pct(rest)},
        },
    )
    return out


def summary(user_id: str) -> dict:
    rows = execute_sql(
        """SELECT action, alpha_20d::float AS alpha_20d,
                   (context->'scanner'->>'rank')::int AS scanner_rank
            FROM decision_journal WHERE user_id = :u""",
        {"u": user_id},
    )
    return summarise(rows)
