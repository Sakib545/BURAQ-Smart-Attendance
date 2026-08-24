"""Regression coverage for assigned shifts and legacy attendance correction."""

import os
from datetime import datetime

os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("DATABASE_PATH", "/tmp/buraq_shift_correction_test.db")
os.environ.setdefault("REQUIRE_SECURE_SECRETS", "false")
os.environ.setdefault("ALLOW_TEMP_DB_FALLBACK", "false")
os.environ.setdefault("SESSION_SECRET", "test-session-secret-01234567890123456789")
os.environ.setdefault("CONFIG_ENCRYPTION_KEY", "test-config-secret-0123456789012345678")

from app.database import apply_feature_migrations, get_db, init_db
from app.services import automatic_attendance_shift


def test_shift_assignment_and_existing_records_are_corrected():
    init_db()
    day = "2026-08-20"
    staff_ids = ("SHIFT-FIRST", "SHIFT-SECOND", "SHIFT-EARLY", "SHIFT-CUSTOM")

    with get_db() as db:
        for staff_id in staff_ids:
            old = db.execute("SELECT id FROM employees WHERE staff_id=?", (staff_id,)).fetchone()
            if old:
                db.execute("DELETE FROM attendance WHERE employee_id=?", (old["id"],))
                db.execute("DELETE FROM custom_duties WHERE employee_id=?", (old["id"],))
                db.execute("DELETE FROM employees WHERE id=?", (old["id"],))
        db.execute("DELETE FROM schema_migrations WHERE version=?",
                   ("v9.26.1-correct-existing-attendance-shifts",))

        for staff_id, employee_shift, checked_in in (
            ("SHIFT-FIRST", "morning", "08:35"),
            ("SHIFT-SECOND", "morning", "16:05"),
            ("SHIFT-EARLY", "evening", "15:50"),
            ("SHIFT-CUSTOM", "morning", "15:50"),
        ):
            db.execute("INSERT INTO employees(staff_id,name,shift) VALUES(?,?,?)",
                       (staff_id, staff_id, employee_shift))
            employee_id = db.execute("SELECT id FROM employees WHERE staff_id=?",
                                     (staff_id,)).fetchone()["id"]
            db.execute("INSERT INTO attendance(employee_id,work_date,check_in,attendance_shift) "
                       "VALUES(?,?,?,'first')",
                       (employee_id, day, f"{day}T{checked_in}:00+06:00"))
            if staff_id == "SHIFT-CUSTOM":
                db.execute("INSERT INTO custom_duties(employee_id,duty_date,start_time,end_time) "
                           "VALUES(?,?,?,?)", (employee_id, day, "16:00", "22:00"))

    apply_feature_migrations()

    with get_db() as db:
        rows = db.execute("SELECT e.staff_id,a.attendance_shift FROM attendance a "
                          "JOIN employees e ON e.id=a.employee_id "
                          "WHERE e.staff_id LIKE 'SHIFT-%'").fetchall()
        actual = {row["staff_id"]: row["attendance_shift"] for row in rows}
        employee = db.execute("SELECT * FROM employees WHERE staff_id=?",
                              ("SHIFT-CUSTOM",)).fetchone()
        early_custom = automatic_attendance_shift(
            datetime.fromisoformat(f"{day}T15:50:00+06:00"), employee, db=db
        )

    assert actual == {
        "SHIFT-FIRST": "first", "SHIFT-SECOND": "second",
        "SHIFT-EARLY": "second", "SHIFT-CUSTOM": "second",
    }
    assert early_custom == "second"
