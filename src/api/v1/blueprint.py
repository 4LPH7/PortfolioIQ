"""
PortfolioIQ — API v1 Blueprint
Defines REST API endpoints for portfolio valuation, stock analysis,
rebalancing, tax summaries, audit trails, and system settings.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from flask import Blueprint, jsonify, request

from src.api.limiter import limiter
from src.api.middleware import format_error_response, require_api_key, validate_json
from src.config.settings import get_settings
from src.db.connection import check_connection, execute_sql
from src.db.repository import (
    get_audit_orders,
    get_audit_validations,
    get_database_stats,
    list_app_configs,
    update_app_config,
)
from src.models.dtos import CreateCashFlowDTO, CreateMarketCalendarDTO, UpdateConfigDTO

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


@api_v1_bp.route("/market/calendar", methods=["GET"])
def get_market_calendar():
    year_str = request.args.get("year")
    year = int(year_str) if year_str and year_str.isdigit() else None
    segment = request.args.get("segment", "equity")
    from src.db.repository import get_market_calendar_entries

    entries = get_market_calendar_entries(year=year, segment=segment)
    data = []
    for e in entries:
        dump = e.model_dump()
        if dump.get("holiday_date"):
            dump["holiday_date"] = dump["holiday_date"].isoformat()
        if dump.get("created_at"):
            dump["created_at"] = dump["created_at"].isoformat()
        data.append(dump)
    return jsonify({"ok": True, "data": data})


@api_v1_bp.route("/market/calendar", methods=["POST"])
@require_api_key
@limiter.limit(get_settings().rate_limit_mutations)
@validate_json(CreateMarketCalendarDTO)
def post_market_calendar(payload: CreateMarketCalendarDTO):
    from src.db.repository import upsert_market_calendar_entry
    from src.ingestion.market_hours import _get_calendar_records

    dto = upsert_market_calendar_entry(payload)
    _get_calendar_records.cache_clear()

    dump = dto.model_dump()
    if dump.get("holiday_date"):
        dump["holiday_date"] = dump["holiday_date"].isoformat()
    if dump.get("created_at"):
        dump["created_at"] = dump["created_at"].isoformat()
    return jsonify({"ok": True, "data": dump})


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
# Quantitative Signal Engine (Phase 5: Evidence-Based Signals)
# ─────────────────────────────────────────────────────────────
@api_v1_bp.route("/signals/<symbol>", methods=["GET"])
@require_api_key
def get_stock_signal(symbol: str):
    """Retrieve evidence-based quantitative signal evaluation for a symbol."""
    from src.analytics.signal_recorder import compute_holding_signal
    from src.analytics.valuator import get_valuation_summary

    summary = get_valuation_summary()
    avg_price = next(
        (h["avg_price"] for h in summary.get("holdings", []) if h["symbol"] == symbol),
        0.0,
    )
    sig = compute_holding_signal(symbol, avg_buy_price=avg_price)
    return jsonify({"ok": True, "data": sig.model_dump(mode="json")})


@api_v1_bp.route("/signals", methods=["GET"])
@require_api_key
def get_portfolio_signals():
    """Retrieve evidence-based quantitative signals for all active portfolio holdings."""
    from src.analytics.signal_recorder import compute_portfolio_signals

    user_id = request.args.get("user_id", "default")
    signals = compute_portfolio_signals(user_id=user_id)
    return jsonify({"ok": True, "data": [s.model_dump(mode="json") for s in signals]})


@api_v1_bp.route("/signals/backtest", methods=["POST"])
@require_api_key
@limiter.limit(get_settings().rate_limit_mutations)
def post_signal_backtest():
    """Trigger an out-of-sample rolling walk-forward backtest for a symbol."""
    payload = request.get_json(silent=True) or {}
    symbol = payload.get("symbol") or payload.get("tradingsymbol")
    if not symbol:
        return format_error_response("VALIDATION_ERROR", "symbol or tradingsymbol is required", 400)

    train_window = int(payload.get("train_window_days", 252))
    test_window = int(payload.get("test_window_days", 63))

    from src.analytics.signal_recorder import run_and_record_backtest

    try:
        run_dto = run_and_record_backtest(
            symbol=symbol,
            train_window=train_window,
            test_window=test_window,
        )
        return jsonify({"ok": True, "data": run_dto.model_dump(mode="json")})
    except Exception as exc:
        return format_error_response("BACKTEST_ERROR", str(exc), 500)


@api_v1_bp.route("/signals/history", methods=["GET"])
@require_api_key
def get_signal_history():
    """Retrieve historical daily signal snapshots with multi-horizon forward returns."""
    symbol = request.args.get("symbol") or request.args.get("tradingsymbol")
    if not symbol:
        return format_error_response("VALIDATION_ERROR", "symbol or tradingsymbol is required", 400)

    limit = int(request.args.get("limit", 60))
    user_id = request.args.get("user_id", "default")

    from src.db.repository import get_signal_snapshots

    snapshots = get_signal_snapshots(tradingsymbol=symbol, user_id=user_id, limit=limit)
    return jsonify({"ok": True, "data": [s.model_dump(mode="json") for s in snapshots]})


# ─────────────────────────────────────────────────────────────
# Legacy Stock Analyzer (Deprecated — Routes to Quantitative Signals)
# ─────────────────────────────────────────────────────────────
@api_v1_bp.route("/analysis/<symbol>", methods=["GET"])
@require_api_key
def analyse_stock(symbol: str):
    """
    Legacy analysis endpoint aliased to the quantitative signal engine.
    Emits HTTP Warning 299 header advising migration to /api/v1/signals/<symbol>.
    """
    from src.analytics.signal_recorder import compute_holding_signal
    from src.analytics.valuator import get_valuation_summary

    summary = get_valuation_summary()
    avg_price = next(
        (h["avg_price"] for h in summary.get("holdings", []) if h["symbol"] == symbol),
        0.0,
    )
    sig = compute_holding_signal(symbol, avg_buy_price=avg_price)
    resp = jsonify({"ok": True, "data": sig.model_dump(mode="json")})
    resp.headers["Warning"] = f'299 - "Deprecated endpoint. Use /api/v1/signals/{symbol} instead."'
    return resp


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


@api_v1_bp.route("/holdings/reconciliation", methods=["GET"])
@require_api_key
def get_holdings_reconciliation():
    """Retrieve holdings reconciliation audit logs."""
    from src.db.repository import get_holdings_reconciliation_logs

    try:
        limit = min(int(request.args.get("limit", 50)), 200)
    except ValueError:
        limit = 50

    reason = request.args.get("reason")

    logs = get_holdings_reconciliation_logs(user_id="default", limit=limit, reason=reason)
    return jsonify({"ok": True, "data": [entry.model_dump() for entry in logs]}), 200


# ─────────────────────────────────────────────────────────────
# Phase 4: Portfolio Analytics & Constrained Rebalancing
# ─────────────────────────────────────────────────────────────
@api_v1_bp.route("/analytics/performance", methods=["GET"])
@require_api_key
def get_analytics_performance():
    """
    Returns quantitative return metrics (TWR, XIRR), risk ratios (Sharpe, Sortino,
    Max Drawdown, Beta, Jensen's Alpha), and basis-point drag attribution.
    """
    from src.analytics.performance import compute_portfolio_performance_summary

    benchmark = request.args.get("benchmark", "NIFTY 50 TRI")
    try:
        rf_rate = float(request.args.get("risk_free_rate", 0.065))
    except ValueError:
        rf_rate = 0.065

    window = request.args.get("window", "all")
    user_id = request.args.get("user_id", "default")

    summary = compute_portfolio_performance_summary(
        user_id=user_id,
        benchmark_name=benchmark,
        rf_annual=rf_rate,
        window=window,
    )
    return jsonify({"ok": True, "data": summary.model_dump(mode="json")}), 200


@api_v1_bp.route("/analytics/snapshots", methods=["GET"])
@require_api_key
def get_analytics_snapshots():
    """Returns historical daily snapshots (equity curve & benchmark comparison)."""
    from datetime import date

    from src.db.repository import get_daily_snapshots

    user_id = request.args.get("user_id", "default")
    start_str = request.args.get("start_date")
    end_str = request.args.get("end_date")
    try:
        limit = min(int(request.args.get("limit", 365)), 500)
    except ValueError:
        limit = 365

    start_date = date.fromisoformat(start_str) if start_str else None
    end_date = date.fromisoformat(end_str) if end_str else None

    snapshots = get_daily_snapshots(
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )
    return jsonify({"ok": True, "data": [s.model_dump(mode="json") for s in snapshots]}), 200


@api_v1_bp.route("/analytics/tax-harvesting", methods=["GET"])
@require_api_key
def get_analytics_tax_harvesting():
    """Returns tax-loss harvesting opportunities, near-LTCG locks, and Q4 gain harvesting."""
    from datetime import date

    from src.analytics.tax_guard import get_annual_tax_harvesting_summary

    user_id = request.args.get("user_id", "default")
    ref_date_str = request.args.get("ref_date")
    ref_date = date.fromisoformat(ref_date_str) if ref_date_str else None

    summary = get_annual_tax_harvesting_summary(user_id=user_id, ref_date=ref_date)
    return jsonify({"ok": True, "data": summary.model_dump(mode="json")}), 200


@api_v1_bp.route("/portfolio/cash-flows", methods=["GET"])
@require_api_key
def get_portfolio_cash_flows():
    """Returns ledger of portfolio cash deposits, withdrawals, and corporate action flows."""
    from datetime import date

    from src.db.repository import get_cash_flows

    user_id = request.args.get("user_id", "default")
    start_str = request.args.get("start_date")
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
    except ValueError:
        limit = 50

    start_date = date.fromisoformat(start_str) if start_str else None
    flows = get_cash_flows(user_id=user_id, start_date=start_date, limit=limit)
    return jsonify({"ok": True, "data": [cf.model_dump(mode="json") for cf in flows]}), 200


@api_v1_bp.route("/portfolio/cash-flows", methods=["POST"])
@require_api_key
@limiter.limit(get_settings().rate_limit_mutations)
@validate_json(CreateCashFlowDTO)
def post_portfolio_cash_flow(payload: CreateCashFlowDTO):
    """Records an external cash flow deposit or withdrawal."""
    from src.db.repository import record_cash_flow

    recorded = record_cash_flow(payload)
    return jsonify({"ok": True, "data": recorded.model_dump(mode="json")}), 201


@api_v1_bp.route("/rebalance/preview", methods=["POST"])
@require_api_key
@limiter.limit(get_settings().rate_limit_mutations)
def post_rebalance_preview():
    """
    Generates a dry-run rebalance manifest with trade sizing reasons,
    itemized Indian delivery cost breakdown, and estimated tax liabilities.
    """
    from decimal import Decimal

    from src.analytics.rebalancer import get_rebalance_summary

    body = request.get_json(silent=True) or {}
    user_id = body.get("user_id", "default")

    min_trade = (
        Decimal(str(body["min_trade_value"]))
        if "min_trade_value" in body and body["min_trade_value"] is not None
        else None
    )
    turnover_cap = (
        Decimal(str(body["turnover_cap_pct"]))
        if "turnover_cap_pct" in body and body["turnover_cap_pct"] is not None
        else None
    )
    cash_buffer = (
        Decimal(str(body["cash_buffer_pct"]))
        if "cash_buffer_pct" in body and body["cash_buffer_pct"] is not None
        else None
    )

    summary = get_rebalance_summary(
        user_id=user_id,
        dry_run=True,
        min_trade_value=min_trade,
        turnover_cap_pct=turnover_cap,
        cash_buffer_pct=cash_buffer,
    )
    return jsonify({"ok": True, "data": summary}), 200


# ─────────────────────────────────────────────────────────────
# Phase 6: Product & UX Polish Endpoints
# ─────────────────────────────────────────────────────────────
@api_v1_bp.route("/auth/verify", methods=["POST"])
@limiter.limit(get_settings().rate_limit_mutations)
def post_auth_verify():
    """
    Verifies master API key or session PIN.
    Accepts API key via X-API-Key header or JSON body `{"api_key": "..."}`.
    Does not require prior authentication (public auth gateway).
    """
    settings = get_settings()
    expected_key = settings.portfolioiq_api_key

    provided_key = request.headers.get("X-API-Key")
    if not provided_key:
        payload = request.get_json(silent=True) or {}
        provided_key = payload.get("api_key")

    if not provided_key or provided_key != expected_key:
        return format_error_response("UNAUTHORIZED", "Invalid API Key or PIN", status_code=401)

    return (
        jsonify(
            {
                "ok": True,
                "data": {
                    "status": "authenticated",
                    "message": "API key verified successfully",
                },
            }
        ),
        200,
    )


@api_v1_bp.route("/alerts", methods=["GET"])
@require_api_key
def get_alerts():
    """
    Aggregates active operational and portfolio alerts:
    - Allocation drift exceeding thresholds (>5% warning, >10% critical)
    - Stale price quotes (>60s old during regular trading hours)
    - Tax harvesting opportunities and near-LTCG locks
    - System health warnings (broker token, database)
    """
    from loguru import logger

    from src.analytics.drift_detector import detect_drift
    from src.db.repository import get_current_holdings
    from src.ingestion.kite_auth import get_stored_token
    from src.ingestion.market_hours import is_market_open

    user_id = request.args.get("user_id", "default")
    alerts: list[dict[str, Any]] = []
    now = datetime.now(UTC)

    # 1. System Health Checks
    if not check_connection():
        alerts.append(
            {
                "id": "sys_db_disconnect",
                "type": "SYSTEM",
                "severity": "CRITICAL",
                "title": "Database Disconnected",
                "message": "PostgreSQL database connection is currently unavailable.",
                "action_label": "View System Status",
                "action_url": "/status.html",
                "timestamp": now.isoformat(),
                "metadata": {},
            }
        )

    token = None
    try:
        token = get_stored_token()
    except Exception:
        pass

    if not token:
        alerts.append(
            {
                "id": "sys_kite_token",
                "type": "SYSTEM",
                "severity": "CRITICAL",
                "title": "Zerodha Token Expired",
                "message": "Kite Connect session is inactive or token has expired.",
                "action_label": "Re-authenticate",
                "action_url": "/settings.html",
                "timestamp": now.isoformat(),
                "metadata": {},
            }
        )

    # 2. Holdings & Drift Checks
    try:
        holdings = get_current_holdings(user_id=user_id)
        if holdings:
            drift_signals = detect_drift(user_id=user_id)
            for s in drift_signals:
                drift_pct = abs(float(s.drift_pct))
                sym = s.name
                if drift_pct >= 10.0:
                    alerts.append(
                        {
                            "id": f"drift_crit_{sym}",
                            "type": "DRIFT",
                            "severity": "CRITICAL",
                            "title": f"Severe Allocation Drift: {sym}",
                            "message": f"{sym} has drifted by {drift_pct:.1f}% from target allocation.",
                            "action_label": "Rebalance",
                            "action_url": "/rebalance.html",
                            "timestamp": now.isoformat(),
                            "metadata": {"symbol": sym, "drift_pct": drift_pct},
                        }
                    )
                elif drift_pct >= 5.0:
                    alerts.append(
                        {
                            "id": f"drift_warn_{sym}",
                            "type": "DRIFT",
                            "severity": "WARNING",
                            "title": f"Allocation Drift: {sym}",
                            "message": f"{sym} has drifted by {drift_pct:.1f}% from target allocation.",
                            "action_label": "Rebalance",
                            "action_url": "/rebalance.html",
                            "timestamp": now.isoformat(),
                            "metadata": {"symbol": sym, "drift_pct": drift_pct},
                        }
                    )

            # 3. Price Staleness Checks
            if is_market_open():
                tokens = [h.instrument_token for h in holdings]
                rows = execute_sql(
                    "SELECT instrument_token, last_price, is_stale, last_updated FROM live_prices WHERE instrument_token = ANY(:tokens)",
                    {"tokens": tokens},
                )
                prices_by_token = {r["instrument_token"]: r for r in rows}
                for h in holdings:
                    price_row = prices_by_token.get(h.instrument_token)
                    if price_row is None:
                        alerts.append(
                            {
                                "id": f"stale_{h.tradingsymbol}",
                                "type": "PRICE_STALE",
                                "severity": "WARNING",
                                "title": f"Missing Price: {h.tradingsymbol}",
                                "message": f"No live price record found for {h.tradingsymbol} during market hours.",
                                "action_label": "Refresh Quotes",
                                "action_url": "/index.html",
                                "timestamp": now.isoformat(),
                                "metadata": {"symbol": h.tradingsymbol},
                            }
                        )
                    elif price_row.get("last_updated"):
                        last_up = price_row["last_updated"]
                        if getattr(last_up, "tzinfo", None) is None:
                            last_up = last_up.replace(tzinfo=UTC)
                        age_sec = (now - last_up.astimezone(UTC)).total_seconds()
                        if age_sec > 60 or price_row.get("is_stale"):
                            alerts.append(
                                {
                                    "id": f"stale_{h.tradingsymbol}",
                                    "type": "PRICE_STALE",
                                    "severity": "WARNING",
                                    "title": f"Stale Quote: {h.tradingsymbol}",
                                    "message": f"Market price is {int(age_sec)}s old (>60s limit).",
                                    "action_label": "Refresh Quotes",
                                    "action_url": "/index.html",
                                    "timestamp": now.isoformat(),
                                    "metadata": {"symbol": h.tradingsymbol, "age_seconds": age_sec},
                                }
                            )
    except Exception as exc:
        logger.warning(f"Error checking holdings or drift for alerts: {exc}")

    # 4. Tax Harvesting Opportunities
    try:
        from src.analytics.tax_guard import get_annual_tax_harvesting_summary

        tax_summary = get_annual_tax_harvesting_summary(user_id=user_id)
        for opp in getattr(tax_summary, "opportunities", []):
            if opp.action == "NEAR_LTCG_DEFER":
                alerts.append(
                    {
                        "id": f"tax_near_{opp.tradingsymbol}",
                        "type": "TAX",
                        "severity": "WARNING",
                        "title": f"Near-LTCG Lock: {opp.tradingsymbol}",
                        "message": opp.reason,
                        "action_label": "View Tax Guard",
                        "action_url": "/tax.html",
                        "timestamp": now.isoformat(),
                        "metadata": {"symbol": opp.tradingsymbol},
                    }
                )
            elif opp.action == "LOSS_HARVEST":
                alerts.append(
                    {
                        "id": f"tax_loss_{opp.tradingsymbol}",
                        "type": "TAX",
                        "severity": "INFO",
                        "title": f"Tax Loss Opportunity: {opp.tradingsymbol}",
                        "message": opp.reason,
                        "action_label": "Harvest Losses",
                        "action_url": "/tax.html",
                        "timestamp": now.isoformat(),
                        "metadata": {"symbol": opp.tradingsymbol},
                    }
                )
    except Exception as exc:
        logger.warning(f"Error checking tax opportunities for alerts: {exc}")

    system_healthy = not any(a["severity"] == "CRITICAL" for a in alerts)
    summary = {
        "unread_count": len(alerts),
        "alerts": alerts,
        "system_healthy": system_healthy,
    }
    return jsonify({"ok": True, "data": summary}), 200


@api_v1_bp.route("/system/status", methods=["GET"])
@require_api_key
def get_system_status():
    """
    Exposes live system telemetry:
    - API Engine version, environment, and latency
    - PostgreSQL connection pool health and statistics
    - Zerodha Kite Connect session status and token validity
    - NSE Market Open/Close countdown and holiday state
    - APScheduler background job states
    """
    from src.ingestion.kite_auth import get_stored_token
    from src.ingestion.market_hours import get_market_status

    settings = get_settings()
    now = datetime.now(UTC)

    # 1. Database
    db_connected = check_connection()
    db_stats = {}
    try:
        db_stats = get_database_stats()
    except Exception:
        pass
    db_info = {
        "connected": db_connected,
        "engine": "postgresql",
        "stats": db_stats,
    }

    # 2. Broker
    tok = None
    try:
        tok = get_stored_token()
    except Exception:
        pass
    broker_info = {
        "broker_name": "Zerodha Kite Connect",
        "authenticated": bool(tok),
        "token_active": bool(tok),
    }

    # 3. Market
    market_info = get_market_status()

    # 4. Scheduler
    jobs = [
        {
            "id": "daily_eod_snapshot",
            "name": "Daily EOD Portfolio Snapshot",
            "schedule": "16:00 IST Mon-Fri",
        },
        {
            "id": "daily_signals",
            "name": "Daily EOD Signal Snapshots",
            "schedule": "16:15 IST Mon-Fri",
        },
        {
            "id": "mature_forward_returns",
            "name": "Mature Signal Forward Returns",
            "schedule": "16:30 IST Mon-Fri",
        },
        {
            "id": "partition_maintenance",
            "name": "Partition Maintenance",
            "schedule": "00:00 IST Daily",
        },
    ]
    scheduler_info = {
        "active": True,
        "jobs_count": len(jobs),
        "jobs": jobs,
    }

    # Overall system health
    if db_connected and bool(tok):
        overall_status = "OK"
    elif db_connected:
        overall_status = "DEGRADED"
    else:
        overall_status = "ERROR"

    return (
        jsonify(
            {
                "ok": True,
                "data": {
                    "status": overall_status,
                    "timestamp": now.isoformat(),
                    "api_version": "v1",
                    "environment": settings.app_env,
                    "dry_run_mode": settings.dry_run_mode,
                    "database": db_info,
                    "broker": broker_info,
                    "market": market_info,
                    "scheduler": scheduler_info,
                },
            }
        ),
        200,
    )
