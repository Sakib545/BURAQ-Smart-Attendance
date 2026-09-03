"""v9.24 — configurable shift rules, duty precedence and manual-only overtime."""
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("DATABASE_PATH", "/tmp/buraq_v9_24_test.db")
os.environ.setdefault("REQUIRE_SECURE_SECRETS", "false")
os.environ.setdefault("ALLOW_TEMP_DB_FALLBACK", "false")
os.environ.setdefault("SESSION_SECRET", "test-session-secret-01234567890123456789")
os.environ.setdefault("CONFIG_ENCRYPTION_KEY", "test-config-secret-0123456789012345678")

# A clean file keeps every run deterministic; production databases are never
# touched here because DATABASE_PATH points at a temporary SQLite file.
import pytest
from fastapi.testclient import TestClient

from app.main import app, APP_VERSION
from app.database import get_db
from app import shift_rules
from app.services import (
    approve_pending_attendance,
    automatic_attendance_shift,
    duty_window,
    shift_times,
)

DEFAULT_RULES = dict(shift_rules.DEFAULTS)


@pytest.fixture(autouse=True)
def running_app():
    """Every test runs against an initialised database, rules reset to default."""
    with TestClient(app) as client:
        shift_rules.save_shift_rules(
            DEFAULT_RULES[shift_rules.FIRST_START_KEY], DEFAULT_RULES[shift_rules.FIRST_END_KEY],
            DEFAULT_RULES[shift_rules.SECOND_START_KEY], DEFAULT_RULES[shift_rules.SECOND_END_KEY],
            DEFAULT_RULES[shift_rules.CUTOFF_KEY], DEFAULT_RULES[shift_rules.GRACE_KEY],
        )
        yield client


def _employee(staff_id: str, shift: str = "morning"):
    with get_db() as db:
        db.execute("DELETE FROM employees WHERE staff_id=?", (staff_id,))
        db.execute(
            "INSERT INTO employees(staff_id,name,shift,registration_status) VALUES(?,?,?,?)",
            (staff_id, f"Test {staff_id}", shift, "approved"),
        )
        return db.execute("SELECT * FROM employees WHERE staff_id=?", (staff_id,)).fetchone()


def test_first_shift_default_is_0830_to_1600():
    start, end = shift_times("morning")
    assert start.strftime("%H:%M") == "08:30"
    assert end.strftime("%H:%M") == "16:00"


def test_second_shift_default_is_1600_to_2200():
    start, end = shift_times("evening")
    assert start.strftime("%H:%M") == "16:00"
    assert end.strftime("%H:%M") == "22:00"


def test_second_shift_detection_uses_configured_cutoff():
    assert automatic_attendance_shift("2026-08-20T08:45:00+06:00") == "first"
    assert automatic_attendance_shift("2026-08-20T14:59:00+06:00") == "first"
    assert automatic_attendance_shift("2026-08-20T15:00:00+06:00") == "first"
    assert automatic_attendance_shift("2026-08-20T16:05:00+06:00") == "second"

    shift_rules.save_shift_rules("08:30", "16:00", "16:00", "22:00", "16:00", 0)
    assert automatic_attendance_shift("2026-08-20T15:30:00+06:00") == "first"
    assert automatic_attendance_shift("2026-08-20T16:00:00+06:00") == "second"


def test_saved_rules_persist_in_system_settings():
    shift_rules.save_shift_rules("09:15", "17:45", "17:45", "09:15", "14:30", 12)
    with get_db() as db:
        stored = {
            row["key"]: row["value"]
            for row in db.execute(
                "SELECT key,value FROM system_settings WHERE key LIKE 'shift_%'"
            ).fetchall()
        }
    assert stored[shift_rules.FIRST_START_KEY] == "09:15"
    assert stored[shift_rules.SECOND_END_KEY] == "09:15"
    assert stored[shift_rules.CUTOFF_KEY] == "14:30"
    assert stored[shift_rules.GRACE_KEY] == "12"

    # A fresh read of the rules (as a later month would do) sees the same values.
    rules = shift_rules.get_shift_rules()
    assert rules[shift_rules.FIRST_START_KEY] == "09:15"
    assert rules[shift_rules.FIRST_END_KEY] == "17:45"
    assert rules[shift_rules.GRACE_KEY] == 12
    assert shift_times("morning")[0].strftime("%H:%M") == "09:15"


def test_invalid_rule_values_never_blank_the_configuration():
    shift_rules.save_shift_rules("bad", "", "16:00", "22:00", "not-a-time", "abc")
    rules = shift_rules.get_shift_rules()
    assert rules[shift_rules.FIRST_START_KEY] == "08:30"
    assert rules[shift_rules.CUTOFF_KEY] == "16:00"
    assert rules[shift_rules.GRACE_KEY] == 0


def test_broken_v924_defaults_are_corrected_without_touching_custom_rules():
    from app.database import apply_feature_migrations

    with get_db() as db:
        db.execute("DELETE FROM schema_migrations WHERE version=?", ("v9.24.1-correct-shift-defaults",))
        for key, value in {
            shift_rules.FIRST_START_KEY: "08:30",
            shift_rules.FIRST_END_KEY: "16:00",
            shift_rules.SECOND_START_KEY: "16:00",
            shift_rules.SECOND_END_KEY: "10:00",
            shift_rules.CUTOFF_KEY: "15:00",
        }.items():
            db.execute(
                "INSERT INTO system_settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
    apply_feature_migrations()
    corrected = shift_rules.get_shift_rules()
    assert corrected[shift_rules.SECOND_END_KEY] == "22:00"
    assert corrected[shift_rules.CUTOFF_KEY] == "16:00"

    # A later customized combination does not match the broken generated set.
    shift_rules.save_shift_rules("09:00", "17:00", "17:00", "23:00", "16:30", 5)
    with get_db() as db:
        db.execute("DELETE FROM schema_migrations WHERE version=?", ("v9.24.1-correct-shift-defaults",))
    apply_feature_migrations()
    custom = shift_rules.get_shift_rules()
    assert custom[shift_rules.FIRST_START_KEY] == "09:00"
    assert custom[shift_rules.SECOND_END_KEY] == "23:00"
    assert custom[shift_rules.CUTOFF_KEY] == "16:30"


def test_weekly_duty_overrides_global_shift_rules():
    employee = _employee("TEST-RULE-WEEKLY")
    target = date(2026, 8, 24)  # Monday
    with get_db() as db:
        db.execute(
            "INSERT INTO duty_schedules(employee_id,weekday,start_time,end_time,break_minutes,office_name) "
            "VALUES(?,?,?,?,?,?)",
            (employee["id"], target.weekday(), "10:00", "18:00", 30, "BURAQ Office"),
        )
    start, end = duty_window(employee, target)
    assert start.strftime("%H:%M") == "10:00"
    assert end.strftime("%H:%M") == "18:00"

    # A day without weekly duty still falls back to the global First Shift.
    fallback_start, fallback_end = duty_window(employee, target + timedelta(days=1))
    assert fallback_start.strftime("%H:%M") == "08:30"
    assert fallback_end.strftime("%H:%M") == "16:00"


def test_custom_duty_overrides_weekly_duty_and_is_employee_scoped():
    first = _employee("TEST-RULE-CUSTOM-1")
    second = _employee("TEST-RULE-CUSTOM-2")
    target = date(2026, 8, 25)
    with get_db() as db:
        db.execute(
            "INSERT INTO duty_schedules(employee_id,weekday,start_time,end_time,break_minutes,office_name) "
            "VALUES(?,?,?,?,?,?)",
            (first["id"], target.weekday(), "10:00", "18:00", 30, "BURAQ Office"),
        )
        db.execute(
            "INSERT INTO custom_duties(employee_id,duty_date,start_time,end_time,break_minutes,office_name,note) "
            "VALUES(?,?,?,?,?,?,?)",
            (first["id"], target.isoformat(), "13:00", "21:00", 45, "BURAQ Office", "Special"),
        )
    start, end = duty_window(first, target)
    assert start.strftime("%H:%M") == "13:00"
    assert end.strftime("%H:%M") == "21:00"

    # The other employee keeps the global default: one save never leaks across.
    other_start, other_end = duty_window(second, target)
    assert other_start.strftime("%H:%M") == "08:30"
    assert other_end.strftime("%H:%M") == "16:00"


def test_overnight_duty_end_moves_to_the_next_day():
    employee = _employee("TEST-RULE-NIGHT")
    target = date(2026, 8, 26)
    with get_db() as db:
        db.execute(
            "INSERT INTO custom_duties(employee_id,duty_date,start_time,end_time,break_minutes,office_name,note) "
            "VALUES(?,?,?,?,?,?,?)",
            (employee["id"], target.isoformat(), "22:00", "06:00", 0, "BURAQ Office", "Night duty"),
        )
    start, end = duty_window(employee, target)
    assert start.date() == target
    assert end.date() == target + timedelta(days=1)


def test_late_grace_is_applied_before_late_minutes_are_recorded():
    assert shift_rules.apply_late_grace(0) == 0
    assert shift_rules.apply_late_grace(25) == 25
    shift_rules.save_shift_rules("08:30", "16:00", "16:00", "22:00", "16:00", 15)
    assert shift_rules.apply_late_grace(10) == 0
    assert shift_rules.apply_late_grace(15) == 0
    assert shift_rules.apply_late_grace(25) == 10


def _pending_selfie(employee_id: int, action: str, media_id: str, created_at: str) -> int:
    with get_db() as db:
        db.execute("DELETE FROM attendance_fingerprints WHERE media_id=?", (media_id,))
        db.execute(
            """INSERT INTO attendance_fingerprints(
                employee_id,action,media_id,image_data,latitude,longitude,distance_meters,
                phash,ahash,dhash,embedding,decision,review_status,face_score,duplicate_score,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (employee_id, action, media_id, "", 23.0, 90.0, 10.0,
             media_id, media_id, media_id, "[]", "accept", "pending", 0.9, 0.01, created_at),
        )
        return int(db.execute(
            "SELECT id FROM attendance_fingerprints WHERE media_id=?", (media_id,)
        ).fetchone()["id"])


def test_late_minutes_follow_duty_start_and_configured_grace():
    employee = _employee("TEST-RULE-LATE")
    shift_rules.save_shift_rules("08:30", "16:00", "16:00", "22:00", "16:00", 10)
    fingerprint = _pending_selfie(employee["id"], "check_in", "rule-late-media", "2026-08-27T08:55:00+06:00")
    assert approve_pending_attendance(fingerprint, "test-admin")
    with get_db() as db:
        row = db.execute(
            "SELECT late_minutes,attendance_shift FROM attendance WHERE employee_id=? AND work_date=?",
            (employee["id"], "2026-08-27"),
        ).fetchone()
    # 25 minutes late against the 08:30 First Shift start, minus 10 grace minutes.
    assert int(row["late_minutes"]) == 15
    assert row["attendance_shift"] == "first"


def test_checkout_without_checkin_is_rejected():
    employee = _employee("TEST-RULE-NOCHECKIN")
    fingerprint = _pending_selfie(employee["id"], "check_out", "rule-nocheckin-media", "2026-08-28T17:00:00+06:00")
    with pytest.raises(ValueError):
        approve_pending_attendance(fingerprint, "test-admin")
    with get_db() as db:
        rows = db.execute(
            "SELECT COUNT(*) c FROM attendance WHERE employee_id=?", (employee["id"],)
        ).fetchone()
    assert int(rows["c"]) == 0


def test_late_checkout_never_creates_automatic_overtime():
    employee = _employee("TEST-RULE-OT")
    check_in = _pending_selfie(employee["id"], "check_in", "rule-ot-in", "2026-08-29T08:30:00+06:00")
    assert approve_pending_attendance(check_in, "test-admin")
    check_out = _pending_selfie(employee["id"], "check_out", "rule-ot-out", "2026-08-29T19:45:00+06:00")
    assert approve_pending_attendance(check_out, "test-admin")
    with get_db() as db:
        row = db.execute(
            "SELECT check_in,check_out,overtime_minutes FROM attendance WHERE employee_id=? AND work_date=?",
            (employee["id"], "2026-08-29"),
        ).fetchone()
    # The real timestamps are preserved, the overtime stays zero.
    assert str(row["check_in"]).startswith("2026-08-29T08:30")
    assert str(row["check_out"]).startswith("2026-08-29T19:45")
    assert int(row["overtime_minutes"] or 0) == 0


def test_payroll_uses_only_manually_entered_overtime():
    from app.main import _calculate_employee_payroll

    employee = _employee("TEST-RULE-PAYROLL")
    month = "2026-07"
    with get_db() as db:
        db.execute(
            "INSERT INTO custom_duties(employee_id,duty_date,start_time,end_time,break_minutes,office_name,note) "
            "VALUES(?,?,?,?,?,?,?)",
            (employee["id"], "2026-07-01", "08:30", "16:00", 30, "BURAQ Office", "Regular duty"),
        )
        db.execute(
            "INSERT INTO attendance(employee_id,work_date,check_in,check_out,attendance_shift,late_minutes) "
            "VALUES(?,?,?,?,?,?)",
            (employee["id"], "2026-07-01", "2026-07-01T08:30:00+06:00", "2026-07-01T20:00:00+06:00", "first", 0),
        )

    without_manual = _calculate_employee_payroll(employee["id"], month, 30000, 100)
    assert without_manual["overtime_hours"] == 0
    assert without_manual["overtime_amount"] == 0
    assert without_manual["overtime_mode"] == "manual"

    with_manual = _calculate_employee_payroll(employee["id"], month, 30000, 100, "manual", 3)
    assert with_manual["overtime_hours"] == 3
    assert with_manual["overtime_amount"] == 300


def test_no_completed_duty_means_no_full_basic_salary():
    from app.main import _calculate_employee_payroll

    employee = _employee("TEST-RULE-NODUTY")
    result = _calculate_employee_payroll(employee["id"], "2026-07", 30000, 0)
    assert result["earned_basic_salary"] == 0
    assert result["net_salary"] == 0


def test_break_minutes_reduce_payable_duty_time():
    from app.main import _calculate_employee_payroll

    employee = _employee("TEST-RULE-BREAK")
    with get_db() as db:
        db.execute(
            "INSERT INTO custom_duties(employee_id,duty_date,start_time,end_time,break_minutes,office_name,note) "
            "VALUES(?,?,?,?,?,?,?)",
            (employee["id"], "2026-07-02", "08:30", "16:00", 60, "BURAQ Office", "Regular duty"),
        )
    result = _calculate_employee_payroll(employee["id"], "2026-07", 30000, 0)
    # 7h30m duty minus a 60 minute break.
    assert result["payable_duty_minutes"] == 390


def test_existing_data_survives_initialisation_and_migration():
    from app.database import apply_feature_migrations, init_db

    employee = _employee("TEST-RULE-KEEP")
    with get_db() as db:
        db.execute(
            "INSERT INTO attendance(employee_id,work_date,check_in,attendance_shift,late_minutes) VALUES(?,?,?,?,?)",
            (employee["id"], "2026-06-01", "2026-06-01T08:30:00+06:00", "first", 4),
        )
    shift_rules.save_shift_rules("09:00", "17:00", "17:00", "09:00", "14:00", 5)

    init_db()
    apply_feature_migrations()

    with get_db() as db:
        kept = db.execute(
            "SELECT check_in,late_minutes FROM attendance WHERE employee_id=? AND work_date=?",
            (employee["id"], "2026-06-01"),
        ).fetchone()
        staff = db.execute(
            "SELECT staff_id FROM employees WHERE id=?", (employee["id"],)
        ).fetchone()
    assert staff["staff_id"] == "TEST-RULE-KEEP"
    assert int(kept["late_minutes"]) == 4
    assert shift_rules.get_shift_rules()[shift_rules.FIRST_START_KEY] == "09:00"


def test_health_and_ready_report_the_new_version(running_app):
    health = running_app.get("/health")
    assert health.status_code == 200
    assert health.json()["version"] == APP_VERSION
    assert health.json()["status"] == "ok"

    ready = running_app.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["database_ok"] is True


def test_duty_page_shows_shift_rules_card(running_app):
    setup = running_app.post(
        "/setup",
        data={"email": "admin@buraq.com", "password": "password123", "confirm_password": "password123"},
        follow_redirects=False,
    )
    if setup.status_code == 403:
        login = running_app.post(
            "/login",
            data={"email": "admin@buraq.com", "password": "password123"},
            follow_redirects=False,
        )
        assert login.status_code == 303

    page = running_app.get("/duty")
    assert page.status_code == 200
    assert "Shift Rules" in page.text
    assert "Overtime: Manual only" in page.text
    assert "/duty/shift-rules" in page.text

    saved = running_app.post(
        "/duty/shift-rules",
        data={"first_start": "08:45", "first_end": "16:30", "second_start": "16:30",
              "second_end": "10:30", "second_cutoff": "15:30", "late_grace_minutes": 7},
        follow_redirects=False,
    )
    assert saved.status_code == 303
    assert saved.headers["location"] == "/duty?saved=shift"
    rules = shift_rules.get_shift_rules()
    assert rules[shift_rules.FIRST_START_KEY] == "08:45"
    assert rules[shift_rules.GRACE_KEY] == 7
