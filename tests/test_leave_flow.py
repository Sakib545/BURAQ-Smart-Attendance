"""v9.26 guided WhatsApp leave request tests."""
import os
from datetime import date, datetime, timedelta

import pytest

os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("DATABASE_PATH", "/tmp/buraq_feature_test.db")
os.environ.setdefault("REQUIRE_SECURE_SECRETS", "false")
os.environ.setdefault("ALLOW_TEMP_DB_FALLBACK", "false")

from app import leave_flow
from app.database import get_db, init_db

init_db(max_attempts=1)


PHONE = "8801700000099"
STAFF_ID = "TEST-LEAVE-001"


@pytest.fixture(autouse=True)
def employee():
    with get_db() as db:
        db.execute("DELETE FROM conversation_states WHERE phone=?", (PHONE,))
        old = db.execute("SELECT id FROM employees WHERE staff_id=?", (STAFF_ID,)).fetchone()
        if old:
            db.execute("DELETE FROM leave_requests WHERE employee_id=?", (old["id"],))
            db.execute("DELETE FROM employees WHERE id=?", (old["id"],))
        db.execute(
            "INSERT INTO employees(staff_id,name,registration_status,is_active,whatsapp_phone) "
            "VALUES(?,?,?,?,?)",
            (STAFF_ID, "Leave Test", "approved", True, PHONE),
        )
    yield
    with get_db() as db:
        row = db.execute("SELECT id FROM employees WHERE staff_id=?", (STAFF_ID,)).fetchone()
        if row:
            db.execute("DELETE FROM leave_requests WHERE employee_id=?", (row["id"],))
            db.execute("DELETE FROM employees WHERE id=?", (row["id"],))
        db.execute("DELETE FROM conversation_states WHERE phone=?", (PHONE,))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-08-25", date(2026, 8, 25)),
        ("25/08/2026", date(2026, 8, 25)),
        ("২৫/০৮/২০২৬", date(2026, 8, 25)),
        ("আজ", date(2026, 8, 20)),
        ("আগামীকাল", date(2026, 8, 21)),
    ],
)
def test_flexible_dates(raw, expected):
    assert leave_flow.parse_leave_date(raw, today=date(2026, 8, 20)) == expected


def test_invalid_ranges_are_blocked():
    today = date(2026, 8, 20)
    assert "৩০ দিনের" in leave_flow._date_error(today - timedelta(days=31), today=today)
    assert "আগে" in leave_flow._date_error(today, today - timedelta(days=1), today=today)
    assert "৬০" in leave_flow._date_error(today, today + timedelta(days=60), today=today)


def test_complete_guided_request_and_status(monkeypatch):
    monkeypatch.setattr(leave_flow, "now_local", lambda: datetime(2026, 8, 20, 12, 0))

    assert "শুরু" in leave_flow.handle_leave_message(PHONE, "ছুটি")
    assert "শুরুর তারিখ" in leave_flow.handle_leave_message(PHONE, "1")
    assert "শেষ তারিখ" in leave_flow.handle_leave_message(PHONE, "আগামীকাল")
    assert "কারণ" in leave_flow.handle_leave_message(PHONE, "21/08/2026")
    assert "YES" in leave_flow.handle_leave_message(PHONE, "পারিবারিক প্রয়োজন")
    assert "HR-এর কাছে" in leave_flow.handle_leave_message(PHONE, "YES")

    with get_db() as db:
        row = db.execute(
            "SELECT l.* FROM leave_requests l JOIN employees e ON e.id=l.employee_id "
            "WHERE e.staff_id=?", (STAFF_ID,),
        ).fetchone()
    assert row["leave_type"] == "Casual"
    assert row["start_date"] == "2026-08-21"
    assert row["end_date"] == "2026-08-21"
    assert row["requested_by"] == "employee:whatsapp"
    assert "Pending" in leave_flow.handle_leave_message(PHONE, "my leave")


def test_overlapping_pending_request_is_blocked(monkeypatch):
    monkeypatch.setattr(leave_flow, "now_local", lambda: datetime(2026, 8, 20, 12, 0))
    with get_db() as db:
        employee_id = db.execute("SELECT id FROM employees WHERE staff_id=?", (STAFF_ID,)).fetchone()["id"]
        db.execute(
            "INSERT INTO leave_requests(employee_id,leave_type,start_date,end_date,status,requested_by) "
            "VALUES(?,?,?,?,?,?)",
            (employee_id, "Sick", "2026-08-25", "2026-08-27", "pending", "employee:whatsapp"),
        )

    leave_flow.handle_leave_message(PHONE, "leave")
    leave_flow.handle_leave_message(PHONE, "Sick")
    leave_flow.handle_leave_message(PHONE, "25/08/2026")
    response = leave_flow.handle_leave_message(PHONE, "26/08/2026")
    assert "মিলে গেছে" in response
