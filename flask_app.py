"""
PortfolioIQ — Flask REST API
Dual-mounted API v1 endpoints with API key security, correlation tracking,
rate limiting, and uniform error envelopes.
"""

from __future__ import annotations

import atexit
import os
import sys
from datetime import datetime
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent))

from flask import Flask, Response, request, send_from_directory
from flask_cors import CORS
from loguru import logger
from werkzeug.exceptions import HTTPException

from src.api.limiter import limiter
from src.api.middleware import (
    attach_correlation_id_header,
    format_error_response,
    setup_correlation_id,
)
from src.api.v1.blueprint import api_v1_bp
from src.execution.safety_certification import verify_startup_safety

DEFAULT_ALLOWED_ORIGINS = [
    "http://localhost:8080",
    "http://localhost:3000",
    "http://127.0.0.1:5000",
]


def _allowed_cors_origins() -> list[str]:
    """Return the configured cross-origin allowlist; defaults to local development."""
    raw = os.environ.get("ALLOWED_ORIGINS")
    if raw is None or not raw.strip():
        return list(DEFAULT_ALLOWED_ORIGINS)
    if raw.strip() == "*":
        return ["*"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


app = Flask(__name__)

# Configure CORS
CORS(app, origins=_allowed_cors_origins())

# Initialize rate limiting
limiter.init_app(app)

# Request lifecycle hooks
app.before_request(setup_correlation_id)


@app.before_request
def handle_options_preflight():
    """Return 204 No Content for CORS preflight OPTIONS requests without requiring auth."""
    if request.method == "OPTIONS":
        return Response("", status=204)


app.after_request(attach_correlation_id_header)


FRONTEND_DIR = Path(__file__).parent / "frontend"


@app.route("/")
def index():
    """Serve dashboard UI for browser requests, or service metadata for JSON API clients."""
    accept = request.headers.get("Accept", "")
    if "text/html" in accept and "application/json" not in accept:
        index_file = FRONTEND_DIR / "index.html"
        if index_file.exists():
            return send_from_directory(FRONTEND_DIR, "index.html")

    return {
        "ok": True,
        "service": "PortfolioIQ REST API",
        "version": "v1.0",
        "health": "/api/v1/health",
        "status": "online",
    }


@app.route("/<path:filename>")
def serve_frontend_static(filename: str):
    """Serve frontend HTML pages, stylesheets, scripts, and static assets."""
    if filename.startswith("api/") or filename == "api" or filename.startswith("callback"):
        return format_error_response("NOT_FOUND", "Endpoint not found", status_code=404)

    target = FRONTEND_DIR / filename
    if target.is_file():
        return send_from_directory(FRONTEND_DIR, filename)

    if (FRONTEND_DIR / f"{filename}.html").is_file():
        return send_from_directory(FRONTEND_DIR, f"{filename}.html")

    return format_error_response("NOT_FOUND", f"Resource '{filename}' not found", status_code=404)


# Dual-mount API Blueprints: /api/v1 (primary) and /api (legacy alias)
app.register_blueprint(api_v1_bp, url_prefix="/api/v1")
app.register_blueprint(api_v1_bp, url_prefix="/api", name="api_legacy")

# Kite apps configured before the API callback was namespaced may still redirect
# to this root path. Keep it as an alias so those sign-ins complete successfully.
app.add_url_rule(
    "/callback",
    endpoint="kite_callback_legacy",
    view_func=app.view_functions["api_v1.broker_callback"],
    methods=["GET"],
)

# Startup Safety Gate Verification (Phase 8)
try:
    _safety_report = verify_startup_safety()
    logger.info(
        "Trading safety gate verified: dry_run={} all_passed={}",
        _safety_report["is_dry_run"],
        _safety_report["all_passed"],
    )
except Exception as _safety_exc:
    logger.critical("Startup safety gate failed: {}", _safety_exc)
    raise


# Keep scheduled jobs in the API process for deployments without a separate
# worker. Configure one Gunicorn worker; host sleep or usage limits pause jobs
# whenever the API process is stopped.
_inline_scheduler = None
if os.environ.get("RUN_INLINE_SCHEDULER", "").lower() == "true":
    import pytz

    from src.config.settings import get_settings
    from src.scheduler.jobs import create_scheduler

    _IST = pytz.timezone("Asia/Kolkata")
    _inline_scheduler = create_scheduler()

    def _poll_live_quotes() -> None:
        from src.ingestion.kite_quote_poller import poll_kite_quotes_if_stale

        poll_kite_quotes_if_stale()

    _inline_scheduler.add_job(
        _poll_live_quotes,
        "interval",
        seconds=get_settings().polling_interval_sec,
        id="live_quote_polling",
        name="Live Kite quote refresh",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
        next_run_time=datetime.now(_IST),
    )
    _inline_scheduler.start()

    def _safe_shutdown():
        if _inline_scheduler and getattr(_inline_scheduler, "running", False):
            try:
                _inline_scheduler.shutdown(wait=False)
            except Exception:
                pass

    atexit.register(_safe_shutdown)


# Global Error Handlers — enforce uniform error envelope
@app.errorhandler(HTTPException)
def handle_http_exception(err: HTTPException):
    """Format all HTTP error responses into the uniform envelope."""
    code_map = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        422: "UNPROCESSABLE_ENTITY",
        429: "RATE_LIMIT_EXCEEDED",
    }
    error_code = code_map.get(err.code, "HTTP_ERROR")
    message = err.description or str(err)
    return format_error_response(
        code=error_code,
        message=message,
        status_code=err.code,
    )


@app.errorhandler(Exception)
def handle_unexpected_exception(err: Exception):
    """Format unhandled exceptions into 500 error envelope."""
    logger.exception("Unhandled server exception: {}", err)
    return format_error_response(
        code="INTERNAL_SERVER_ERROR",
        message="An unexpected server error occurred",
        status_code=500,
    )


if __name__ == "__main__":
    logger.info("PortfolioIQ Flask API starting on http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=True)
