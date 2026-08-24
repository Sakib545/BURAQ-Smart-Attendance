# Bug fixes — BURAQ Smart Attendance

All fixes are in the `app/` package (the code the server actually runs via
`app.main:app`). Existing tests pass (77 passed) after the changes.

## 1. Removed stale, divergent duplicate code at the repo root
The repo shipped two copies of the same modules — `main.py`, `services.py`,
`face_ai.py`, `whatsapp.py`, `ui.py`, `config.py`, `employee_seed.py` and
`base.html` at the root, plus the real ones under `app/` / `templates/`. The
root copies had drifted by thousands of lines (`main.py` alone differed by
~2000 lines) but were **never imported** — the server, the Dockerfile and every
test use `app.*`. They were a trap for anyone editing the wrong file, so the
unused root copies were deleted. Nothing that runs referenced them.

## 2. Backfill now matches live check-in (`app/main.py::_write_backfill`)
HR-backfilled past days computed late minutes differently from a live check-in:
they **skipped the late-grace period**, and when an employee had no explicit
duty schedule they recorded **`late = 0`** instead of falling back to the global
shift window. A worker backfilled at noon for an 08:30 shift showed "on time".
Backfill now honours the employee's assigned shift, uses custom/weekly duty
first and the global shift window otherwise, applies the grace period, and also
records `early_leave_minutes` — identical to what the WhatsApp flow produces.

## 3. Removed dead overtime aggregate on the dashboard (`app/main.py`)
The dashboard KPI query summed `attendance.overtime_minutes` into an `overtime`
variable that was never displayed (the card shows "Checked out today"). Since
overtime is manual-only since v9.24, `overtime_minutes` is always 0 — the sum
and the unused variable were removed.

## 4. First-shift workers are no longer mislabelled by the clock (`app/services.py::resolve_attendance_shift`)  ⭐ main fix
The shift resolver only let an **explicit second-shift** assignment win; a
`morning` assignment always fell through to the 16:00 clock cutoff. So a
first-shift worker who checked in after 16:00 (e.g. 16:20 for an 08:30 duty)
was re-labelled **Second Shift** and measured against the 16:00 start —
recorded as **20 minutes late instead of the real ~8 hours**, and the mislabel
also skewed check-out early-leave. Because `shift` is `NOT NULL DEFAULT
'morning'` and the UI only writes `morning`/`evening`, this hit every
first-shift employee.

Fix: an explicit assignment now wins in **both** directions (first *and*
second). The clock is used only when the shift column is genuinely blank/
unknown. Per-day exceptions (someone covering the other shift) are handled by a
custom/weekly duty row, which `duty_window` already honours first. This does
not regress the earlier early-arrival fix.

## 5. Small correctness/cleanliness fixes
* `app/main.py` — removed five `f"..."` literals that had no placeholders.
* `app/main.py` — renamed a local `state` that shadowed the imported
  `services.state`.
* `app/face_ai.py` — dropped three unused landmark `y` unpacks in the yaw-only
  pose estimate.

---

## Correcting already-recorded data — `scripts/repair_attendance_derived.py`
The code fixes only change *new* records. Rows already written with the wrong
shift/late/early need recomputing. The repair script recomputes the three
**derived** fields (`attendance_shift`, `late_minutes`, `early_leave_minutes`)
for every row that has a check-in, using the corrected rules. It corrects
misfiles in both directions **and** the stale-late left behind by old backfills
and HR time-corrections.

It never touches `check_in`/`check_out` timestamps, `status`, or `source`
(those are human intent; late/early/shift are pure functions of them).

* **Dry run by default** — prints an old→new diff table and writes nothing.
* **Payroll-locked months are skipped** (a `payroll_records` row marked
  `finalized`/`paid`) unless you pass `--include-locked`.
* Idempotent — a second run reports "nothing to do".

```bash
# preview this month
python scripts/repair_attendance_derived.py

# preview a specific range / everything
python scripts/repair_attendance_derived.py --from 2026-01-01 --to 2026-08-31
python scripts/repair_attendance_derived.py --all

# write the corrections (after reviewing the dry run)
python scripts/repair_attendance_derived.py --all --apply
```

Recommended: **take a backup first**, run the dry run, review the diff, then
`--apply`. The older `scripts/repair_shift_lateness.py` only handled one
direction; this new script supersedes it.
