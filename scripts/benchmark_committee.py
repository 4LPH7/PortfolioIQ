"""
Compare Claude Opus 5.5 and Sonnet 5.5 as the committee's analyst/debate model, with Opus 5.5
making the final rating in both arms. Same snapshot per stock, so differences come from the
model. Reports rating agreement with the all-Opus arm, citation grounding, cost and latency.

This spends real money (two full committee runs per stock), so it refuses to run without
--spend. Nothing is written to research_runs; results go to logs/benchmark_committee_*.json.

    python scripts/benchmark_committee.py --symbols INFY TCS --spend
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OPUS, SONNET = "claude-opus-5-5", "claude-sonnet-5-5"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--symbols", nargs="+", required=True)
    ap.add_argument("--user-id", default="default")
    ap.add_argument("--budget", type=float, default=1.0, help="USD cap per run")
    ap.add_argument("--spend", action="store_true", help="confirm that real API calls are made")
    args = ap.parse_args()

    if not args.spend:
        print(f"This makes {2 * len(args.symbols)} committee runs (up to ${args.budget:.2f} each).")
        print("Re-run with --spend to confirm.")
        return 2

    import anthropic

    from src.research.committee import CommitteeConfig, run_committee
    from src.research.runs import CALL_TIMEOUT_SEC, _build_snapshot, research_enabled

    if not research_enabled():
        print("ANTHROPIC_API_KEY is not set.")
        return 1
    client = anthropic.Anthropic(
        timeout=anthropic.Timeout(CALL_TIMEOUT_SEC, connect=10.0), max_retries=2
    )

    results = []
    for sym in args.symbols:
        snapshot = _build_snapshot(args.user_id, sym)
        row = {"symbol": sym, "data_as_of": snapshot["data_as_of"]}
        for arm, quick in (("opus", OPUS), ("sonnet", SONNET)):
            started = time.monotonic()
            out = run_committee(
                snapshot, client, CommitteeConfig(quick, OPUS, args.budget, started + 15 * 60)
            )
            calls = out["calls"]
            row[arm] = {
                "status": out["status"],
                "rating": (out["decision"] or {}).get("rating"),
                "conviction": (out["decision"] or {}).get("conviction"),
                "grounding_rate": out["grounding"]["rate"],
                "cost_usd": out["cost_usd"],
                "seconds": round(time.monotonic() - started, 1),
                "cache_read_tokens": sum(c.get("cache_read_input_tokens", 0) for c in calls),
                "fallbacks": sum(bool(c.get("fallback")) for c in calls),
            }
            print(sym, arm, row[arm])
        row["same_rating"] = row["opus"]["rating"] == row["sonnet"]["rating"]
        results.append(row)

    both = [r for r in results if r["opus"]["rating"] and r["sonnet"]["rating"]]
    summary = {
        "runs": len(results),
        "rating_agreement": round(sum(r["same_rating"] for r in both) / len(both), 3)
        if both
        else None,
        **{
            f"{arm}_{k}": round(sum(r[arm][k] or 0 for r in results) / len(results), 4)
            for arm in ("opus", "sonnet")
            for k in ("cost_usd", "seconds", "grounding_rate")
        },
    }
    out_path = Path("logs") / f"benchmark_committee_{datetime.now(UTC):%Y%m%dT%H%M%S}.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps({"summary": summary, "results": results}, indent=2))
    print(json.dumps(summary, indent=2))
    print(f"Saved {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
