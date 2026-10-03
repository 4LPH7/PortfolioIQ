"""
PortfolioIQ — API v1 Blueprint
Defines REST API endpoints for portfolio valuation, stock analysis,
rebalancing, tax summaries, audit trails, and system settings.
"""

from __future__ import annotations

import hmac
import os
from datetime import UTC, datetime
from typing import Any

from flask import Blueprint, jsonify, request
from loguru import logger

from src.api.limiter import limiter
from src.api.middleware import format_error_response, require_api_key, validate_json
from src.config.settings import get_settings
from src.db.connection import check_connection, execute_sql, get_db_session
from src.db.repository import (
    get_audit_orders,
    get_audit_validations,
    get_database_stats,
    list_app_configs,
    update_app_config,
)
from src.models.dtos import (
    CreateCashFlowDTO,
    CreateMarketCalendarDTO,
    CSVImportHoldingDTO,
    CSVImportResultDTO,
    UpdateConfigDTO,
)

api_v1_bp = Blueprint("api_v1", __name__)


# ─────────────────────────────────────────────────────────────
# Health & Status (Public Endpoints)
# ─────────────────────────────────────────────────────────────
@api_v1_bp.route("/health")
def health():
    """Health check endpoint for local and hosted backend monitoring."""
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

    live_refresh = None
    if request.args.get("refresh") == "1":
        try:
            from src.ingestion.kite_quote_poller import poll_kite_quotes_if_stale

            live_refresh = poll_kite_quotes_if_stale()
        except Exception as exc:
            # Keep the dashboard available with its last known values when Kite
            # is unavailable; the UI will still show their source/staleness.
            logger.warning("On-demand Kite quote refresh failed: {}", exc)
            live_refresh = {"status": "error"}

    data = get_valuation_summary()
    if live_refresh is not None:
        data["live_refresh"] = live_refresh
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
    Stock Analysis endpoint returning full 5-method quantitative models:
    RSI, MACD, Bollinger Bands, Linear Regression, and Monte Carlo simulation,
    plus walk-forward quantitative signal indicators and evidence hurdles.
    Emits HTTP Warning 299 header advising migration to /api/v1/signals/<symbol>.
    """
    import dataclasses

    from src.analytics.predictor import analyse_holding
    from src.analytics.signal_recorder import compute_holding_signal
    from src.analytics.valuator import get_valuation_summary

    summary = get_valuation_summary()
    avg_price = next(
        (h["avg_price"] for h in summary.get("holdings", []) if h["symbol"] == symbol),
        0.0,
    )

    # 1. Run 5-method technical models
    try:
        analysis = analyse_holding(symbol, avg_buy_price=avg_price)
        data = dataclasses.asdict(analysis)
        data.pop("ohlcv", None)
    except Exception as exc:
        logger.warning("analyse_holding failed for {}: {}", symbol, exc)
        return jsonify(
            {
                "ok": False,
                "error": {
                    "code": "ANALYSIS_DATA_UNAVAILABLE",
                    "message": "Real historical market data is unavailable. Connect Kite and retry.",
                },
            }
        ), 503

    # 2. Run / merge walk-forward signal engine
    sig_dto = None
    try:
        sig_dto = compute_holding_signal(symbol, avg_buy_price=avg_price)
    except Exception as exc:
        logger.warning("compute_holding_signal failed for {}: {}", symbol, exc)

    data["tradingsymbol"] = symbol
    if sig_dto:
        sig_dump = sig_dto.model_dump(mode="json")
        data["tradingsymbol"] = sig_dump.get("tradingsymbol", symbol)
        data["evidence_badge"] = sig_dump.get("evidence_badge", "")
        data["status"] = sig_dump.get("status", "PENDING")
        data["indicators"] = sig_dump.get("indicators", [])
        data["backtest_summary"] = sig_dump.get("backtest_summary")
        if not data.get("current_price") and sig_dto.current_price:
            data["current_price"] = sig_dto.current_price
        if not data.get("composite"):
            data["composite"] = {
                "score": sig_dto.composite_score,
                "signal": sig_dto.signal_label.replace("_", " "),
                "summary": f"Quantitative evidence score: {sig_dto.composite_score:.0f}/100 ({sig_dto.evidence_badge}).",
                "rsi_score": 50,
                "macd_score": 50,
                "bollinger_score": 50,
                "lr_score": 50,
                "monte_carlo_score": 50,
            }

    resp = jsonify({"ok": True, "data": data})
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
@api_v1_bp.route("/holdings/sync", methods=["POST"])
@require_api_key
@limiter.limit(get_settings().rate_limit_mutations)
def manual_sync():
    from src.ingestion.kite_sync import run_start_of_day_sync

    user_id = request.args.get("user_id", "default")
    replace_imported = (
        request.args.get("mode") == "live_only"
        or request.args.get("replace_imported") in ("1", "true", "yes")
        or (request.is_json and request.json and request.json.get("replace_imported"))
        or (request.is_json and request.json and request.json.get("mode") == "live_only")
    )
    result = run_start_of_day_sync(user_id=user_id, replace_imported=bool(replace_imported))
    try:
        from src.ingestion.kite_quote_poller import poll_kite_quotes

        poll_kite_quotes()
    except Exception as exc:
        logger.debug("Quote poller after sync: {}", exc)

    return jsonify({"ok": bool(result.get("ok")), "data": result}), (
        200 if result.get("ok") else 502
    )


@api_v1_bp.route("/holdings/purge-imported", methods=["POST"])
@require_api_key
@limiter.limit(get_settings().rate_limit_mutations)
def purge_imported():
    """Purge all imported CSV and manual holdings/positions, leaving only live broker data."""
    from src.ingestion.kite_quote_poller import poll_kite_quotes
    from src.ingestion.kite_sync import purge_imported_holdings

    user_id = request.args.get("user_id", "default")
    count = purge_imported_holdings(user_id=user_id)
    try:
        poll_kite_quotes()
    except Exception as exc:
        logger.debug("Quote poller after purge: {}", exc)

    return jsonify(
        {
            "ok": True,
            "data": {
                "purged_count": count,
                "message": f"Successfully purged {count} imported records. Showing live broker portfolio only.",
            },
        }
    )


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
    from src.db.repository import get_cash_flows, get_daily_snapshots

    benchmark = request.args.get("benchmark", "NIFTY 50 TRI")
    try:
        rf_rate = float(request.args.get("risk_free_rate", 0.065))
    except ValueError:
        rf_rate = 0.065

    window = request.args.get("window", "all")
    user_id = request.args.get("user_id", "default")

    snapshots = get_daily_snapshots(user_id=user_id)
    cash_flows = get_cash_flows(user_id=user_id)

    try:
        summary = compute_portfolio_performance_summary(
            snapshots=snapshots,
            cash_flows=cash_flows,
            rf_annual=rf_rate,
            user_id=user_id,
        )
    except TypeError:
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
    if not snapshots and not start_date and not end_date:
        try:
            from src.analytics.snapshot_recorder import record_daily_eod_snapshot

            record_daily_eod_snapshot(user_id=user_id, snapshot_date=date.today())
            snapshots = get_daily_snapshots(
                user_id=user_id,
                start_date=start_date,
                end_date=end_date,
                limit=limit,
            )
        except Exception:
            pass

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

    valid_keys = {expected_key.strip()} if expected_key and expected_key.strip() else set()
    if not provided_key or not any(
        hmac.compare_digest(provided_key.strip(), k) for k in valid_keys
    ):
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


@api_v1_bp.route("/broker/login", methods=["GET"])
@require_api_key
def broker_login():
    """Return the broker's interactive daily sign-in URL."""
    from src.ingestion.kite_auth import get_login_url

    return jsonify({"ok": True, "data": {"url": get_login_url()}})


@api_v1_bp.route("/broker/callback", methods=["GET"])
def broker_callback():
    """Exchange Kite's one-time request token and finish daily sign-in."""
    from flask import redirect

    from src.ingestion.kite_auth import exchange_token

    if request.args.get("error") or not request.args.get("request_token"):
        return (
            "<main><h1>Sign-in was not completed</h1><p>Return to PortfolioIQ and try again.</p></main>",
            400,
        )
    try:
        exchange_token(request.args["request_token"])
    except Exception:
        from loguru import logger

        logger.exception("Kite callback token exchange failed")
        return (
            "<main><h1>Sign-in failed</h1><p>Return to PortfolioIQ and try again.</p></main>",
            500,
        )
    frontend_origin = get_settings().frontend_origin.rstrip("/")
    return redirect(f"{frontend_origin}/settings.html?broker=connected")


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
        "daily_login_required": not bool(tok),
    }

    # 3. Market
    market_info = get_market_status()

    # 4. Scheduler
    jobs = [
        {
            "id": "instrument_refresh",
            "name": "Instrument Master Refresh",
            "schedule": "08:30 IST Mon-Fri",
        },
        {
            "id": "sod_sync",
            "name": "Start of Day Kite Sync",
            "schedule": "09:15 IST Mon-Fri",
        },
        {
            "id": "eod_export",
            "name": "End of Day Export",
            "schedule": "15:45 IST Mon-Fri",
        },
        {
            "id": "eod_snapshot",
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
            "schedule": "00:05 IST Daily",
        },
        {
            "id": "token_expiry_check",
            "name": "Kite Token Expiry Check",
            "schedule": "05:30 IST Daily",
        },
        {
            "id": "live_quote_polling",
            "name": "Live Quote Refresh",
            "schedule": f"Every {settings.polling_interval_sec}s during market hours",
        },
    ]
    scheduler_info = {
        "active": os.environ.get("RUN_INLINE_SCHEDULER", "").lower() == "true",
        "managed_by": "Single API process",
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


@api_v1_bp.route("/holdings/import-csv", methods=["POST"])
@require_api_key
def import_holdings_csv():
    """
    Parses and ingests portfolio holdings or tradebook CSV exports from Zerodha / generic brokers.
    Validates required columns, converts datatypes, resolves instrument tokens, and upserts holdings.
    """
    import csv
    import decimal
    import io
    import re
    import zlib
    from decimal import Decimal

    from sqlalchemy import text

    user_id = request.args.get("user_id", "default")

    # 1. Retrieve raw CSV content
    content = ""
    if "file" in request.files:
        uploaded_file = request.files["file"]
        if uploaded_file.filename == "":
            return format_error_response(
                "MISSING_FILE", "Uploaded file has no filename", status_code=400
            )
        content = uploaded_file.read().decode("utf-8-sig", errors="replace")
    elif request.is_json and request.json and "csv_text" in request.json:
        content = request.json["csv_text"]
    elif request.content_type and "text/csv" in request.content_type:
        content = request.get_data(as_text=True)

    if not content or not content.strip():
        return format_error_response(
            "EMPTY_FILE", "CSV content is empty or not provided", status_code=400
        )

    # 2. Parse lines with csv.reader
    reader = csv.reader(io.StringIO(content.strip()))
    header_row = None
    data_rows = []

    for row in reader:
        if not row or not any(cell.strip() for cell in row):
            continue
        cleaned_cells = [cell.strip().lower() for cell in row]
        # Detect header row: look for presence of symbol/instrument indicator
        if header_row is None:
            norm_cells = [re.sub(r"[^a-z0-9]", "", c) for c in cleaned_cells]
            if any(
                s in norm_cells for s in ["instrument", "symbol", "tradingsymbol", "stock", "scrip"]
            ):
                header_row = row
                continue
        else:
            data_rows.append(row)

    if not header_row:
        return format_error_response(
            "INVALID_CSV",
            "Could not identify a valid header row containing 'Instrument' or 'Symbol'",
            status_code=400,
        )

    # 3. Identify column indices
    col_map: dict[str, int] = {}
    for idx, col_name in enumerate(header_row):
        norm = re.sub(r"[^a-z0-9]", "", col_name.strip().lower())
        if (
            norm in ["instrument", "symbol", "tradingsymbol", "stock", "scrip"]
            and "symbol" not in col_map
        ):
            col_map["symbol"] = idx
        elif norm in ["qty", "quantity", "shares", "holdingqty"] and "quantity" not in col_map:
            col_map["quantity"] = idx
        elif (
            norm in ["avgcost", "avgprice", "averageprice", "buyprice", "price", "costprice", "avg"]
            and "price" not in col_map
        ):
            col_map["price"] = idx
        elif norm in ["exchange", "exch", "segment"] and "exchange" not in col_map:
            col_map["exchange"] = idx
        elif norm in ["tradetype", "type", "transactiontype"] and "trade_type" not in col_map:
            col_map["trade_type"] = idx

    for req in ["symbol", "quantity", "price"]:
        if req not in col_map:
            return format_error_response(
                "INVALID_CSV",
                f"Required column '{req}' not found in CSV. Headers: {header_row}",
                status_code=400,
            )

    sym_idx = col_map["symbol"]
    qty_idx = col_map["quantity"]
    price_idx = col_map["price"]
    exch_idx = col_map.get("exchange")
    trade_type_idx = col_map.get("trade_type")

    # 4. Parse rows into holdings
    parsed_holdings: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    skipped_count = 0

    for line_num, r in enumerate(data_rows, start=2):
        if len(r) <= max(sym_idx, qty_idx, price_idx):
            skipped_count += 1
            errors.append(f"Row {line_num}: Insufficient columns")
            continue

        raw_sym = r[sym_idx].strip().upper()
        # Clean symbol suffixes e.g. INFY-EQ -> INFY, NSE:TCS -> TCS
        raw_sym = re.sub(r"^(NSE|BSE):", "", raw_sym)
        raw_sym = re.sub(r"-(EQ|BE|SM|ST)$", "", raw_sym).strip()
        if not raw_sym:
            skipped_count += 1
            continue

        raw_qty = r[qty_idx].strip().replace(",", "")
        raw_price = r[price_idx].strip().replace(",", "").replace("₹", "").replace("$", "")
        exchange = (
            r[exch_idx].strip().upper() if exch_idx is not None and len(r) > exch_idx else "NSE"
        ) or "NSE"

        try:
            qty = int(float(raw_qty))
            price = Decimal(str(float(raw_price)))
        except (ValueError, decimal.InvalidOperation) as exc:
            skipped_count += 1
            errors.append(f"Row {line_num} ({raw_sym}): Invalid number format ({exc})")
            continue

        # If tradebook, aggregate buys and sells
        if trade_type_idx is not None and len(r) > trade_type_idx:
            trade_type = r[trade_type_idx].strip().upper()
            if raw_sym not in parsed_holdings:
                parsed_holdings[raw_sym] = {
                    "symbol": raw_sym,
                    "quantity": 0,
                    "total_cost": Decimal("0"),
                    "exchange": exchange,
                }
            if "BUY" in trade_type:
                parsed_holdings[raw_sym]["quantity"] += qty
                parsed_holdings[raw_sym]["total_cost"] += Decimal(qty) * price
            elif "SELL" in trade_type:
                parsed_holdings[raw_sym]["quantity"] -= qty
        else:
            if qty <= 0 or price <= 0:
                skipped_count += 1
                errors.append(f"Row {line_num} ({raw_sym}): Quantity and price must be > 0")
                continue
            parsed_holdings[raw_sym] = {
                "symbol": raw_sym,
                "quantity": qty,
                "total_cost": Decimal(qty) * price,
                "exchange": exchange,
            }

    # 5. Filter valid holdings & Upsert into Database
    final_holdings: list[CSVImportHoldingDTO] = []
    with get_db_session() as session:
        for sym, h in parsed_holdings.items():
            qty = h["quantity"]
            if qty <= 0:
                continue
            avg_price = (h["total_cost"] / Decimal(qty)).quantize(Decimal("0.01"))
            exchange = h["exchange"]

            # Token resolution: check instrument_master first, else generate deterministic pseudo-token
            row = session.execute(
                text(
                    "SELECT instrument_token FROM instrument_master WHERE tradingsymbol = :sym AND exchange = :exch LIMIT 1"
                ),
                {"sym": sym, "exch": exchange},
            ).fetchone()
            if row:
                token = row[0]
            else:
                token = (zlib.crc32(f"{exchange}:{sym}".encode()) & 0x7FFFFFFF) or 100001
                session.execute(
                    text("""
                        INSERT INTO instrument_master (
                            instrument_token, exchange_token, tradingsymbol,
                            name, isin, exchange, instrument_type, is_active
                        )
                        VALUES (
                            :token, :token, :sym,
                            :sym, NULL, :exchange, 'EQ', TRUE
                        )
                        ON CONFLICT (instrument_token) DO NOTHING
                    """),
                    {"token": token, "sym": sym, "exchange": exchange},
                )

            # Upsert into user_holdings
            session.execute(
                text("""
                    INSERT INTO user_holdings (
                        user_id, instrument_token, tradingsymbol, exchange, isin,
                        quantity, t1_quantity, opening_quantity, used_quantity,
                        authorised_quantity, collateral_quantity, average_price,
                        last_price, close_price, pnl, day_change, day_change_pct,
                        product, has_discrepancy, last_synced_at
                    )
                    VALUES (
                        :user_id, :token, :sym, :exchange, NULL,
                        :qty, 0, :qty, 0,
                        :qty, 0, :avg_price,
                        :avg_price, :avg_price, 0, 0, 0,
                        'CNC', FALSE, NOW()
                    )
                    ON CONFLICT (user_id, instrument_token, product) DO UPDATE SET
                        quantity = EXCLUDED.quantity,
                        average_price = EXCLUDED.average_price,
                        opening_quantity = EXCLUDED.opening_quantity,
                        last_synced_at = NOW(),
                        updated_at = NOW()
                """),
                {
                    "user_id": user_id,
                    "token": token,
                    "sym": sym,
                    "exchange": exchange,
                    "qty": qty,
                    "avg_price": avg_price,
                },
            )

            final_holdings.append(
                CSVImportHoldingDTO(
                    tradingsymbol=sym,
                    quantity=qty,
                    average_price=avg_price,
                    exchange=exchange,
                    instrument_token=token,
                    invested_value=(Decimal(qty) * avg_price).quantize(Decimal("0.01")),
                )
            )

    result_dto = CSVImportResultDTO(
        imported_count=len(final_holdings),
        skipped_count=skipped_count,
        holdings=final_holdings,
        errors=errors[:10],
        message=f"Successfully imported {len(final_holdings)} holdings from CSV.",
    )
    return jsonify({"ok": True, "data": result_dto.model_dump()}), 200


# ─────────────────────────────────────────────────────────────
# Positions & Smart CSV Import (migration 022)
# ─────────────────────────────────────────────────────────────


@api_v1_bp.route("/holdings/positions", methods=["GET"])
@require_api_key
def get_positions():
    """Return live positions from user_positions table (populated by Kite sync or CSV import)."""
    from sqlalchemy import text as sa_text

    user_id = request.args.get("user_id", "default")
    with get_db_session() as session:
        rows = (
            session.execute(
                sa_text("""
                SELECT tradingsymbol, exchange, product, quantity,
                       average_price, last_price, pnl, day_change, day_change_pct,
                       data_source, last_synced_at
                FROM user_positions
                WHERE user_id = :user_id
                ORDER BY ABS(quantity) DESC, tradingsymbol
            """),
                {"user_id": user_id},
            )
            .mappings()
            .all()
        )
    return jsonify(
        {
            "ok": True,
            "data": [dict(r) for r in rows],
            "count": len(rows),
        }
    ), 200


@api_v1_bp.route("/holdings/import-smart", methods=["POST"])
@require_api_key
@limiter.limit(get_settings().rate_limit_mutations)
def import_smart_csv():
    """
    Auto-detect and import a Zerodha CSV export (holdings, positions, or ledger).
    Returns a preview payload before committing. Add ?commit=1 to write to DB.
    """
    from datetime import date
    from decimal import Decimal

    from sqlalchemy import text as sa_text

    from src.ingestion.broker_csv_import import BrokerCSVError, parse_broker_csv

    user_id = request.args.get("user_id", "default")
    commit = request.args.get("commit", "0") in ("1", "true", "yes")

    if "file" in request.files:
        f = request.files["file"]
        raw = f.read()
        filename = f.filename or "upload.csv"
    elif request.is_json and request.json and "csv_text" in request.json:
        raw = request.json["csv_text"].encode()
        filename = request.json.get("filename", "inline.csv")
    else:
        return format_error_response(
            "MISSING_FILE", "Provide a file upload or csv_text in JSON body.", 400
        )

    try:
        parsed = parse_broker_csv(filename, raw)
    except BrokerCSVError as exc:
        return format_error_response("PARSE_ERROR", str(exc), 400)

    kind = parsed["kind"]
    rows = parsed["rows"]

    # The standard Zerodha holdings export omits exchange. Resolve that case
    # consistently with our price-symbol map (for example, GOLDCASE is BSE).
    # Keep the resolved exchange in the preview so the user can verify it.
    if kind in ("holdings", "positions"):
        from src.ingestion.ticker_map import get_yf_ticker

        for row in rows:
            if not row.get("exchange"):
                ticker = get_yf_ticker(row["symbol"])
                row["exchange"] = "BSE" if ticker.endswith(".BO") else "NSE"

    def json_safe(value):
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, date):
            return value.isoformat()
        return value

    if not commit:
        return jsonify(
            {
                "ok": True,
                "preview": True,
                "kind": kind,
                "filename": filename,
                "row_count": len(rows),
                "skipped_count": parsed.get("skipped_count", 0),
                "rows": [
                    {key: json_safe(value) for key, value in row.items()} for row in rows[:50]
                ],  # cap preview at 50 rows
            }
        ), 200

    # ── commit to DB ──────────────────────────────────────────
    imported = 0
    cash_flows_imported = 0

    with get_db_session() as session:
        if kind in ("holdings", "positions"):
            table = "user_holdings" if kind == "holdings" else "user_positions"
            for r in rows:
                sym = r["symbol"]
                qty = r["quantity"]
                avg = float(r["average_price"])
                ltp = float(r.get("last_price") or Decimal("0"))
                exchange = r.get("exchange") or "NSE"
                product = r.get("product", "CNC")

                # Resolve or create instrument_master token
                existing = session.execute(
                    sa_text(
                        "SELECT instrument_token FROM instrument_master WHERE tradingsymbol = :s AND exchange = :e LIMIT 1"
                    ),
                    {"s": sym, "e": exchange},
                ).fetchone()
                if existing:
                    token = existing[0]
                else:
                    import zlib

                    token = (zlib.crc32(f"{exchange}:{sym}".encode()) & 0x7FFFFFFF) or 100001
                    session.execute(
                        sa_text("""
                            INSERT INTO instrument_master (instrument_token, exchange_token, tradingsymbol, name, isin, exchange, instrument_type, is_active)
                            VALUES (:tok, :tok, :sym, :sym, NULL, :exch, 'EQ', TRUE)
                            ON CONFLICT (instrument_token) DO NOTHING
                        """),
                        {"tok": token, "sym": sym, "exch": exchange},
                    )

                if table == "user_holdings":
                    session.execute(
                        sa_text("""
                            INSERT INTO user_holdings (
                                user_id, instrument_token, tradingsymbol, exchange, isin,
                                quantity, t1_quantity, opening_quantity, used_quantity,
                                authorised_quantity, collateral_quantity, average_price,
                                last_price, close_price, pnl, day_change, day_change_pct,
                                product, has_discrepancy, last_synced_at, data_source
                            )
                            VALUES (
                                :uid, :tok, :sym, :exch, NULL,
                                :qty, 0, :qty, 0, :qty, 0, :avg,
                                :ltp, :ltp, 0, 0, 0,
                                :prod, FALSE, NOW(), 'CSV_IMPORT'
                            )
                            ON CONFLICT (user_id, instrument_token, product) DO UPDATE SET
                                quantity = EXCLUDED.quantity,
                                average_price = EXCLUDED.average_price,
                                last_price = EXCLUDED.last_price,
                                opening_quantity = EXCLUDED.opening_quantity,
                                last_synced_at = NOW(),
                                data_source = 'CSV_IMPORT',
                                updated_at = NOW()
                        """),
                        {
                            "uid": user_id,
                            "tok": token,
                            "sym": sym,
                            "exch": exchange,
                            "qty": qty,
                            "avg": avg,
                            "ltp": ltp,
                            "prod": product,
                        },
                    )
                else:  # user_positions
                    session.execute(
                        sa_text("""
                            INSERT INTO user_positions (
                                user_id, instrument_token, tradingsymbol, exchange,
                                product, quantity, average_price, last_price,
                                pnl, day_change, day_change_pct,
                                data_source, last_synced_at, updated_at
                            )
                            VALUES (
                                :uid, :tok, :sym, :exch,
                                :prod, :qty, :avg, :ltp,
                                :pnl, 0, 0,
                                'CSV_IMPORT', NOW(), NOW()
                            )
                            ON CONFLICT (user_id, instrument_token, product) DO UPDATE SET
                                quantity = EXCLUDED.quantity,
                                average_price = EXCLUDED.average_price,
                                last_price = EXCLUDED.last_price,
                                last_synced_at = NOW(),
                                data_source = 'CSV_IMPORT',
                                updated_at = NOW()
                        """),
                        {
                            "uid": user_id,
                            "tok": token,
                            "sym": sym,
                            "exch": exchange,
                            "qty": qty,
                            "avg": avg,
                            "ltp": ltp,
                            "pnl": float(r.get("pnl") or Decimal("0")),
                            "prod": product,
                        },
                    )
                imported += 1

        elif kind == "ledger":
            for r in rows:
                inserted = session.execute(
                    sa_text("""
                        INSERT INTO broker_ledger_entries (
                            user_id, import_hash, posting_date, particulars,
                            cost_center, voucher_type, debit, credit,
                            net_balance, entry_kind, source_file
                        )
                        VALUES (
                            :uid, :hash, :dt, :particulars,
                            :cc, :vt, :debit, :credit,
                            :bal, :kind, :src
                        )
                        ON CONFLICT (user_id, import_hash) DO NOTHING
                    """),
                    {
                        "uid": user_id,
                        "hash": r["import_hash"],
                        "dt": r["posting_date"],
                        "particulars": r.get("particulars", ""),
                        "cc": r.get("cost_center", ""),
                        "vt": r.get("voucher_type", ""),
                        "debit": float(r.get("debit") or 0),
                        "credit": float(r.get("credit") or 0),
                        "bal": float(r.get("net_balance") or 0)
                        if r.get("net_balance") is not None
                        else None,
                        "kind": r.get("entry_kind", "OTHER"),
                        "src": filename,
                    },
                )
                imported += max(inserted.rowcount or 0, 0)

                # Statement snapshots and trade settlements are retained in
                # the ledger, but only genuine external movements enter the
                # performance cash-flow series.
                flow_kind = r.get("entry_kind")
                if flow_kind in {
                    "DEPOSIT",
                    "WITHDRAWAL",
                    "DIVIDEND",
                    "CHARGE",
                    "INTEREST",
                } and r.get("posting_date"):
                    if flow_kind in {"DEPOSIT", "DIVIDEND", "INTEREST"}:
                        amount = r.get("credit") or r.get("debit")
                    else:
                        amount = r.get("debit") or r.get("credit")
                    if amount and amount > 0:
                        flow = session.execute(
                            sa_text("""
                                INSERT INTO portfolio_cash_flows (
                                    user_id, flow_date, flow_type, amount,
                                    source, external_reference, notes
                                )
                                VALUES (
                                    :uid, :flow_date, :flow_type, :amount,
                                    'BROKER_LEDGER', :external_reference, :notes
                                )
                                ON CONFLICT (user_id, external_reference)
                                WHERE source = 'BROKER_LEDGER'
                                  AND external_reference IS NOT NULL
                                DO NOTHING
                            """),
                            {
                                "uid": user_id,
                                "flow_date": r["posting_date"],
                                "flow_type": flow_kind,
                                "amount": amount,
                                "external_reference": f"kite-ledger:{r['import_hash']}",
                                "notes": r.get("particulars", ""),
                            },
                        )
                        cash_flows_imported += max(flow.rowcount or 0, 0)

    return jsonify(
        {
            "ok": True,
            "preview": False,
            "kind": kind,
            "filename": filename,
            "imported_count": imported,
            "cash_flows_imported": cash_flows_imported,
            "skipped_count": parsed.get("skipped_count", 0),
            "message": f"Imported {imported} {kind} rows from {filename}.",
        }
    ), 200


@api_v1_bp.route("/ledger", methods=["GET"])
@require_api_key
def get_ledger():
    """Return broker ledger entries from broker_ledger_entries table."""
    from sqlalchemy import text as sa_text

    user_id = request.args.get("user_id", "default")
    try:
        limit = min(int(request.args.get("limit", 100)), 500)
    except ValueError:
        limit = 100
    kind = request.args.get("kind")

    with get_db_session() as session:
        if kind:
            rows = (
                session.execute(
                    sa_text("""
                    SELECT posting_date, particulars, cost_center, voucher_type,
                           debit, credit, net_balance, entry_kind, source_file, imported_at
                    FROM broker_ledger_entries
                    WHERE user_id = :uid AND entry_kind = :kind
                    ORDER BY posting_date DESC NULLS LAST, id DESC
                    LIMIT :lim
                """),
                    {"uid": user_id, "kind": kind.upper(), "lim": limit},
                )
                .mappings()
                .all()
            )
        else:
            rows = (
                session.execute(
                    sa_text("""
                    SELECT posting_date, particulars, cost_center, voucher_type,
                           debit, credit, net_balance, entry_kind, source_file, imported_at
                    FROM broker_ledger_entries
                    WHERE user_id = :uid
                    ORDER BY posting_date DESC NULLS LAST, id DESC
                    LIMIT :lim
                """),
                    {"uid": user_id, "lim": limit},
                )
                .mappings()
                .all()
            )

    return jsonify(
        {
            "ok": True,
            "data": [dict(r) for r in rows],
            "count": len(rows),
        }
    ), 200
