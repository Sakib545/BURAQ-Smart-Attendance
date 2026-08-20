"""v9.25 — employee-side leave requests over WhatsApp."""
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("DATABASE_PATH", "/tmp/buraq_leave_test.db")
os.environ.setdefault("REQUIRE_SECURE_SECRETS", "false")
os.environ.setdefault("ALLOW_TEMP_DB_FALLBACK", "false")
os.environ.setdefault("SESSION_SECRET", "test-session-secret-01234567890123456789")
os.environ.setdefault("CONFIG_ENCRYPTION_KEY", "test-config-secret-0123456789012345678")

Path(os.environ["DATABASE_PATH"]).unlink(missing_ok=True)

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import get_db
from app.services import process, clear_state, state
from app import leave_flow

PHONE = "8801799000111"
STAFF_ID = "TEST-LEAVE-001"


@pytest.fixture(autouse=True)
def employee():
    with TestClient(app):
        with get_db() as db:
            row = db.execute("SELECT id FROM employees WHERE staff_id=?", (STAFF_ID,)).fetchone()
            if row:
                db.execute("DELETE FROM leave_requests WHERE employee_id=?", (row["id"],))
                # Approved leave writes attendance rows that reference the
                # employee; they must go first or the DELETE below hits the FK.
                db.execute("DELETE FROM attendance WHERE employee_id=?", (row["id"],))
            db.execute("DELETE FROM employees WHERE staff_id=?", (STAFF_ID,))
            db.execute(
                "INSERT INTO employees(staff_id,name,shift,registration_status,whatsapp_phone) "
                "VALUES(?,?,?,?,?)",
                (STAFF_ID, "Leave Tester", "morning", "approved", PHONE),
            )
        clear_state(PHONE)
        yield
        clear_state(PHONE)


def _requests():
    with get_db() as db:
        return db.execute(
            "SELECT l.* FROM leave_requests l JOIN employees e ON e.id=l.employee_id "
            "WHERE e.staff_id=? ORDER BY l.id DESC", (STAFF_ID,)).fetchall()


# --- date parsing -----------------------------------------------------------

def test_parse_iso_and_slash_dates():
    assert leave_flow.parse_leave_date("2026-08-25") == date(2026, 8, 25)
    assert leave_flow.parse_leave_date("25/08/2026") == date(2026, 8, 25)
    assert leave_flow.parse_leave_date("25-08-2026") == date(2026, 8, 25)


def test_parse_bengali_digits_and_relative_words():
    base = date(2026, 8, 20)
    assert leave_flow.parse_leave_date("২৫/০৮/২০২৬") == date(2026, 8, 25)
    assert leave_flow.parse_leave_date("আজ", base) == base
    assert leave_flow.parse_leave_date("আগামীকাল", base) == date(2026, 8, 21)


def test_parse_rejects_garbage():
    assert leave_flow.parse_leave_date("next week") is None
    assert leave_flow.parse_leave_date("") is None


def test_leave_type_aliases():
    assert leave_flow.parse_leave_type("2") == "Sick"
    assert leave_flow.parse_leave_type("SICK") == "Sick"
    assert leave_flow.parse_leave_type("অসুস্থ") == "Sick"
    assert leave_flow.parse_leave_type("holiday") is None


# --- happy path -------------------------------------------------------------

def test_full_request_is_saved_as_pending():
    start = date.today() + timedelta(days=3)
    end = start + timedelta(days=2)

    assert "ছুটির আবেদন" in process(PHONE, "leave")
    assert state(PHONE)["state"] == "leave_type"

    process(PHONE, "2")
    assert state(PHONE)["state"] == "leave_start:Sick"

    process(PHONE, start.isoformat())
    process(PHONE, end.isoformat())
    assert state(PHONE)["state"].startswith("leave_reason:Sick")

    preview = process(PHONE, "জ্বর ও কাশি")
    assert "Sick" in preview and "3 দিন" in preview

    done = process(PHONE, "YES")
    assert "জমা হয়েছে" in done
    assert state(PHONE) is None  # state cleared after submit

    rows = _requests()
    assert len(rows) == 1
    assert rows[0]["leave_type"] == "Sick"
    assert rows[0]["status"] == "pending"
    assert rows[0]["requested_by"] == "employee:whatsapp"
    assert rows[0]["reason"] == "জ্বর ও কাশি"


def test_single_day_leave_uses_same_date_twice():
    day = (date.today() + timedelta(days=5)).isoformat()
    process(PHONE, "leave"); process(PHONE, "Casual")
    process(PHONE, day); process(PHONE, day)
    process(PHONE, "ব্যক্তিগত কাজ"); process(PHONE, "yes")
    rows = _requests()
    assert rows[0]["start_date"] == day and rows[0]["end_date"] == day


# --- validation -------------------------------------------------------------

def test_end_before_start_is_rejected_and_step_is_retried():
    start = date.today() + timedelta(days=10)
    process(PHONE, "leave"); process(PHONE, "Casual"); process(PHONE, start.isoformat())
    reply = process(PHONE, (start - timedelta(days=2)).isoformat())
    assert "শেষ তারিখ" in reply
    # Still waiting on the end date rather than advancing with bad data.
    assert state(PHONE)["state"].startswith("leave_end:")
    assert _requests() == []


def test_far_past_start_is_rejected():
    old = date.today() - timedelta(days=leave_flow.MAX_PAST_DAYS + 5)
    process(PHONE, "leave"); process(PHONE, "Sick")
    reply = process(PHONE, old.isoformat())
    assert "পুরোনো" in reply
    assert _requests() == []


def test_duration_cap_is_enforced():
    start = date.today() + timedelta(days=1)
    end = start + timedelta(days=leave_flow.MAX_DURATION_DAYS + 1)
    process(PHONE, "leave"); process(PHONE, "Annual"); process(PHONE, start.isoformat())
    reply = process(PHONE, end.isoformat())
    assert str(leave_flow.MAX_DURATION_DAYS) in reply
    assert _requests() == []


def test_overlapping_request_is_blocked():
    start = date.today() + timedelta(days=20)
    end = start + timedelta(days=3)
    process(PHONE, "leave"); process(PHONE, "Casual")
    process(PHONE, start.isoformat()); process(PHONE, end.isoformat())
    process(PHONE, "প্রথম আবেদন"); process(PHONE, "yes")
    assert len(_requests()) == 1

    # Second request overlapping the middle of the first one.
    process(PHONE, "leave"); process(PHONE, "Casual")
    reply = process(PHONE, (start + timedelta(days=1)).isoformat())
    assert "মিলে যাচ্ছে" in reply
    assert len(_requests()) == 1


def test_unknown_type_reprompts_without_advancing():
    process(PHONE, "leave")
    reply = process(PHONE, "holiday")
    assert "বুঝতে পারিনি" in reply
    assert state(PHONE)["state"] == "leave_type"


def test_short_reason_is_rejected():
    day = (date.today() + timedelta(days=4)).isoformat()
    process(PHONE, "leave"); process(PHONE, "Casual"); process(PHONE, day); process(PHONE, day)
    reply = process(PHONE, "ok")
    assert "৩ অক্ষরের" in reply
    assert state(PHONE)["state"].startswith("leave_reason:")


def test_cancel_aborts_and_saves_nothing():
    process(PHONE, "leave"); process(PHONE, "Sick")
    reply = process(PHONE, "cancel")
    assert "বাতিল" in reply
    assert state(PHONE) is None
    assert _requests() == []


def test_confirm_step_requires_yes():
    day = (date.today() + timedelta(days=6)).isoformat()
    process(PHONE, "leave"); process(PHONE, "Casual"); process(PHONE, day); process(PHONE, day)
    process(PHONE, "পারিবারিক কারণ")
    reply = process(PHONE, "maybe")
    assert "YES" in reply
    assert _requests() == []


# --- reporting and approval -------------------------------------------------

def test_my_leave_lists_status():
    assert "কোনো ছুটির আবেদন নেই" in process(PHONE, "my leave")
    day = (date.today() + timedelta(days=7)).isoformat()
    process(PHONE, "leave"); process(PHONE, "Annual"); process(PHONE, day); process(PHONE, day)
    process(PHONE, "ভ্রমণ"); process(PHONE, "yes")
    report = process(PHONE, "my leave")
    assert "Annual" in report and "pending" in report


def test_reason_with_colon_survives_the_state_machine():
    """Reasons are base64-encoded in state, so ':' must not corrupt parsing."""
    day = (date.today() + timedelta(days=8)).isoformat()
    process(PHONE, "leave"); process(PHONE, "Casual"); process(PHONE, day); process(PHONE, day)
    process(PHONE, "ডাক্তার: সকাল ১০:৩০ appointment"); process(PHONE, "yes")
    assert _requests()[0]["reason"] == "ডাক্তার: সকাল ১০:৩০ appointment"


def test_approval_writes_leave_attendance_rows():
    start = date.today() + timedelta(days=12)
    end = start + timedelta(days=1)
    process(PHONE, "leave"); process(PHONE, "Casual")
    process(PHONE, start.isoformat()); process(PHONE, end.isoformat())
    process(PHONE, "পারিবারিক অনুষ্ঠান"); process(PHONE, "yes")
    request_id = _requests()[0]["id"]

    with TestClient(app) as client:
        setup = client.post(
            "/setup",
            data={"email": "admin@buraq.com", "password": "password123",
                  "confirm_password": "password123"},
            follow_redirects=False,
        )
        if setup.status_code == 403:
            login = client.post(
                "/login",
                data={"email": "admin@buraq.com", "password": "password123"},
                follow_redirects=False,
            )
            assert login.status_code == 303
        client.post(f"/leave/{request_id}/approve", follow_redirects=False)

    rows = _requests()
    assert rows[0]["status"] == "approved"
    with get_db() as db:
        marked = db.execute(
            "SELECT COUNT(*) c FROM attendance a JOIN employees e ON e.id=a.employee_id "
            "WHERE e.staff_id=? AND a.status='leave'", (STAFF_ID,)).fetchone()["c"]
    assert marked == 2


def test_decision_message_mentions_dates():
    row = {"leave_type": "Sick", "start_date": "2026-09-01", "end_date": "2026-09-03"}
    approved = leave_flow.decision_message(row, "approved")
    rejected = leave_flow.decision_message(row, "rejected")
    assert "অনুমোদিত হয়েছে" in approved and "2026-09-01" in approved
    assert "অনুমোদিত হয়নি" in rejected
