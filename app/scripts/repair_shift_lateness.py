#!/usr/bin/env python3
"""Repair attendance rows that the old shift detection filed against the wrong
shift start.

Before v9.26 a check-in was classified purely by the clock. A second-shift
employee arriving at 15:56 for a 16:00 duty fell before the 16:00 cutoff, was
filed as first shift, and was then measured against the 08:30 start — recorded
as 446 minutes late for arriving four minutes early.

This script finds rows where the employee is assigned to an evening shift but
the row says ``attendance_shift='first'``, recomputes ``late_minutes`` against
their real duty window, and reports the difference.

DRY RUN BY DEFAULT — it prints what it would change and touches nothing:

    python scripts/repair_shift_lateness.py
    python scripts/repair_shift_lateness.py --from 2026-08-01 --to 2026-08-21
    python scripts/repair_shift_lateness.py --apply        # actually writes

Only ``attendance_shift`` and ``late_minutes`` are updated. Check-in and
check-out timestamps are never touched.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import get_db, init_db  # noqa: E402
from app.services import SECOND_SHIFT_ASSIGNMENTS, duty_window  # noqa: E402
from app.shift_rules import apply_late_grace  # noqa: E402


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="date_from", default="",
                        help="First work_date to inspect (YYYY-MM-DD). Default: 30 days back.")
    parser.add_argument("--to", dest="date_to", default="",
                        help="Last work_date to inspect (YYYY-MM-DD). Default: today.")
    parser.add_argument("--apply", action="store_true",
                        help="Write the corrections. Without this the script only reports.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    today = date.today()
    date_to = args.date_to or today.isoformat()
    date_from = args.date_from or (today.replace(day=1)).isoformat()

    init_db(max_attempts=1)

    placeholders = ",".join("?" for _ in SECOND_SHIFT_ASSIGNMENTS)
    with get_db() as c:
        rows = c.execute(
            "SELECT a.id,a.employee_id,a.work_date,a.check_in,a.late_minutes,a.attendance_shift,"
            "       e.staff_id,e.name,e.shift "
            "FROM attendance a JOIN employees e ON e.id=a.employee_id "
            "WHERE a.check_in IS NOT NULL AND a.work_date BETWEEN ? AND ? "
            f"  AND LOWER(TRIM(e.shift)) IN ({placeholders}) "
            "  AND a.attendance_shift='first' "
            "ORDER BY a.work_date, e.staff_id",
            (date_from, date_to, *SECOND_SHIFT_ASSIGNMENTS)).fetchall()

    if not rows:
        print(f"No misfiled rows between {date_from} and {date_to}. Nothing to do.")
        return 0

    changes = []
    for row in rows:
        try:
            check_in = datetime.fromisoformat(str(row["check_in"]))
            work_date = date.fromisoformat(str(row["work_date"]))
        except ValueError:
            print(f"  ! skipping attendance id={row['id']}: unparsable timestamp")
            continue
        employee = {"id": row["employee_id"], "shift": "evening"}
        start_dt, _ = duty_window(employee, work_date, shift_override="evening")
        new_late = apply_late_grace(int((check_in - start_dt).total_seconds() // 60))
        if new_late == int(row["late_minutes"]) and row["attendance_shift"] == "second":
            continue
        changes.append((row, new_late))

    print(f"Range {date_from} .. {date_to}")
    print(f"{len(changes)} row(s) would be corrected:\n")
    print(f"  {'DATE':<12} {'STAFF':<12} {'NAME':<18} {'OLD LATE':>9} {'NEW LATE':>9}")
    total_removed = 0
    for row, new_late in changes:
        total_removed += int(row["late_minutes"]) - new_late
        print(f"  {row['work_date']:<12} {str(row['staff_id']):<12} {str(row['name'])[:18]:<18} "
              f"{int(row['late_minutes']):>8}m {new_late:>8}m")
    print(f"\n  Total late minutes removed: {total_removed}")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to save these corrections.")
        return 0

    with get_db() as c:
        for row, new_late in changes:
            c.execute(
                "UPDATE attendance SET attendance_shift='second',late_minutes=?,"
                "source=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (new_late, "shift-repair", row["id"]))
    print(f"\n✅ Applied {len(changes)} correction(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
