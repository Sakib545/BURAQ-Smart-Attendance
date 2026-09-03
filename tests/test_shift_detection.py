"""Shift detection — an early arrival must never be recorded as hours late.

Regression from production: second-shift staff reaching the office at 3:56 PM
for a 4:00 PM duty were filed as first shift (15:56 is before the 16:00 cutoff)
and then measured against the 08:30 first-shift start — recorded as 446 minutes
"late" for arriving four minutes early.

v9.26.1 fixed this by consulting the employee's assigned duty before falling
back to the clock. These tests pin that behaviour down.
"""
import os
import sys
from datetime import date, datetime, time as clock_time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("DATABASE_PATH", "/tmp/buraq_shiftdet_test.db")
os.environ.setdefault("REQUIRE_SECURE_SECRETS", "false")
os.environ.setdefault("ALLOW_TEMP_DB_FALLBACK", "false")
os.environ.setdefault("SESSION_SECRET", "test-session-secret-01234567890123456789")
os.environ.setdefault("CONFIG_ENCRYPTION_KEY", "test-config-secret-0123456789012345678")
import pytest
from fastapi.testclient import TestClient
from zoneinfo import ZoneInfo

from app.config import settings
from app.database import get_db
from app.main import app
from app.services import automatic_attendance_shift, _employee_shift_for_attendance
from app.shift_rules import apply_late_grace, save_shift_rules, shift_window

EVENING = "TEST-SD-PM"
MORNING = "TEST-SD-AM"
TZ = ZoneInfo(settings.timezone)


@pytest.fixture(autouse=True)
def staff():
    with TestClient(app):
        # The cutoff HR actually wants: second shift starts at 16:00.
        save_shift_rules("08:30", "16:00", "16:00", "22:00", "16:00", 0)
        with get_db() as db:
            for staff_id, shift in ((EVENING, "second"), (MORNING, "morning")):
                row = db.execute(
                    "SELECT id FROM employees WHERE staff_id=?", (staff_id,)
                ).fetchone()
                if row:
                    db.execute("DELETE FROM attendance WHERE employee_id=?", (row["id"],))
                    db.execute("DELETE FROM custom_duties WHERE employee_id=?", (row["id"],))
                db.execute("DELETE FROM employees WHERE staff_id=?", (staff_id,))
                db.execute(
                    "INSERT INTO employees(staff_id,name,shift,registration_status,is_active) "
                    "VALUES(?,?,?,?,?)",
                    (staff_id, staff_id, shift, "approved", True),
                )
        yield
        # This module rewrites the shared shift rules (one test sweeps the
        # cutoff), so put the defaults back or the next module inherits them.
        save_shift_rules("08:30", "16:00", "16:00", "22:00", "16:00", 0)
        with get_db() as db:
            for staff_id in (EVENING, MORNING):
                row = db.execute(
                    "SELECT id FROM employees WHERE staff_id=?", (staff_id,)
                ).fetchone()
                if row:
                    db.execute("DELETE FROM attendance WHERE employee_id=?", (row["id"],))
                    db.execute("DELETE FROM custom_duties WHERE employee_id=?", (row["id"],))
                    db.execute("DELETE FROM employees WHERE id=?", (row["id"],))


def _employee(staff_id):
    with get_db() as db:
        return db.execute("SELECT * FROM employees WHERE staff_id=?", (staff_id,)).fetchone()


def _moment(clock):
    hour, minute = (int(part) for part in clock.split(":"))
    return datetime.combine(date(2026, 8, 20), clock_time(hour, minute), tzinfo=TZ)


def _late_for(staff_id, clock):
    """Reproduce exactly what check_in computes, without WhatsApp."""
    employee = _employee(staff_id)
    moment = _moment(clock)
    shift = automatic_attendance_shift(moment, employee)
    start, _ = shift_window(_employee_shift_for_attendance(shift))
    start_dt = datetime.combine(moment.date(), start, tzinfo=TZ)
    return shift, apply_late_grace(int((moment - start_dt).total_seconds() // 60))


# --- the reported regression -------------------------------------------------

@pytest.mark.parametrize("clock", ["15:56", "15:59"])
def test_early_second_shift_arrival_is_not_late(clock):
    shift, late = _late_for(EVENING, clock)
    assert shift == "second"
    assert late == 0


def test_the_clock_alone_would_still_have_said_first():
    """Guards the exact production number, so the bug cannot creep back."""
    moment = _moment("15:56")
    guessed = automatic_attendance_shift(moment)  # no employee — clock only
    assert guessed == "first"
    start, _ = shift_window(_employee_shift_for_attendance(guessed))
    old_late = apply_late_grace(
        int((moment - datetime.combine(moment.date(), start, tzinfo=TZ)).total_seconds() // 60)
    )
    assert old_late == 446
    # ...but with the employee supplied, it no longer does.
    assert _late_for(EVENING, "15:56") == ("second", 0)


# --- lateness must still be recorded ----------------------------------------

def test_genuinely_late_second_shift_arrival_is_still_counted():
    assert _late_for(EVENING, "16:25") == ("second", 25)


def test_morning_staff_are_unaffected():
    assert _late_for(MORNING, "08:30") == ("first", 0)
    assert _late_for(MORNING, "08:45") == ("first", 15)


def test_unassigned_staff_still_fall_back_to_the_clock():
    """shift='morning' is the column default, so the clock still decides."""
    employee = _employee(MORNING)
    assert automatic_attendance_shift(_moment("16:30"), employee) == "second"
    assert automatic_attendance_shift(_moment("09:30"), employee) == "first"


@pytest.mark.parametrize("assigned", ["second", "evening", "night", "SECOND", " Second "])
def test_every_evening_assignment_spelling_is_recognised(assigned):
    with get_db() as db:
        db.execute("UPDATE employees SET shift=? WHERE staff_id=?", (assigned, EVENING))
    assert automatic_attendance_shift(_moment("15:56"), _employee(EVENING)) == "second"


def test_a_rostered_duty_beats_both_the_column_and_the_clock():
    """A custom duty for the day is the most specific truth available."""
    employee = _employee(MORNING)
    with get_db() as db:
        db.execute(
            "INSERT INTO custom_duties(employee_id,duty_date,start_time,end_time,break_minutes,is_active) "
            "VALUES(?,?,?,?,?,?)",
            (employee["id"], "2026-08-20", "16:00", "22:00", 0, True),
        )
    # Arrives at 15:56 for a rostered 16:00 duty: early, not seven hours late.
    assert automatic_attendance_shift(_moment("15:56"), _employee(MORNING)) == "second"


def test_cutoff_no_longer_decides_for_assigned_evening_staff():
    for cutoff in ("15:00", "16:00", "17:00"):
        save_shift_rules("08:30", "16:00", "16:00", "22:00", cutoff, 0)
        assert _late_for(EVENING, "15:56") == ("second", 0)
