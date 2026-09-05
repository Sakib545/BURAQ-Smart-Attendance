"""Find payslips whose stored figures disagree with a fresh calculation.

    python scripts/audit_payroll_snapshots.py                 # every month
    python scripts/audit_payroll_snapshots.py --month 2026-07 # one month
    python scripts/audit_payroll_snapshots.py --all-statuses  # drafts too
    python scripts/audit_payroll_snapshots.py --csv out.csv   # save the table

READ ONLY. This script never writes to the database. It exists because a
finalized payslip serves its numbers from ``calculation_snapshot`` — a frozen
JSON blob — rather than recalculating. Any payslip finalized before the
mid-month divisor fix therefore still shows the old figures, and no amount of
correct code today will change what is already stored.

The mid-month bug: the per-day rate used to divide the fixed salary by the duty
days *elapsed so far* instead of the duty days in the whole month. A payroll
prepared on the 10th of a 30-day month paid a full month's salary for 10 days
of work. Records finalized mid-month are the ones to look at first, and the
``prepared`` column below shows when each snapshot was written.

The partial-month bug: the same rate divided by the days *assigned* to the
employee. Someone given only 7 duty days — a new joiner, or anyone set up with
custom duties and no weekly pattern — earned a full month's salary for working
those 7. The divisor is now the employee's standard duty month, so partial
assignments pay pro rata. New joiners are the ones to look at first.

Read the OVERPAID rows as money already out the door, and the UNDERPAID rows as
money still owed. Nothing here is corrected automatically: reopen the affected
payslips in the UI, re-prepare, and finalize again.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.database import get_db  # noqa: E402
from app.main import _calculate_employee_payroll  # noqa: E402

# Ignore differences smaller than this — pure float/rounding noise, not a bug.
TOLERANCE = 0.01

# Figures worth comparing. Anything else in the snapshot is informational.
COMPARED_FIELDS = (
    "per_day_salary",
    "earned_basic_salary",
    "gross_salary",
    "total_deduction",
    "net_salary",
)


def _rows(month: str | None, all_statuses: bool):
    clauses = ["1=1"]
    params: list = []
    if month:
        clauses.append("p.salary_month=?")
        params.append(month)
    if not all_statuses:
        clauses.append("p.payment_status IN ('finalized','paid')")
    sql = (
        "SELECT p.id, p.employee_id, p.salary_month, p.payment_status, p.fixed_salary, "
        "p.overtime_rate, p.overtime_mode, p.overtime_hours, p.bonus, p.advance_amount, "
        "p.fine_amount, p.deduction, p.net_salary, p.calculation_snapshot, p.created_at, "
        "e.staff_id, e.name "
        "FROM payroll_records p JOIN employees e ON e.id = p.employee_id "
        "WHERE " + " AND ".join(clauses) +
        " ORDER BY p.salary_month, e.staff_id"
    )
    with get_db() as c:
        return c.execute(sql, params).fetchall()


def audit(month: str | None = None, all_statuses: bool = False) -> list[dict]:
    findings = []
    for row in _rows(month, all_statuses):
        try:
            stored = json.loads(row["calculation_snapshot"] or "{}")
        except (TypeError, ValueError):
            findings.append({
                "payroll_id": int(row["id"]),
                "staff_id": row["staff_id"],
                "name": row["name"],
                "month": row["salary_month"],
                "status": row["payment_status"],
                "prepared": row["created_at"],
                "error": "snapshot unreadable",
                "stored_net": float(row["net_salary"] or 0),
                "fresh_net": None,
                "difference": None,
                "fields": {},
            })
            continue

        mode = str(row["overtime_mode"] or "auto")
        manual_hours = float(row["overtime_hours"] or 0) if mode == "manual" else 0
        try:
            fresh = _calculate_employee_payroll(
                int(row["employee_id"]), str(row["salary_month"]),
                float(row["fixed_salary"] or 0), float(row["overtime_rate"] or 0),
                mode, manual_hours, float(row["bonus"] or 0),
                float(row["advance_amount"] or 0), float(row["fine_amount"] or 0),
                float(row["deduction"] or 0),
            )
        except Exception as exc:  # a broken record should not stop the audit
            findings.append({
                "payroll_id": int(row["id"]), "staff_id": row["staff_id"],
                "name": row["name"], "month": row["salary_month"],
                "status": row["payment_status"], "prepared": row["created_at"],
                "error": f"recalculation failed: {exc}",
                "stored_net": float(row["net_salary"] or 0),
                "fresh_net": None, "difference": None, "fields": {},
            })
            continue

        differing = {}
        for field in COMPARED_FIELDS:
            old = float(stored.get(field) or 0)
            new = float(fresh.get(field) or 0)
            if abs(old - new) > TOLERANCE:
                differing[field] = (round(old, 2), round(new, 2))

        if not differing:
            continue

        stored_net = float(stored.get("net_salary") or row["net_salary"] or 0)
        fresh_net = float(fresh.get("net_salary") or 0)
        findings.append({
            "payroll_id": int(row["id"]),
            "staff_id": row["staff_id"],
            "name": row["name"],
            "month": row["salary_month"],
            "status": row["payment_status"],
            "prepared": row["created_at"],
            "error": None,
            "stored_net": round(stored_net, 2),
            "fresh_net": round(fresh_net, 2),
            "difference": round(stored_net - fresh_net, 2),
            "fields": differing,
        })
    return findings


def _print_report(findings: list[dict], scope: str) -> None:
    if not findings:
        print(f"No differences found ({scope}). Compared figures match the current calculation.")
        return

    broken = [f for f in findings if f["error"]]
    overpaid = [f for f in findings if not f["error"] and (f["difference"] or 0) > 0]
    underpaid = [f for f in findings if not f["error"] and (f["difference"] or 0) < 0]

    print(f"Payroll snapshot audit ({scope})")
    print("=" * 78)
    print(f"{len(findings)} payslip(s) differ from a fresh calculation.\n")

    def block(title, items, note):
        if not items:
            return
        total = sum(abs(i["difference"] or 0) for i in items)
        print(f"{title} — {len(items)} payslip(s), {total:,.2f} total")
        print(f"  {note}")
        print(f"  {'Staff':<12}{'Name':<24}{'Month':<10}{'Status':<11}{'Stored':>12}{'Correct':>12}{'Diff':>12}")
        for item in sorted(items, key=lambda i: abs(i["difference"] or 0), reverse=True):
            print(f"  {str(item['staff_id']):<12}{str(item['name'])[:23]:<24}"
                  f"{item['month']:<10}{item['status']:<11}"
                  f"{item['stored_net']:>12,.2f}{item['fresh_net']:>12,.2f}"
                  f"{item['difference']:>+12,.2f}")
            for field, (old, new) in item["fields"].items():
                if field != "net_salary":
                    print(f"      {field}: {old:,.2f} → {new:,.2f}")
        print()

    block("HIGHER SAVED AMOUNT", overpaid, "Saved figure exceeds the current calculation. Review roster history and payment evidence.")
    block("LOWER SAVED AMOUNT", underpaid, "Saved figure is below the current calculation. Review roster history and payment evidence.")

    if broken:
        print(f"COULD NOT CHECK — {len(broken)} payslip(s)")
        for item in broken:
            print(f"  {item['staff_id']} {item['name']} {item['month']}: {item['error']}")
        print()

    net = sum(f["difference"] or 0 for f in findings if not f["error"])
    print("-" * 78)
    print(f"Net exposure: {net:+,.2f}  (positive = saved estimate exceeds current calculation)")
    print("\nNothing was changed. To correct a payslip: reopen it, re-prepare, finalize again.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--month", help="Limit to one month, e.g. 2026-07")
    parser.add_argument("--all-statuses", action="store_true",
                        help="Include drafts (they recalculate live, so differences are expected)")
    parser.add_argument("--csv", help="Also write the findings to this CSV file")
    args = parser.parse_args()

    scope = args.month or "all months"
    if args.all_statuses:
        scope += ", all statuses"
    findings = audit(args.month, args.all_statuses)
    _print_report(findings, scope)

    if args.csv and findings:
        with open(args.csv, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["payroll_id", "staff_id", "name", "month", "status",
                             "prepared", "stored_net", "correct_net", "difference",
                             "changed_fields", "error"])
            for item in findings:
                writer.writerow([
                    item["payroll_id"], item["staff_id"], item["name"], item["month"],
                    item["status"], item["prepared"], item["stored_net"],
                    item["fresh_net"], item["difference"],
                    "; ".join(f"{k}: {v[0]}→{v[1]}" for k, v in item["fields"].items()),
                    item["error"] or "",
                ])
        print(f"\nCSV written to {args.csv}")

    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
