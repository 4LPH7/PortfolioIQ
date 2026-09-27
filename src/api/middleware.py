"""
PortfolioIQ — API Middleware & Security
Enforces API key authentication, correlation ID propagation, and uniform error formatting.
"""
from __future__ import annotations

import contextvars
import hmac
from functools import wraps
from typing import Any, Callable, Type
from uuid import uuid4

from flask import Response, g, jsonify, request
from loguru import logger
from pydantic import BaseModel, ValidationError

from src.config.settings import get_settings

# Request-scoped correlation ID context variable
correlation_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="system")

# Endpoints explicitly exempt from API key validation
EXEMPT_ENDPOINTS = {
    "api_v1.health",
    "api_legacy.health",
    "api_v1.market_status",
    "api_legacy.market_status",
}


def setup_correlation_id() -> None:
    """Read or generate correlation ID for current request and set in contextvars."""
    req_id = request.headers.get("X-Request-ID") or uuid4().hex[:12]
    correlation_id_ctx.set(req_id)
    g.correlation_id = req_id


def attach_correlation_id_header(response: Response) -> Response:
    """Attach correlation ID to outgoing response headers."""
    req_id = getattr(g, "correlation_id", None)
    if req_id:
        response.headers["X-Request-ID"] = req_id
    return response


def format_error_response(code: str, message: str, details: Any = None, status_code: int = 400) -> tuple[Response, int]:
    """Return a uniform error JSON response envelope."""
    payload = {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "details": details,
        },
    }
    return jsonify(payload), status_code


def require_api_key(f: Callable[..., Any]) -> Callable[..., Any]:
    """
    Decorator requiring a valid X-API-Key header.
    Exempts CORS preflight OPTIONS requests and designated public endpoints.
    """
    @wraps(f)
    def decorated_function(*args: Any, **kwargs: Any) -> Any:
        # Always allow CORS preflight
        if request.method == "OPTIONS":
            return "", 204

        # Allow explicitly exempt health / market status endpoints
        if request.endpoint in EXEMPT_ENDPOINTS:
            return f(*args, **kwargs)

        api_key = request.headers.get("X-API-Key")
        settings = get_settings()
        expected_key = settings.portfolioiq_api_key

        if not api_key or not expected_key or not hmac.compare_digest(api_key.strip(), expected_key.strip()):
            logger.warning("Unauthorized access attempt on {} from {}", request.path, request.remote_addr)
            return format_error_response(
                code="UNAUTHORIZED",
                message="Invalid or missing X-API-Key header",
                status_code=401,
            )

        return f(*args, **kwargs)

    return decorated_function


def validate_json(schema_cls: Type[BaseModel]) -> Callable[..., Any]:
    """
    Decorator that validates incoming request JSON against a Pydantic schema.
    Passes the validated model instance as the first argument to the route handler.
    """
    def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(f)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            body = request.get_json(silent=True)
            if body is None:
                body = {}
            try:
                validated = schema_cls.model_validate(body)
            except ValidationError as err:
                return format_error_response(
                    code="VALIDATION_ERROR",
                    message="Request payload failed validation",
                    details=err.errors(),
                    status_code=422,
                )
            return f(validated, *args, **kwargs)

        return wrapper

    return decorator
