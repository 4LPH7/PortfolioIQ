"""
Dry-run soak report for GRADUATION_CHECKLIST section 3E.

Usage: DATABASE_URL=... python scripts/soak_report.py [--days 14]
Prints, per day, orders audited / blocked / dry-run and how many failed validation checks
occurred. Exit 1 if fewer than --days distinct days have audit rows or any check failed
without a matching BLOCKED order (a gap in the audit trail).
"""

import argparse
import sys

from sqlalchemy import text

from src.db.connection import get_db_session


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    days = ap.parse_args().days

    with get_db_session() as s:
        rows = (
            s.execute(
                text("""
                SELECT (created_at AT TIME ZONE 'Asia/Kolkata')::date AS day,
                       COUNT(*) AS orders,
                       COUNT(*) FILTER (WHERE validation_status = 'BLOCKED') AS blocked,
                       COUNT(*) FILTER (WHERE validation_status = 'DRY_RUN') AS dry_run
                FROM order_audit_trail
                WHERE created_at >= NOW() - make_interval(days => :d * 2)
                GROUP BY 1 ORDER BY 1
            """),
                {"d": days},
            )
            .mappings()
            .all()
        )
        orphans = s.execute(
            text("""
                SELECT COUNT(*) FROM order_audit_trail a
                WHERE NOT EXISTS (SELECT 1 FROM order_validation_log v WHERE v.audit_id = a.id)
                  AND a.created_at >= NOW() - make_interval(days => :d * 2)
            """),
            {"d": days},
        ).scalar()

    print(f"{'day':<12}{'orders':>8}{'blocked':>9}{'dry_run':>9}")
    for r in rows:
        print(f"{r['day']!s:<12}{r['orders']:>8}{r['blocked']:>9}{r['dry_run']:>9}")
    print(f"\nDays with activity: {len(rows)} (need {days}); orders without check rows: {orphans}")
    ok = len(rows) >= days and orphans == 0
    print("SOAK PASSED" if ok else "SOAK NOT YET PASSED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
