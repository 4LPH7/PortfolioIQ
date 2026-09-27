"""
PortfolioIQ — Safety-Critical Coverage Verification Script
Parses coverage.json output from pytest-cov and enforces a strict >= 85.0%
coverage threshold on safety-critical paths:
    - src/execution/
    - src/config/
    - src/api/middleware.py

Exit code:
    0: All critical paths meet or exceed 85.0% coverage.
    1: One or more critical paths are below 85.0%, or coverage.json is missing/invalid.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CRITICAL_TARGET_PCT = 85.0

# Prefixes or exact file paths (normalized with forward slashes)
CRITICAL_PATH_PATTERNS = [
    "src/execution/",
    "src/config/",
    "src/api/middleware.py",
]


def is_critical_file(file_path: str) -> bool:
    """Check if normalized file path matches any critical pattern."""
    normalized = file_path.replace("\\", "/")
    return any(pattern in normalized for pattern in CRITICAL_PATH_PATTERNS)


def check_coverage(coverage_file: Path, threshold: float = CRITICAL_TARGET_PCT) -> bool:
    """
    Read coverage.json and verify each matching critical file achieves >= threshold.
    Returns True if all critical files pass, False otherwise.
    """
    if not coverage_file.exists():
        print(f"ERROR: Coverage report file not found at: {coverage_file}", file=sys.stderr)
        return False

    try:
        with open(coverage_file, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        print(f"ERROR: Failed to parse coverage JSON: {exc}", file=sys.stderr)
        return False

    files_data = data.get("files", {})
    critical_files: dict[str, dict] = {}

    for file_path, details in files_data.items():
        if is_critical_file(file_path):
            critical_files[file_path] = details

    if not critical_files:
        print("ERROR: No safety-critical files found in coverage report!", file=sys.stderr)
        return False

    print("=" * 80)
    print(f"Safety-Critical Code Coverage Verification (Target: >= {threshold:.1f}%)")
    print("=" * 80)
    print(f"{'File':<55} {'Covered':<8} {'Total':<8} {'Percent':<8}")
    print("-" * 80)

    all_passed = True
    for file_path, details in sorted(critical_files.items()):
        summary = details.get("summary", {})
        covered = summary.get("covered_lines", 0)
        total = summary.get("num_statements", 0)
        percent = summary.get("percent_covered", 0.0)

        # Normalize relative display name
        norm_path = file_path.replace("\\", "/")
        if "src/" in norm_path:
            norm_path = norm_path[norm_path.index("src/") :]

        status_marker = "PASS" if percent >= threshold else "FAIL"
        if percent < threshold:
            all_passed = False

        print(f"{norm_path:<55} {covered:<8} {total:<8} {percent:>6.2f}%  [{status_marker}]")

    print("=" * 80)
    if all_passed:
        print("SUCCESS: All safety-critical modules meet or exceed the 85.0% coverage requirement.")
    else:
        print(
            "FAILURE: One or more safety-critical modules failed the 85.0% coverage requirement.",
            file=sys.stderr,
        )

    return all_passed


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify safety-critical test coverage.")
    parser.add_argument(
        "--coverage-file",
        type=Path,
        default=Path("coverage.json"),
        help="Path to coverage.json file (default: coverage.json)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=CRITICAL_TARGET_PCT,
        help=f"Minimum required coverage percentage (default: {CRITICAL_TARGET_PCT})",
    )
    args = parser.parse_args()

    success = check_coverage(coverage_file=args.coverage_file, threshold=args.threshold)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
