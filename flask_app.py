"""
PortfolioIQ — Flask REST API
Run: python flask_app.py
Serves all data endpoints consumed by the static Netlify frontend.
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from flask import Flask, jsonify, request
from flask_cors import CORS
from loguru import logger

app = Flask(__name__)
CORS(app, origins="*")   # allow Netlify frontend + localhost dev


# ─────────────────────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────────────────────
@app.route("/api/health")
def health():
    from src.db.connection import check_connection
    return jsonify({"status": "ok", "db": check_connection()})


# ─────────────────────────────────────────────────────────────
# Portfolio
# ─────────────────────────────────────────────────────────────
@app.route("/api/portfolio/summary")
def portfolio_summary():
    try:
        from src.analytics.valuator import get_valuation_summary
        data = get_valuation_summary()
        return jsonify({"ok": True, "data": data})
    except Exception as exc:
        logger.error("portfolio/summary error: {}", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────
# Stock Analyzer
# ─────────────────────────────────────────────────────────────
@app.route("/api/analysis/<symbol>")
def analyse_stock(symbol: str):
    try:
        from src.analytics.valuator import get_valuation_summary
        from src.analytics.predictor import analyse_holding
        summary = get_valuation_summary()
        avg_price = next(
            (h["avg_price"] for h in summary.get("holdings", []) if h["symbol"] == symbol),
            0.0,
        )
        result = analyse_holding(symbol, avg_buy_price=avg_price)

        if result.error:
            return jsonify({"ok": False, "error": result.error}), 404

        def _safe(obj):
            if obj is None:
                return None
            d = obj.__dict__.copy()
            d.pop("ohlcv", None)          # strip DataFrame — not JSON serialisable
            return d

        payload = {
            "symbol":        result.symbol,
            "yf_ticker":     result.yf_ticker,
            "current_price": result.current_price,
            "avg_buy_price": result.avg_buy_price,
            "data_start":    result.data_start,
            "data_end":      result.data_end,
            "data_points":   result.data_points,
            "rsi":            _safe(result.rsi),
            "macd":           _safe(result.macd),
            "bollinger":      _safe(result.bollinger),
            "linear_regression": _safe(result.linear_regression),
            "monte_carlo":    _safe(result.monte_carlo),
            "composite":      _safe(result.composite),
        }
        return jsonify({"ok": True, "data": payload})
    except Exception as exc:
        logger.exception("analysis/{} error", symbol)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/holdings/symbols")
def holdings_symbols():
    """Return list of current holding symbols."""
    try:
        from src.analytics.valuator import get_valuation_summary
        summary = get_valuation_summary()
        symbols = [{"symbol": h["symbol"], "avg_price": h["avg_price"]}
                   for h in summary.get("holdings", [])]
        return jsonify({"ok": True, "data": symbols})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────
# Rebalance
# ─────────────────────────────────────────────────────────────
@app.route("/api/rebalance/drift")
def rebalance_drift():
    try:
        from src.analytics.drift_detector import get_drift_summary
        data = get_drift_summary()
        return jsonify({"ok": True, "data": data})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/rebalance/orders")
def rebalance_orders():
    try:
        from src.analytics.rebalancer import get_rebalance_summary
        data = get_rebalance_summary()
        return jsonify({"ok": True, "data": data})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────
# Tax Guard
# ─────────────────────────────────────────────────────────────
@app.route("/api/tax/summary")
def tax_summary():
    try:
        from src.analytics.tax_guard import get_tax_summary
        data = get_tax_summary()
        return jsonify({"ok": True, "data": data})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────
# Audit Log
# ─────────────────────────────────────────────────────────────
@app.route("/api/audit/orders")
def audit_orders():
    try:
        from src.db.connection import execute_sql
        status = request.args.get("status", "ALL")
        side   = request.args.get("side",   "ALL")
        limit  = int(request.args.get("limit", 50))

        where_parts, params = [], {"limit": limit}
        if status != "ALL":
            where_parts.append("status = :status")
            params["status"] = status
        if side != "ALL":
            where_parts.append("transaction_type = :side")
            params["side"] = side
        where_sql = "WHERE " + " AND ".join(where_parts) if where_parts else ""

        rows = execute_sql(f"""
            SELECT id, kite_order_id, tradingsymbol, exchange,
                   transaction_type, quantity, price, status,
                   trigger_source, reason, error_message, is_dry_run,
                   placed_at::text
            FROM order_audit_trail {where_sql}
            ORDER BY placed_at DESC LIMIT :limit
        """, params)
        return jsonify({"ok": True, "data": rows})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/audit/validations")
def audit_validations():
    try:
        from src.db.connection import execute_sql
        limit = int(request.args.get("limit", 50))
        rows = execute_sql("""
            SELECT id, tradingsymbol, transaction_type, quantity, price,
                   validation_result, failure_reason, checks_passed,
                   checks_failed, validated_at::text
            FROM order_validation_log
            ORDER BY validated_at DESC LIMIT :limit
        """, {"limit": limit})
        return jsonify({"ok": True, "data": rows})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────
# Settings
# ─────────────────────────────────────────────────────────────
@app.route("/api/settings/config")
def get_config():
    try:
        from src.db.connection import execute_sql
        rows = execute_sql("SELECT key, value, description FROM system_config ORDER BY key")
        return jsonify({"ok": True, "data": rows})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/settings/config", methods=["PATCH"])
def update_config():
    try:
        from src.db.connection import get_db_session
        from sqlalchemy import text
        body = request.get_json()
        key, val = body.get("key"), body.get("value")
        if not key:
            return jsonify({"ok": False, "error": "key required"}), 400
        with get_db_session() as s:
            s.execute(text("UPDATE system_config SET value=:v WHERE key=:k"), {"v": val, "k": key})
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/settings/sync", methods=["POST"])
def manual_sync():
    try:
        from src.ingestion.kite_sync import run_start_of_day_sync
        result = run_start_of_day_sync()
        return jsonify({"ok": True, "data": result})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/settings/db-stats")
def db_stats():
    try:
        from src.db.connection import execute_sql
        rows = execute_sql("""
            SELECT
                (SELECT count(*) FROM instrument_master WHERE is_active=TRUE)  AS instruments,
                (SELECT count(*) FROM user_holdings WHERE user_id='default')   AS holdings,
                (SELECT count(*) FROM live_prices)                             AS live_prices,
                (SELECT count(*) FROM price_history)                           AS price_history,
                (SELECT count(*) FROM order_audit_trail)                       AS audit_entries,
                (SELECT count(*) FROM order_validation_log)                    AS validation_entries,
                (SELECT count(*) FROM holding_tax_lots WHERE remaining_quantity>0) AS tax_lots,
                (SELECT count(*) FROM market_calendar)                         AS holidays
        """)
        return jsonify({"ok": True, "data": rows[0] if rows else {}})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/market/status")
def market_status():
    try:
        from src.ingestion.market_hours import get_market_status
        return jsonify({"ok": True, "data": get_market_status()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


if __name__ == "__main__":
    logger.info("PortfolioIQ Flask API starting on http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=True)
