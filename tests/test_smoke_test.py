"""
Tests for scripts/smoke_test.py
Verifies HTTP endpoint probing, assertion validation, cold-start retry backoff,
timeout failure, and CLI exit codes.
"""

from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import patch

from scripts.smoke_test import check_endpoint, main, run_smoke_test


class MockHTTPResponse:
    """Mock urllib HTTP response object supporting context manager protocol."""

    def __init__(
        self, status_code: int, body_dict: dict | None = None, raw_body: bytes | None = None
    ):
        self.status_code = status_code
        if raw_body is not None:
            self._data = raw_body
        elif body_dict is not None:
            self._data = json.dumps(body_dict).encode("utf-8")
        else:
            self._data = b""

    def getcode(self) -> int:
        return self.status_code

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


class TestCheckEndpoint:
    """Unit tests for check_endpoint function."""

    @patch("urllib.request.urlopen")
    def test_check_endpoint_success(self, mock_urlopen) -> None:
        mock_urlopen.return_value = MockHTTPResponse(200, {"status": "ok", "db": True})
        passed, msg = check_endpoint("http://test/api/v1/health", lambda d: d.get("status") == "ok")
        assert passed is True
        assert msg == "OK"

    @patch("urllib.request.urlopen")
    def test_check_endpoint_assertion_failed(self, mock_urlopen) -> None:
        mock_urlopen.return_value = MockHTTPResponse(200, {"status": "ok", "db": False})
        passed, msg = check_endpoint("http://test/api/v1/health", lambda d: d.get("db") is True)
        assert passed is False
        assert "Assertion failed" in msg

    @patch("urllib.request.urlopen")
    def test_check_endpoint_non_200_status(self, mock_urlopen) -> None:
        mock_urlopen.return_value = MockHTTPResponse(503, {"status": "unhealthy"})
        passed, msg = check_endpoint("http://test/api/v1/health", lambda d: True)
        assert passed is False
        assert "Expected HTTP 200, got 503" in msg

    @patch("urllib.request.urlopen")
    def test_check_endpoint_invalid_json(self, mock_urlopen) -> None:
        mock_urlopen.return_value = MockHTTPResponse(200, raw_body=b"<html>Bad Gateway</html>")
        passed, msg = check_endpoint("http://test/api/v1/health", lambda d: True)
        assert passed is False
        assert "Invalid JSON response" in msg

    @patch("urllib.request.urlopen")
    def test_check_endpoint_http_error(self, mock_urlopen) -> None:
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="http://test", code=500, msg="Internal Server Error", hdrs={}, fp=io.BytesIO()
        )
        passed, msg = check_endpoint("http://test/api/v1/health", lambda d: True)
        assert passed is False
        assert "HTTP Error 500" in msg

    @patch("urllib.request.urlopen")
    def test_check_endpoint_url_error(self, mock_urlopen) -> None:
        mock_urlopen.side_effect = urllib.error.URLError(reason="Connection refused")
        passed, msg = check_endpoint("http://test/api/v1/health", lambda d: True)
        assert passed is False
        assert "Connection failed: Connection refused" in msg

    @patch("urllib.request.urlopen")
    def test_check_endpoint_timeout_error(self, mock_urlopen) -> None:
        mock_urlopen.side_effect = TimeoutError()
        passed, msg = check_endpoint("http://test/api/v1/health", lambda d: True)
        assert passed is False
        assert "Request timed out" in msg

    @patch("urllib.request.urlopen")
    def test_check_endpoint_generic_exception(self, mock_urlopen) -> None:
        mock_urlopen.side_effect = RuntimeError("Socket error")
        passed, msg = check_endpoint("http://test/api/v1/health", lambda d: True)
        assert passed is False
        assert "Unexpected error: Socket error" in msg


class TestRunSmokeTest:
    """Tests for run_smoke_test polling and retry loop."""

    @patch("scripts.smoke_test.check_endpoint")
    def test_run_smoke_test_immediate_success(self, mock_check) -> None:
        mock_check.return_value = (True, "OK")
        success = run_smoke_test(
            base_url="https://app.portfolioiq.test/",
            timeout=10.0,
            initial_interval=1.0,
        )
        assert success is True
        assert mock_check.call_count == 2  # health and market status

    @patch("time.sleep")
    @patch("scripts.smoke_test.check_endpoint")
    def test_run_smoke_test_cold_start_retry_succeeds(self, mock_check, mock_sleep) -> None:
        # Attempt 1: health fails
        # Attempt 2: health passes, market status fails
        # Attempt 3: both pass
        mock_check.side_effect = [
            (False, "Service Unavailable"),  # attempt 1 health
            (True, "OK"),  # attempt 2 health
            (False, "Market route not ready"),  # attempt 2 market
            (True, "OK"),  # attempt 3 health
            (True, "OK"),  # attempt 3 market
        ]
        success = run_smoke_test(
            base_url="https://app.portfolioiq.test",
            timeout=30.0,
            initial_interval=1.0,
        )
        assert success is True
        assert mock_sleep.call_count == 2

    @patch("time.sleep")
    @patch("scripts.smoke_test.check_endpoint")
    def test_run_smoke_test_timeout_fails(self, mock_check, mock_sleep) -> None:
        mock_check.return_value = (False, "Service Unavailable")
        # Run with a short timeout and interval, simulating multiple failures until timeout
        start = [100.0]

        def fake_time():
            start[0] += 5.0
            return start[0]

        with patch("time.time", side_effect=fake_time):
            success = run_smoke_test(
                base_url="https://app.portfolioiq.test",
                timeout=12.0,
                initial_interval=2.0,
            )
            assert success is False


class TestMainCLI:
    """Tests for smoke_test CLI entrypoint."""

    @patch("sys.exit")
    @patch("scripts.smoke_test.run_smoke_test", return_value=True)
    def test_main_success_exits_0(self, mock_run, mock_exit) -> None:
        with patch("sys.argv", ["smoke_test.py", "--base-url", "http://localhost:5000"]):
            main()
            mock_exit.assert_called_once_with(0)

    @patch("sys.exit")
    @patch("scripts.smoke_test.run_smoke_test", return_value=False)
    def test_main_failure_exits_1(self, mock_run, mock_exit) -> None:
        with patch("sys.argv", ["smoke_test.py", "--base-url", "http://localhost:5000"]):
            main()
            mock_exit.assert_called_once_with(1)
