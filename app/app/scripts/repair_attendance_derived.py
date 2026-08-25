#!/usr/bin/env python3
"""Recompute derived attendance fields under the corrected shift logic.

Historically the clock-based shift detection misfiled check-ins in *both*
directions, and each direction left the stored ``late_minutes`` /
``early_leave_minutes`` / ``attendance_shift`` wrong:

* An evening-assigned worker arriving a few minutes early (before the 16:00
  cutoff) was filed as first shift and measured against the 08:30 start —
  recorded as hours "late" for arriving on time.
* A morning-assigned worker arriving after 16:00 was filed as second shift and
  measured against the 16:00 start — recorded as a few minutes late instead of
  the real several hours.

Separately, backfilled rows skipped the late-grace period, and HR time
corrections never recomputed ``late_minutes`` at all.

This script recomputes the *derived* fields for every attendance row that has a
check-in, using exactly the same rules the live WhatsApp flow now uses
(``resolve_attendance_shift`` + ``duty_window`` + ``apply_late_grace``). It
never touches ``check_in`` / ``check_out`` timestamps, ``status``, or
``source`` — those are human intent; late/early/shift are pure functions of
them and are simply brought back into agreement.

Safety:
  * DRY RUN BY DEFAULT — prints a diff table and writes nothing.
  * Payroll-locked months (a payroll_records row marked finalized/paid) are
    skipped unless you pass --include-locked, so a paid month is never quietly
    changed underneath a finalized payslip.

Usage:
    python scripts/repair_attendance_derived.py                 # dry run, this month
    python scripts/repair_attendance_derived.py --from 2026-01-01 --to 2026-08-31
    python scripts/repair_attendance_derived.py --all           # every dated row
    python scripts/repair_attendance_derived.py --apply         # actually write
    python scripts/repair_attendance_derived.py --all --apply --include-locked
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import get_db, init_db  # noqa: E402
from app.services import (  # noqa: E402
    _submitted_at_local,
    resolve_duty,
)
from app.shift_rules import apply_late_grace  # noqa: E402


def parse_args():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--from", dest="date_from", default="",
                   help="First work_date to inspect (YYYY-MM-DD). Default: 1st of this month.")
    p.add_argument("--to", dest="date_to", default="",
                   help="Last work_date to inspect (YYYY-MM-DD). Default: today.")
    p.add_argument("--all", action="store_true",
                   help="Inspect every dated row, ignoring --from/--to.")
    p.add_argument("--apply", action="store_true",
                   help="Write the corrections. Without this the script only reports.")
    p.add_argument("--include-locked", action="store_true",
                   help="Also correct months whose payroll is finalized/paid (off by default).")
    return p.parse_args()


def _month_locked(c, employee_id: int, month: str) -> bool:
    row = c.execute(
        "SELECT payment_status FROM payroll_records WHERE employee_id=? AND salary_month=?",
        (employee_id, month)).fetchone()
    return bool(row and str(row["payment_status"]) in {"finalized", "paid"})


def main() -> int:
    args = parse_args()
    today = date.today()

    init_db(max_attempts=1)

    if args.all:
        where, params = "a.check_in IS NOT NULL", ()
        span = "all dates"
    else:
        date_to = args.date_to or today.isoformat()
        date_from = args.date_from or today.replace(day=1).isoformat()
        where = "a.check_in IS NOT NULL AND a.work_date BETWEEN ? AND ?"
        params = (date_from, date_to)
        span = f"{date_from} .. {date_to}"

    changes = []
    skipped_locked = 0
    with get_db() as c:
        rows = c.execute(
            "SELECT a.id,a.employee_id,a.work_date,a.check_in,a.check_out,"
            "       a.attendance_shift,a.late_minutes,a.early_leave_minutes,"
            "       e.staff_id,e.name,e.shift "
            "FROM attendance a JOIN employees e ON e.id=a.employee_id "
            f"WHERE {where} "
            "ORDER BY a.work_date, e.staff_id", params).fetchall()

        for row in rows:
            month = str(row["work_date"])[:7]
            if not args.include_locked and _month_locked(c, row["employee_id"], month):
                skipped_locked += 1
                continue
            try:
                check_in = _submitted_at_local(str(row["check_in"]))
                work_date = date.fromisoformat(str(row["work_date"]))
            except (ValueError, TypeError):
                print(f"  ! skipping attendance id={row['id']}: unparsable timestamp")
                continue

            employee = {"id": row["employee_id"], "shift": row["shift"]}
            correct_shift, start_dt, end_dt = resolve_duty(employee, check_in, work_date, db=c)

            new_late = apply_late_grace(int((check_in - start_dt).total_seconds() // 60))
            if row["check_out"]:
                try:
                    check_out = _submitted_at_local(str(row["check_out"]))
                    new_early = max(0, int((end_dt - check_out).total_seconds() // 60))
                except (ValueError, TypeError):
                    new_early = int(row["early_leave_minutes"] or 0)
            else:
                new_early = int(row["early_leave_minutes"] or 0)

            old = (str(row["attendance_shift"]), int(row["late_minutes"] or 0),
                   int(row["early_leave_minutes"] or 0))
            new = (correct_shift, new_late, new_early)
            if old != new:
                changes.append((row, new))

    print(f"Range: {span}")
    if skipped_locked:
        print(f"Skipped {skipped_locked} row(s) in payroll-locked months "
              f"(use --include-locked to include them).")
    if not changes:
        print("Everything already agrees with the corrected rules. Nothing to do.")
        return 0

    print(f"{len(changes)} row(s) would be corrected:\n")
    print(f"  {'DATE':<12} {'STAFF':<10} {'NAME':<16} "
          f"{'SHIFT':>14} {'LATE(m)':>14} {'EARLY(m)':>14}")
    late_delta = 0
    for row, (shift, late, early) in changes:
        old_shift = str(row["attendance_shift"])
        old_late = int(row["late_minutes"] or 0)
        old_early = int(row["early_leave_minutes"] or 0)
        late_delta += late - old_late
        print(f"  {str(row['work_date']):<12} {str(row['staff_id']):<10} "
              f"{str(row['name'])[:16]:<16} "
              f"{old_shift + '→' + shift:>14} "
              f"{str(old_late) + '→' + str(late):>14} "
              f"{str(old_early) + '→' + str(early):>14}")
    sign = "+" if late_delta >= 0 else ""
    print(f"\n  Net late-minutes change: {sign}{late_delta}")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to save these corrections.")
        return 0

    with get_db() as c:
        for row, (shift, late, early) in changes:
            c.execute(
                "UPDATE attendance SET attendance_shift=?,late_minutes=?,"
                "early_leave_minutes=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (shift, late, early, row["id"]))
    print(f"\n✅ Applied {len(changes)} correction(s). "
          f"Timestamps, status and source were left untouched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
