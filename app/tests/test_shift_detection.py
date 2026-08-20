"""v9.26 — an early arrival must never be recorded as hours late.

Regression from production: second-shift staff reaching the office at 3:56 PM
for a 4:00 PM duty were filed as first shift (because 15:56 is before the 16:00
cutoff) and then measured against the 08:30 first-shift start — 446 minutes
"late" for arriving four minutes early.
"""
import os
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("DATABASE_PATH", "/tmp/buraq_shiftfix_test.db")
os.environ.setdefault("REQUIRE_SECURE_SECRETS", "false")
os.environ.setdefault("ALLOW_TEMP_DB_FALLBACK", "false")
os.environ.setdefault("SESSION_SECRET", "test-session-secret-01234567890123456789")
os.environ.setdefault("CONFIG_ENCRYPTION_KEY", "test-config-secret-0123456789012345678")

Path(os.environ["DATABASE_PATH"]).unlink(missing_ok=True)

import pytest
from fastapi.testclient import TestClient
from zoneinfo import ZoneInfo

from app.config import settings
from app.main import app
from app.database import get_db
from app.services import (automatic_attendance_shift, resolve_attendance_shift,
                          _employee_shift_for_attendance)
from app.shift_rules import apply_late_grace, save_shift_rules, shift_window

EVENING_STAFF = "TEST-SHIFT-PM"
MORNING_STAFF = "TEST-SHIFT-AM"
TZ = ZoneInfo(settings.timezone)


@pytest.fixture(autouse=True)
def rules_and_staff():
    with TestClient(app):
        # The cutoff HR actually wants: second shift starts at 16:00.
        save_shift_rules("08:30", "16:00", "16:00", "22:00", "16:00", 0)
        with get_db() as db:
            for staff_id, shift in ((EVENING_STAFF, "second"), (MORNING_STAFF, "morning")):
                row = db.execute("SELECT id FROM employees WHERE staff_id=?", (staff_id,)).fetchone()
                if row:
                    db.execute("DELETE FROM attendance WHERE employee_id=?", (row["id"],))
                db.execute("DELETE FROM employees WHERE staff_id=?", (staff_id,))
                db.execute(
                    "INSERT INTO employees(staff_id,name,shift,registration_status,is_active) "
                    "VALUES(?,?,?,?,?)", (staff_id, staff_id, shift, "approved", True))
        yield
        with get_db() as db:
            for staff_id in (EVENING_STAFF, MORNING_STAFF):
                row = db.execute("SELECT id FROM employees WHERE staff_id=?", (staff_id,)).fetchone()
                if row:
                    db.execute("DELETE FROM attendance WHERE employee_id=?", (row["id"],))
                    db.execute("DELETE FROM employees WHERE id=?", (row["id"],))


def _employee(staff_id: str):
    with get_db() as db:
        return db.execute("SELECT * FROM employees WHERE staff_id=?", (staff_id,)).fetchone()


def _moment(clock: str):
    hour, minute = (int(part) for part in clock.split(":"))
    return datetime.combine(date(2026, 8, 20), time(hour, minute), tzinfo=TZ)


def _late_for(staff_id: str, clock: str) -> int:
    """Reproduce exactly what check_in computes, without WhatsApp."""
    employee = _employee(staff_id)
    moment = _moment(clock)
    attendance_shift = resolve_attendance_shift(employee, moment)
    start, _ = shift_window(_employee_shift_for_attendance(attendance_shift))
    start_dt = datetime.combine(moment.date(), start, tzinfo=TZ)
    return apply_late_grace(int((moment - start_dt).total_seconds() // 60))


# --- the reported regression -------------------------------------------------

@pytest.mark.parametrize("clock", ["15:56", "15:59"])
def test_early_second_shift_arrival_is_not_late(clock):
    assert resolve_attendance_shift(_employee(EVENING_STAFF), _moment(clock)) == "second"
    assert _late_for(EVENING_STAFF, clock) == 0


def test_the_old_behaviour_would_have_reported_446_minutes():
    """Guards the exact number seen in production, so it cannot come back."""
    moment = _moment("15:56")
    guessed = automatic_attendance_shift(moment)
    assert guessed == "first"  # the clock alone still says first
    start, _ = shift_window(_employee_shift_for_attendance(guessed))
    old_late = apply_late_grace(int((moment - datetime.combine(moment.date(), start, tzinfo=TZ)).total_seconds() // 60))
    assert old_late == 446
    # ...but the resolver no longer uses that guess for assigned staff.
    assert _late_for(EVENING_STAFF, "15:56") == 0


# --- lateness must still be recorded ----------------------------------------

def test_genuinely_late_second_shift_arrival_is_still_counted():
    assert _late_for(EVENING_STAFF, "16:25") == 25


def test_morning_staff_are_unaffected():
    assert resolve_attendance_shift(_employee(MORNING_STAFF), _moment("08:45")) == "first"
    assert _late_for(MORNING_STAFF, "08:45") == 15
    assert _late_for(MORNING_STAFF, "08:30") == 0


def test_unassigned_staff_still_fall_back_to_the_clock():
    """shift='morning' is the column default, so the clock still decides."""
    employee = _employee(MORNING_STAFF)
    assert resolve_attendance_shift(employee, _moment("16:30")) == "second"
    assert resolve_attendance_shift(employee, _moment("09:30")) == "first"


@pytest.mark.parametrize("assigned", ["second", "evening", "night", "SECOND", " Second "])
def test_every_evening_assignment_spelling_is_recognised(assigned):
    with get_db() as db:
        db.execute("UPDATE employees SET shift=? WHERE staff_id=?", (assigned, EVENING_STAFF))
    assert resolve_attendance_shift(_employee(EVENING_STAFF), _moment("15:56")) == "second"


def test_resolver_survives_a_blank_shift_column():
    with get_db() as db:
        db.execute("UPDATE employees SET shift=? WHERE staff_id=?", ("", EVENING_STAFF))
    assert resolve_attendance_shift(_employee(EVENING_STAFF), _moment("16:30")) == "second"
    assert resolve_attendance_shift(_employee(EVENING_STAFF), _moment("09:00")) == "first"


def test_cutoff_no_longer_decides_for_assigned_evening_staff():
    """Whatever HR sets the cutoff to, an assigned evening worker is second."""
    for cutoff in ("15:00", "16:00", "17:00"):
        save_shift_rules("08:30", "16:00", "16:00", "22:00", cutoff, 0)
        assert resolve_attendance_shift(_employee(EVENING_STAFF), _moment("15:56")) == "second"
        assert _late_for(EVENING_STAFF, "15:56") == 0
