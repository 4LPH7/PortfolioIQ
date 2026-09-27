"""
PortfolioIQ — Flask REST API
Dual-mounted API v1 endpoints with API key security, correlation tracking,
rate limiting, and uniform error envelopes.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent))

from flask import Flask, Response, request
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


def _allowed_cors_origins() -> list[str]:
    """Return the configured cross-origin allowlist; empty means deny all."""
    return [
        origin.strip()
        for origin in os.environ.get("ALLOWED_ORIGINS", "").split(",")
        if origin.strip()
    ]


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

# Dual-mount API Blueprints: /api/v1 (primary) and /api (legacy alias)
app.register_blueprint(api_v1_bp, url_prefix="/api/v1")
app.register_blueprint(api_v1_bp, url_prefix="/api", name="api_legacy")


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
