"""
PortfolioIQ — API v1 Blueprint
Defines REST API endpoints for portfolio valuation, stock analysis,
rebalancing, tax summaries, audit trails, and system settings.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify, request

from src.api.limiter import limiter
from src.api.middleware import format_error_response, require_api_key, validate_json
from src.config.settings import get_settings
from src.db.connection import check_connection
from src.db.repository import (
    get_audit_orders,
    get_audit_validations,
    get_database_stats,
    list_app_configs,
    update_app_config,
)
from src.models.dtos import UpdateConfigDTO

api_v1_bp = Blueprint("api_v1", __name__)


# ─────────────────────────────────────────────────────────────
# Health & Status (Public Endpoints)
# ─────────────────────────────────────────────────────────────
@api_v1_bp.route("/health")
def health():
    """Health check endpoint consumed by Render health checks."""
    return jsonify({"status": "ok", "db": check_connection()})


@api_v1_bp.route("/market/status")
def market_status():
    """NSE market status endpoint for frontend sidebar."""
    from src.ingestion.market_hours import get_market_status

    return jsonify({"ok": True, "data": get_market_status()})


# ─────────────────────────────────────────────────────────────
# Portfolio Valuation
# ─────────────────────────────────────────────────────────────
@api_v1_bp.route("/portfolio/summary")
@require_api_key
def portfolio_summary():
    from src.analytics.valuator import get_valuation_summary

    data = get_valuation_summary()
    return jsonify({"ok": True, "data": data})


# ─────────────────────────────────────────────────────────────
# Stock Analyzer
# ─────────────────────────────────────────────────────────────
@api_v1_bp.route("/analysis/<symbol>")
@require_api_key
def analyse_stock(symbol: str):
    from src.analytics.predictor import analyse_holding
    from src.analytics.valuator import get_valuation_summary

    summary = get_valuation_summary()
    avg_price = next(
        (h["avg_price"] for h in summary.get("holdings", []) if h["symbol"] == symbol),
        0.0,
    )
    result = analyse_holding(symbol, avg_buy_price=avg_price)

    if result.error:
        return format_error_response(
            code="NOT_FOUND",
            message=result.error,
            status_code=404,
        )

    def _safe(obj: Any) -> dict[str, Any] | None:
        if obj is None:
            return None
        d = obj.__dict__.copy()
        d.pop("ohlcv", None)  # strip DataFrame — not JSON serialisable
        return d

    payload = {
        "symbol": result.symbol,
        "yf_ticker": result.yf_ticker,
        "current_price": result.current_price,
        "avg_buy_price": result.avg_buy_price,
        "data_start": result.data_start,
        "data_end": result.data_end,
        "data_points": result.data_points,
        "rsi": _safe(result.rsi),
        "macd": _safe(result.macd),
        "bollinger": _safe(result.bollinger),
        "linear_regression": _safe(result.linear_regression),
        "monte_carlo": _safe(result.monte_carlo),
        "composite": _safe(result.composite),
    }
    return jsonify({"ok": True, "data": payload})


@api_v1_bp.route("/holdings/symbols")
@require_api_key
def holdings_symbols():
    """Return list of current holding symbols."""
    from src.analytics.valuator import get_valuation_summary

    summary = get_valuation_summary()
    symbols = [
        {"symbol": h["symbol"], "avg_price": h["avg_price"]} for h in summary.get("holdings", [])
    ]
    return jsonify({"ok": True, "data": symbols})


# ─────────────────────────────────────────────────────────────
# Rebalancing & Drift
# ─────────────────────────────────────────────────────────────
@api_v1_bp.route("/rebalance/drift")
@require_api_key
def rebalance_drift():
    from src.analytics.drift_detector import get_drift_summary

    data = get_drift_summary()
    return jsonify({"ok": True, "data": data})


@api_v1_bp.route("/rebalance/orders")
@require_api_key
def rebalance_orders():
    from src.analytics.rebalancer import get_rebalance_summary

    data = get_rebalance_summary()
    return jsonify({"ok": True, "data": data})


# ─────────────────────────────────────────────────────────────
# Tax Guard
# ─────────────────────────────────────────────────────────────
@api_v1_bp.route("/tax/summary")
@require_api_key
def tax_summary():
    from src.analytics.tax_guard import get_tax_summary

    data = get_tax_summary()
    return jsonify({"ok": True, "data": data})


# ─────────────────────────────────────────────────────────────
# Audit Logs
# ─────────────────────────────────────────────────────────────
@api_v1_bp.route("/audit/orders")
@require_api_key
def audit_orders():
    status = request.args.get("status", "ALL")
    side = request.args.get("side", "ALL")
    try:
        limit = int(request.args.get("limit", 50))
    except ValueError:
        limit = 50

    rows = get_audit_orders(status=status, side=side, limit=limit)
    return jsonify({"ok": True, "data": rows})


@api_v1_bp.route("/audit/validations")
@require_api_key
def audit_validations():
    try:
        limit = int(request.args.get("limit", 50))
    except ValueError:
        limit = 50

    rows = get_audit_validations(limit=limit)
    return jsonify({"ok": True, "data": rows})


# ─────────────────────────────────────────────────────────────
# Configuration & Management
# ─────────────────────────────────────────────────────────────
@api_v1_bp.route("/settings/config", methods=["GET"])
@require_api_key
def get_config():
    configs = list_app_configs()
    data = [
        {
            "key": c.key,
            "value": c.value,
            "value_type": c.value_type,
            "description": c.description,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        }
        for c in configs
    ]
    return jsonify({"ok": True, "data": data})


@api_v1_bp.route("/settings/config", methods=["PATCH"])
@require_api_key
@limiter.limit(get_settings().rate_limit_mutations)
@validate_json(UpdateConfigDTO)
def update_config(payload: UpdateConfigDTO):
    try:
        update_app_config(payload.key, payload.value)
        return jsonify({"ok": True})
    except KeyError as exc:
        return format_error_response(
            code="NOT_FOUND",
            message=str(exc),
            status_code=404,
        )


@api_v1_bp.route("/settings/sync", methods=["POST"])
@require_api_key
@limiter.limit(get_settings().rate_limit_mutations)
def manual_sync():
    from src.ingestion.kite_sync import run_start_of_day_sync

    result = run_start_of_day_sync()
    return jsonify({"ok": True, "data": result})


@api_v1_bp.route("/settings/db-stats")
@require_api_key
def db_stats():
    data = get_database_stats()
    return jsonify({"ok": True, "data": data})
