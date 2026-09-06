# Current payroll rule (version 3)

Basic uses the full calendar month excluding Fridays as its divisor. Only completed first/day-shift attendance earns basic; no weekly/custom assignment is required. Recorded second/night shifts and Fridays are excluded. Half-days count 0.5. Existing approved paid-leave credit and deductions remain. Late deductions use recorded late minutes and the global first-shift duration, not assigned break times.

Friday, Night, Eid/special and Bonus are manual amounts. Overtime is manually entered hours × rate. The form reloads saved monthly amounts before edits. Legacy Special Duty records remain visible as history but are no longer automatically added; review and save version-2 drafts before finalizing. Finalized/Paid snapshots and admin payment reversal are preserved. Performance ranking continues to use its original roster metrics. No database migration or bulk recalculation is performed.

Example: September 2026 has 26 non-Friday days. Basic 10,000 with 5 completed day duties earns 1,923.08 before manual additions and deductions. Future attendance does not earn salary and future days are not yet absences.

## Historical version-2 design (superseded)

# Regular salary and completed Special Duty

All normal roster entries, including Friday and night shifts and normal custom
assignments, contribute to Basic Salary. The divisor is the entire month's
regular roster; elapsed attendance/approved leave determine earned basic.
Schedule the complete normal month before preparing payroll. A partial roster
cannot reveal an employee's unentered contractual working days. Joining-date
filtering excludes pre-employment attendance/absence but keeps the full roster
as the divisor. No fixed 26-day fallback is applied.

In Payroll → Special Duty, an authorized payroll manager records an employee,
date, type, start/end time, amount and completion note. This is a record of
completed extra work, not a replacement attendance/WhatsApp shift assignment.
The manager must confirm completion and that the hours are not also claimed in
manual overtime (manual overtime has no dated intervals to match automatically).

Regular roster time cannot also be recorded as Special Duty. Checks cover
adjacent dates and overnight duties, overlapping special records, and duplicate
employee/date/type entries. Separate non-overlapping extra work on the same day
is allowed. Cancelling retains the amount, note, original actor, cancellation
actor/time and reason. Correct a record by cancelling and adding its replacement.
Only active records contribute to the start-date month's pay.

Monthly Night/Friday/Eid input boxes are replaced by dated records. Normal
Friday/night attendance never automatically generates an extra allowance.
Preview, saved calculation, salary sheet and exports use the same special-duty
summary. The Excel Special Duty tab shows the category totals and dated records.

## Finalization and historical records

Prepare/preview remain available during the month. Finalize waits for month end
and checks missing basic salary/duty, incomplete checkout and negative pay.
Drafts calculated under the new rule before month end must be saved/recalculated
again. A changed or conflicting Special Duty record also blocks finalization
until corrected and recalculated, for both individual and bulk finalization.

Adding/cancelling Special Duty is blocked while the start-date month's payslip
is Finalized/Paid. The existing Admin/Super Admin Paid → Finalized action and
Super Admin Reopen step remain available. Old Finalized/Paid calculations are
not automatically recalculated. An unreadable saved salary-sheet snapshot is
reported instead of being silently replaced by today's calculation.

## Migration and recovery

Deploy the complete branch, not isolated files from the supplied ZIPs. Startup
adds the Special Duty table and partial unique index, plus three allowance
columns needed by the supplied payroll changes. Existing records are retained.
Full database backups discover and include the new table automatically.

The provided audit script is read-only by default:

    python scripts/audit_payroll_snapshots.py --month 2026-07

It compares stored figures with current inputs. Differences are review findings,
not proof of overpayment: rosters and attendance may have changed since payment.

Legacy overtime mode reporting is also read-only by default:

    python scripts/migrate_overtime_mode.py

`--apply` is an explicit data mutation and is not run by this change. It changes
legacy mode labels, not frozen payroll snapshots; review affected records before
recalculating. No production migration or repair script was executed here.

## Verification

The suite covers rates, joining dates, normal Friday/night salary, all extra duty
categories, overlap/duplicates, cancellation history, permissions, locked/stale
payroll, export totals and repeatable schema migration. Tests run on an isolated
SQLite database; production PostgreSQL has not been exercised. SQL uses portable
parameters, per-employee transactional mutation locks and a database unique
index. Snapshot amounts normalize to JSON-compatible numbers for PostgreSQL.

A previously flaky location-token test was corrected to alter significant
signature bits instead of unused Base64 padding bits; application token logic
was not changed.
