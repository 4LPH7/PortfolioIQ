"""
PortfolioIQ — Kite Connect Authentication
Handles the OAuth token exchange flow for Zerodha Kite Connect.

IMPORTANT: Kite's login flow CANNOT be fully automated.
Exchange regulations require a manual user login step.
This module provides a local Flask server that captures the
request_token from the OAuth redirect and exchanges it for
an access_token automatically.

Flow:
    1. Run this script: python -m src.ingestion.kite_auth
    2. It opens the Kite login URL in your browser
    3. You log in on Zerodha's website
    4. Zerodha redirects to http://127.0.0.1:5000/callback?request_token=XXX
    5. Flask catches the redirect, exchanges the token, stores it in DB
    6. Flask server shuts down. Done.
"""
from __future__ import annotations

import threading
import webbrowser
from datetime import datetime, timedelta

import pytz
from flask import Flask, request
from kiteconnect import KiteConnect
from loguru import logger

from src.config.settings import get_settings
from src.db.connection import execute_sql, get_db_session
from sqlalchemy import text

IST = pytz.timezone("Asia/Kolkata")


def _get_kite_client() -> KiteConnect:
    """Return a configured KiteConnect client (no access token yet)."""
    settings = get_settings()
    return KiteConnect(api_key=settings.kite_api_key)


def get_login_url() -> str:
    """Generate the Zerodha login URL for the configured API key."""
    kite = _get_kite_client()
    url = kite.login_url()
    logger.info("Kite login URL: {}", url)
    return url


def exchange_token(request_token: str) -> str:
    """
    Exchange a request_token (from OAuth redirect) for an access_token.
    Stores the access_token in the broker_sessions table.

    Args:
        request_token: The one-time token from Kite's redirect URL

    Returns:
        The access_token string
    """
    settings = get_settings()
    kite = _get_kite_client()

    logger.info("Exchanging request_token for access_token...")
    session_data = kite.generate_session(
        request_token=request_token,
        api_secret=settings.kite_api_secret,
    )

    access_token: str = session_data["access_token"]

    # Calculate expiry: Kite tokens expire around 6 AM IST next day
    now_ist = datetime.now(tz=IST)
    tomorrow_6am = (now_ist + timedelta(days=1)).replace(
        hour=6, minute=0, second=0, microsecond=0
    )

    # Store in DB (upsert — one row per user)
    with get_db_session() as session:
        session.execute(
            text("""
                INSERT INTO broker_sessions
                    (user_id, api_key, access_token, issued_at, expires_at, is_valid)
                VALUES
                    (:user_id, :api_key, :token, NOW(), :expires_at, TRUE)
                ON CONFLICT (user_id) DO UPDATE SET
                    api_key      = EXCLUDED.api_key,
                    access_token = EXCLUDED.access_token,
                    issued_at    = EXCLUDED.issued_at,
                    expires_at   = EXCLUDED.expires_at,
                    is_valid     = TRUE
            """),
            {
                "user_id": "default",
                "api_key": settings.kite_api_key,
                "token": access_token,
                "expires_at": tomorrow_6am,
            },
        )

    logger.success(
        "Access token stored. Valid until ~{}", tomorrow_6am.strftime("%Y-%m-%d 06:00 IST")
    )
    return access_token


def get_stored_token() -> str | None:
    """
    Retrieve the current valid access_token from the database.
    Returns None if no valid token exists (login required).
    """
    rows = execute_sql(
        """
        SELECT access_token, expires_at, is_valid
        FROM broker_sessions
        WHERE user_id = 'default'
          AND is_valid = TRUE
          AND expires_at > NOW()
        LIMIT 1
        """,
    )
    if not rows:
        logger.warning("No valid Kite access token found. Login required.")
        return None
    token = rows[0]["access_token"]
    expires = rows[0]["expires_at"]
    logger.debug("Loaded access token (expires: {})", expires)
    return token


def get_authenticated_kite() -> KiteConnect:
    """
    Return a KiteConnect client with a valid access token.
    Raises RuntimeError if no valid token is stored (login required).

    Usage:
        kite = get_authenticated_kite()
        holdings = kite.holdings()
    """
    token = get_stored_token()
    if token is None:
        raise RuntimeError(
            "No valid Kite access token. "
            "Run: python -m src.ingestion.kite_auth  to log in."
        )
    settings = get_settings()
    kite = KiteConnect(api_key=settings.kite_api_key)
    kite.set_access_token(token)
    return kite


def invalidate_token() -> None:
    """Mark the current stored token as invalid (e.g., on API 403 error)."""
    with get_db_session() as session:
        session.execute(
            text("UPDATE broker_sessions SET is_valid = FALSE WHERE user_id = 'default'")
        )
    logger.warning("Kite access token invalidated.")


# ============================================================
# OAuth Callback Flask Server
# ============================================================

_access_token_result: list[str] = []  # Thread-safe result container
_server_shutdown = threading.Event()


def _create_callback_app() -> Flask:
    """Create the minimal Flask app that handles the OAuth callback."""
    app = Flask(__name__)
    app.logger.disabled = True  # Suppress Flask's own logging

    @app.route("/callback")
    def callback():
        request_token = request.args.get("request_token")
        error = request.args.get("error")

        if error:
            logger.error("Kite login failed: {}", error)
            _server_shutdown.set()
            return f"<h2>Login Failed</h2><p>{error}</p>", 400

        if not request_token:
            logger.error("No request_token in callback URL")
            _server_shutdown.set()
            return "<h2>Error: No request_token received</h2>", 400

        logger.info("request_token received: {}...", request_token[:8])

        try:
            token = exchange_token(request_token)
            _access_token_result.append(token)
            _server_shutdown.set()
            return """
                <h2 style="color:green;">✅ PortfolioIQ — Authentication Successful</h2>
                <p>Access token stored securely. You can close this tab.</p>
                <p>The server will shut down automatically.</p>
            """
        except Exception as exc:
            logger.exception("Token exchange failed: {}", exc)
            _server_shutdown.set()
            return f"<h2>Token Exchange Failed</h2><p>{exc}</p>", 500

    return app


def run_auth_flow() -> str:
    """
    Execute the full OAuth authentication flow:
    1. Start local Flask callback server
    2. Open Kite login URL in browser
    3. Wait for OAuth redirect
    4. Exchange token and store in DB
    5. Shut down server

    Returns:
        access_token string
    """
    settings = get_settings()
    login_url = get_login_url()

    app = _create_callback_app()

    # Start Flask in a background thread
    server_thread = threading.Thread(
        target=lambda: app.run(
            host="127.0.0.1",
            port=5000,
            debug=False,
            use_reloader=False,
        ),
        daemon=True,
    )
    server_thread.start()

    logger.info("=" * 60)
    logger.info("KITE AUTHENTICATION REQUIRED")
    logger.info("=" * 60)
    logger.info("Opening browser for Zerodha login...")
    logger.info("If browser doesn't open, go to:\n{}", login_url)
    logger.info("=" * 60)

    webbrowser.open(login_url)

    # Block until callback received (or timeout after 5 minutes)
    _server_shutdown.wait(timeout=300)

    if _access_token_result:
        return _access_token_result[0]

    raise RuntimeError("Authentication timed out or failed. Please try again.")


if __name__ == "__main__":
    """
    Run this module directly to authenticate:
        python -m src.ingestion.kite_auth
    """
    from src.db.connection import check_connection

    if not check_connection():
        print("ERROR: Cannot connect to database. Is PostgreSQL running?")
        exit(1)

    token = run_auth_flow()
    print(f"\n✅ Authentication complete. Token stored in database.")
    print(f"You can now run the main application.")
