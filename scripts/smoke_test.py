"""
PortfolioIQ — Backend Smoke Test Runner
Probes live backend health and market endpoints with exponential retry backoff.
Zero third-party dependencies (Python standard library only).

Usage:
    python scripts/smoke_test.py --base-url https://your-api-host.example
    python scripts/smoke_test.py --base-url http://localhost:5000 --timeout 30 --interval 2
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any


def check_endpoint(
    url: str,
    assertion: Callable[[dict[str, Any]], bool],
    timeout: float = 5.0,
) -> tuple[bool, str]:
    """
    Perform a GET request against url and evaluate assertion against parsed JSON.

    Returns:
        tuple of (passed: bool, message: str)
    """
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "PortfolioIQ-SmokeTest/1.0", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status_code = response.getcode()
            if status_code != 200:
                return False, f"Expected HTTP 200, got {status_code}"

            raw_body = response.read().decode("utf-8")
            try:
                data = json.loads(raw_body)
            except json.JSONDecodeError as exc:
                return False, f"Invalid JSON response: {exc}"

            if not assertion(data):
                return False, f"Assertion failed on response: {data}"

            return True, "OK"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP Error {exc.code}: {exc.reason}"
    except urllib.error.URLError as exc:
        return False, f"Connection failed: {exc.reason}"
    except TimeoutError:
        return False, "Request timed out"
    except Exception as exc:
        return False, f"Unexpected error: {exc}"


def run_smoke_test(
    base_url: str,
    timeout: float = 60.0,
    initial_interval: float = 5.0,
    max_interval: float = 15.0,
    backoff_factor: float = 1.5,
) -> bool:
    """
    Poll /api/v1/health and /api/v1/market/status until both succeed or timeout expires.

    Returns:
        True if all smoke tests pass, False on timeout or persistent failure.
    """
    base_url = base_url.rstrip("/")
    health_url = f"{base_url}/api/v1/health"
    market_url = f"{base_url}/api/v1/market/status"

    endpoints: list[tuple[str, str, Callable[[dict[str, Any]], bool]]] = [
        (
            "Health & DB Probe",
            health_url,
            lambda d: d.get("status") == "ok" and d.get("db") is True,
        ),
        (
            "Market Status Probe",
            market_url,
            lambda d: d.get("ok") is True,
        ),
    ]

    print("=" * 70)
    print("PortfolioIQ Deployment Smoke Test")
    print(f"Target Base URL: {base_url}")
    print(f"Timeout: {timeout:.1f}s | Initial Interval: {initial_interval:.1f}s")
    print("=" * 70)

    start_time = time.time()
    attempt = 1
    current_interval = initial_interval

    while True:
        elapsed = time.time() - start_time
        remaining = timeout - elapsed
        if remaining <= 0:
            print(f"\n[FAIL] Smoke test timed out after {elapsed:.1f}s ({attempt - 1} attempts).")
            return False

        print(f"\n[Attempt {attempt}] Elapsed: {elapsed:.1f}s / Remaining: {remaining:.1f}s")

        all_endpoints_healthy = True
        for name, url, assertion in endpoints:
            passed, msg = check_endpoint(url, assertion)
            if passed:
                print(f"  ✓ {name:<22} -> PASS ({url})")
            else:
                print(f"  ✗ {name:<22} -> FAIL ({url}): {msg}")
                all_endpoints_healthy = False
                break

        if all_endpoints_healthy:
            print("\n" + "=" * 70)
            print(
                f"SUCCESS: All endpoints verified healthy in {elapsed:.1f}s ({attempt} attempts)."
            )
            print("=" * 70)
            return True

        if remaining <= current_interval:
            sleep_time = remaining
        else:
            sleep_time = current_interval

        if sleep_time <= 0:
            print(f"\n[FAIL] Smoke test timed out after {elapsed:.1f}s.")
            return False

        print(f"  ...waiting {sleep_time:.1f}s before next retry (backoff)")
        time.sleep(sleep_time)

        attempt += 1
        current_interval = min(current_interval * backoff_factor, max_interval)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify backend deployment health via /api/v1/ endpoints."
    )
    parser.add_argument(
        "--base-url",
        required=True,
        help="Target backend base URL, e.g. http://localhost:5000 or https://your-api-host.example",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Maximum duration in seconds to wait for endpoints (default: 60.0)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Initial retry interval in seconds (default: 5.0)",
    )

    args = parser.parse_args()
    success = run_smoke_test(
        base_url=args.base_url,
        timeout=args.timeout,
        initial_interval=args.interval,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
