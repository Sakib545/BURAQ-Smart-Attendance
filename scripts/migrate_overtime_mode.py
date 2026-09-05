"""Normalise legacy ``overtime_mode='auto'`` payroll rows to ``'manual'``.

    python scripts/migrate_overtime_mode.py            # report only
    python scripts/migrate_overtime_mode.py --apply    # write the change

Overtime in this system is always entered by hand — the payroll form posts a
hidden ``overtime_mode=manual``, and ``_calculate_employee_payroll`` stamps
'manual' on every result. Nothing anywhere derives overtime from attendance.

The column default, however, was ``'auto'``, so any row written before the form
started forcing the value kept that. Both the salary sheet and the payslip view
read those rows as::

    manual_hours = overtime_hours if mode == 'manual' else 0

— so a legacy row's overtime hours are silently discarded and the employee is
paid no overtime, with nothing shown to explain it. Only draft rows recalculate,
so finalized rows are unaffected until someone reopens them.

This script reports every non-manual row, flags the ones actually losing money
(overtime_hours > 0), and with ``--apply`` sets them all to 'manual'. That is a
label correction, not a figure change: it makes the stored mode match what the
hours already mean. Net salary is not recomputed here — reopen and re-prepare
any affected finalized payslip to refresh its snapshot.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.database import get_db  # noqa: E402


def find_legacy_rows() -> list[dict]:
    with get_db() as c:
        rows = c.execute(
            "SELECT p.id, p.salary_month, p.payment_status, p.overtime_mode, "
            "p.overtime_hours, p.overtime_rate, p.overtime_amount, "
            "e.staff_id, e.name "
            "FROM payroll_records p JOIN employees e ON e.id = p.employee_id "
            "WHERE COALESCE(p.overtime_mode,'') <> 'manual' "
            "ORDER BY p.salary_month, e.staff_id"
        ).fetchall()
    return [dict(r) for r in rows]


def apply_migration() -> int:
    with get_db() as c:
        cursor = c.execute(
            "UPDATE payroll_records SET overtime_mode='manual', updated_at=CURRENT_TIMESTAMP "
            "WHERE COALESCE(overtime_mode,'') <> 'manual'"
        )
        try:
            return int(cursor.result.rowcount)
        except (AttributeError, TypeError):
            return -1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--apply", action="store_true",
                        help="Actually write the change (default is report only)")
    args = parser.parse_args()

    rows = find_legacy_rows()
    if not rows:
        print("Nothing to migrate — every payroll row already has overtime_mode='manual'.")
        return 0

    losing = [r for r in rows if float(r["overtime_hours"] or 0) > 0]
    drafts = [r for r in losing if str(r["payment_status"]) == "draft"]

    print(f"{len(rows)} payroll row(s) have a non-manual overtime_mode.")
    print(f"{len(losing)} of them record overtime hours that are currently being ignored.")
    if drafts:
        print(f"{len(drafts)} of those are drafts, so the loss is live in the salary sheet today.\n")
    else:
        print()

    if losing:
        print(f"{'Staff':<12}{'Name':<24}{'Month':<10}{'Status':<11}{'Mode':<8}{'OT hrs':>9}{'OT rate':>10}{'Lost':>12}")
        for r in losing:
            hours = float(r["overtime_hours"] or 0)
            rate = float(r["overtime_rate"] or 0)
            print(f"{str(r['staff_id']):<12}{str(r['name'])[:23]:<24}{r['salary_month']:<10}"
                  f"{str(r['payment_status']):<11}{str(r['overtime_mode']):<8}"
                  f"{hours:>9.2f}{rate:>10.2f}{hours * rate:>12,.2f}")
        print(f"\nTotal overtime being ignored: {sum(float(r['overtime_hours'] or 0) * float(r['overtime_rate'] or 0) for r in losing):,.2f}")

    if not args.apply:
        print("\nReport only — nothing written. Re-run with --apply to set these rows to 'manual'.")
        return 1

    changed = apply_migration()
    print(f"\nUpdated {changed if changed >= 0 else len(rows)} row(s) to overtime_mode='manual'.")
    if drafts:
        print("Draft payslips will pick up the restored overtime on their next view.")
    finalized = [r for r in losing if str(r["payment_status"]) in {"finalized", "paid"}]
    if finalized:
        print(f"{len(finalized)} finalized/paid payslip(s) still serve an old snapshot — "
              "reopen and re-prepare them to refresh the figures.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
