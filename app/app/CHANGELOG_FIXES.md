# Bug fixes — BURAQ Smart Attendance

Test suite: 76 passed. The main app change is in **`app/services.py`**.

## Attendance is now counted against each employee's assigned duty
Requirement: whatever duty a person is given (08:30–16:00, 10:00–18:00,
16:00–22:00, …), their lateness/attendance is counted against exactly those
hours.

The system already stored per-employee duties (weekly `duty_schedules` and
per-date `custom_duties`), and lateness was measured against them — but the
first/second **shift label** was still guessed from the clock, so an evening
worker arriving at 15:55 for a 16:00 duty was mislabelled and, without an
assigned duty, measured against the 08:30 global start (the "440 minutes late"
you saw).

Fix (`app/services.py`): a new `resolve_duty()` makes the employee's own
assigned duty drive **both** the window and the first/second label. A custom
duty for the exact date wins first, then the weekly duty for that weekday. Only
when a person has **no** assigned duty does it fall back to the clock + global
shift window. `check_in`, `check_out` and the selfie-approval flow all use it,
so the number and the label always match the hours the person was given.

Verified:
| Assigned duty | Check-in | Result |
|---|---|---|
| 08:30–16:00 | 08:20 | First Shift, 0m |
| 08:30–16:00 | 09:15 | First Shift, 45m |
| 10:00–18:00 | 10:20 | First Shift, 20m |
| 16:00–22:00 | 15:55 | Second Shift, 0m |
| 16:00–22:00 | 16:35 | Second Shift, 35m |

**What you must do in the app:** assign each employee their duty (Duty page →
weekly duty, or a custom duty for a specific date). Once a person has a duty,
everything is counted against it automatically — no shift-cutoff tuning needed.
Employees with no assigned duty still fall back to the global Shift Rules.

## Other fixes included
* **Removed stale duplicate code at the repo root** (`main.py`, `services.py`,
  `face_ai.py`, `whatsapp.py`, `ui.py`, `config.py`, `employee_seed.py`,
  `base.html`) — never imported (server/Dockerfile/tests all use `app.*`), a trap
  for editing the wrong file.
* **Backfill** (`_write_backfill`) now matches a live check-in: applies
  late-grace, uses the assigned duty (else global window instead of `late = 0`),
  and records early-leave.
* **Dashboard**: removed a dead `overtime` aggregate that was summed but never
  shown.
* **Cleanups**: placeholderless `f"..."` literals, a `state` name shadow, unused
  landmark unpacks.

---


## New: assign duty for a date range
The per-employee **Duty** page now has an **"Assign Duty for a Date Range"** card
in addition to "One Specific Date". Pick a *From date* and *To date*, a shift
preset (or a custom start/end), and an **Apply to** option — *Every day*, *Skip
Fridays*, or *Skip Friday & Saturday* — and it creates a custom duty for every
matching day at once (`POST /employees/{id}/duty/range`). It reuses the existing
`custom_duties` table, so `resolve_duty` counts against these immediately. Range
is capped at one year and rejects an end date before the start.

---

## Correcting already-recorded data — `scripts/repair_attendance_derived.py`
Recomputes the derived fields (`attendance_shift`, `late_minutes`,
`early_leave_minutes`) for existing rows using `resolve_duty` — i.e. against each
employee's assigned duty. Never touches check-in/out timestamps, status, or
source.

* Dry run by default; `--apply` to write.
* Payroll-locked months skipped unless `--include-locked`.
* Idempotent.

Order: **back up → assign the duties → preview → apply.**
```bash
python scripts/repair_attendance_derived.py --all          # preview
python scripts/repair_attendance_derived.py --all --apply  # write
```
Then recalculate any unlocked payroll drafts for affected months. (Assign
duties first so the repair recomputes against the right hours.)

---

## Round 2 additions (idea list)
Of the six ideas discussed, three already existed in the app and were left as-is:
bulk range duty (`POST /duty/bulk` + the Duty page selector), late / no-check-in
WhatsApp alerts (`app/reminders.py::run_reminder_cycle`, using each employee's
assigned duty times), and attendance-report filtering + CSV/Excel/PDF export
(the `/reports` page filter form and `/reports/export.*` routes).

The three genuinely-missing ones were implemented:

1. **HR time-correction now recomputes late/early/shift (bug fix).**
   `decide_correction` used to update only the check-in/out timestamps and leave
   `late_minutes` stale. It now recomputes `attendance_shift`, `late_minutes`
   and `early_leave_minutes` against the employee's assigned duty (`resolve_duty`)
   after applying the corrected times.

2. **"No duty for today" warning on the Duty page.**
   The page now lists active, approved employees who have neither a weekly duty
   for today's weekday nor a custom duty for today — the ones who would silently
   fall back to the global shift rules. (The old "Unassigned" pill only counted
   custom duties and missed weekly-duty coverage.)

3. **Deterministic tests (`app/tests/conftest.py`).**
   All test modules quietly shared one database (via `setdefault`) and leaked
   global shift-rule changes between files, which made a few assertions
   order-dependent. A conftest now pins a single fresh test DB and resets the
   shift rules to defaults before every test; a fragile one-character token
   tamper check was also made deterministic. The suite now passes repeatably
   (78 tests).
