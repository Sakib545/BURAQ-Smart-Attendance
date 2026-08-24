"""v9.26 — monthly duty performance scoring and WhatsApp notices."""
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("DATABASE_PATH", "/tmp/buraq_feature_test.db")
os.environ.setdefault("REQUIRE_SECURE_SECRETS", "false")
os.environ.setdefault("ALLOW_TEMP_DB_FALLBACK", "false")
os.environ.setdefault("SESSION_SECRET", "test-session-secret-01234567890123456789")
os.environ.setdefault("CONFIG_ENCRYPTION_KEY", "test-config-secret-0123456789012345678")

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import main as main_module
from app.database import get_db
from app import performance

STAR = "TEST-PERF-STAR"
WEAK = "TEST-PERF-WEAK"
NEW = "TEST-PERF-NEW"
ALL_STAFF = (STAR, WEAK, NEW)


def _last_month() -> str:
    first_of_this = date.today().replace(day=1)
    return (first_of_this - timedelta(days=1)).strftime("%Y-%m")


PERIOD = _last_month()


def _seed(staff_id: str, name: str, worked_days: int, late_per_day: int,
          scheduled_days: int, incomplete_days: int = 0):
    """Give an employee a weekday duty roster and a month of attendance."""
    first = date.fromisoformat(PERIOD + "-01")
    with get_db() as db:
        row = db.execute("SELECT id FROM employees WHERE staff_id=?", (staff_id,)).fetchone()
        if row:
            db.execute("DELETE FROM attendance WHERE employee_id=?", (row["id"],))
            db.execute("DELETE FROM duty_schedules WHERE employee_id=?", (row["id"],))
            db.execute("DELETE FROM performance_notices WHERE employee_id=?", (row["id"],))
        db.execute("DELETE FROM employees WHERE staff_id=?", (staff_id,))
        db.execute(
            "INSERT INTO employees(staff_id,name,shift,registration_status,is_active,whatsapp_phone) "
            "VALUES(?,?,?,?,?,?)",
            (staff_id, name, "morning", "approved", True, "88017" + staff_id[-6:].replace("-", "0")))
        employee_id = db.execute("SELECT id FROM employees WHERE staff_id=?", (staff_id,)).fetchone()["id"]

        # Duty on the first `scheduled_days` days of the month, via custom duty
        # so the roster is exact regardless of weekday.
        for offset in range(scheduled_days):
            day = first + timedelta(days=offset)
            db.execute(
                "INSERT INTO custom_duties(employee_id,duty_date,start_time,end_time,break_minutes,is_active) "
                "VALUES(?,?,?,?,?,?)",
                (employee_id, day.isoformat(), "09:00", "17:00", 0, True))

        for offset in range(worked_days):
            day = first + timedelta(days=offset)
            complete = offset >= incomplete_days
            db.execute(
                "INSERT INTO attendance(employee_id,work_date,check_in,check_out,late_minutes,status,source) "
                "VALUES(?,?,?,?,?,?,?)",
                (employee_id, day.isoformat(), f"{day.isoformat()}T09:00:00+06:00",
                 f"{day.isoformat()}T17:00:00+06:00" if complete else None,
                 late_per_day, "present", "test"))
    return employee_id


@pytest.fixture(autouse=True)
def seeded():
    with TestClient(app):
        with get_db() as db:
            db.execute("DELETE FROM custom_duties WHERE duty_date LIKE ?", (PERIOD + "-%",))
        _seed(STAR, "সেরা কর্মী", worked_days=20, late_per_day=0, scheduled_days=20)
        _seed(WEAK, "দুর্বল মাস", worked_days=8, late_per_day=25, scheduled_days=20)
        _seed(NEW, "নতুন কর্মী", worked_days=3, late_per_day=0, scheduled_days=3)
        yield
        with get_db() as db:
            for staff_id in ALL_STAFF:
                row = db.execute("SELECT id FROM employees WHERE staff_id=?", (staff_id,)).fetchone()
                if not row:
                    continue
                db.execute("DELETE FROM attendance WHERE employee_id=?", (row["id"],))
                db.execute("DELETE FROM custom_duties WHERE employee_id=?", (row["id"],))
                db.execute("DELETE FROM performance_notices WHERE employee_id=?", (row["id"],))
                db.execute("DELETE FROM employees WHERE id=?", (row["id"],))


def _row(staff_id: str):
    return next(r for r in performance.monthly_ranking(PERIOD) if r["staff_id"] == staff_id)


# --- scoring ----------------------------------------------------------------

def test_perfect_month_scores_100():
    scored = performance.score_from_metrics(
        {"scheduled": 20, "worked": 20, "paid_leave": 0, "late_minutes": 0, "incomplete_dates": []})
    assert scored["score"] == 100.0
    assert scored["eligible"] is True


def test_approved_leave_does_not_reduce_the_score():
    """18 worked + 2 approved leave must score the same as 20 worked."""
    with_leave = performance.score_from_metrics(
        {"scheduled": 20, "worked": 18, "paid_leave": 2, "late_minutes": 0, "incomplete_dates": []})
    without = performance.score_from_metrics(
        {"scheduled": 18, "worked": 18, "paid_leave": 0, "late_minutes": 0, "incomplete_dates": []})
    assert with_leave["score"] == without["score"] == 100.0


def test_lateness_reduces_only_the_punctuality_component():
    scored = performance.score_from_metrics(
        {"scheduled": 20, "worked": 20, "paid_leave": 0,
         "late_minutes": 20 * performance.LATE_ZERO_MINUTES, "incomplete_dates": []})
    assert scored["attendance"] == performance.WEIGHT_ATTENDANCE
    assert scored["punctuality"] == 0.0
    assert scored["completeness"] == performance.WEIGHT_COMPLETENESS


def test_missing_checkouts_reduce_completeness():
    scored = performance.score_from_metrics(
        {"scheduled": 10, "worked": 10, "paid_leave": 0, "late_minutes": 0,
         "incomplete_dates": ["d1", "d2"]})
    assert scored["completeness"] < performance.WEIGHT_COMPLETENESS


def test_month_with_no_expected_days_is_not_eligible():
    scored = performance.score_from_metrics(
        {"scheduled": 0, "worked": 0, "paid_leave": 0, "late_minutes": 0, "incomplete_dates": []})
    assert scored["score"] == 0.0 and scored["eligible"] is False


def test_short_tenure_is_not_eligible_even_when_perfect():
    scored = performance.score_from_metrics(
        {"scheduled": performance.MIN_SCHEDULED_DAYS - 1, "worked": performance.MIN_SCHEDULED_DAYS - 1,
         "paid_leave": 0, "late_minutes": 0, "incomplete_dates": []})
    assert scored["score"] == 100.0
    assert scored["eligible"] is False


# --- ranking ----------------------------------------------------------------

def test_ranking_puts_the_strong_month_first():
    star, weak = _row(STAR), _row(WEAK)
    assert star["rank_position"] == 1
    assert star["score"] > weak["score"]
    assert star["suggested"] == "star"


def test_weak_month_is_offered_a_private_coaching_note():
    assert _row(WEAK)["suggested"] == "coaching"


def test_new_joiner_is_ineligible_and_gets_no_notice():
    new = _row(NEW)
    assert new["eligible"] is False
    assert new["rank_position"] == 0
    assert new["suggested"] is None


def test_top_rank_below_star_threshold_gets_no_crown():
    assert performance.suggested_notice(75.0, 1, True) is None
    assert performance.suggested_notice(95.0, 1, True) == "star"
    assert performance.suggested_notice(95.0, 2, True) == "good"


# --- messages ---------------------------------------------------------------

def test_star_message_names_the_month_and_numbers():
    message = performance.build_message(_row(STAR), "star", PERIOD)
    assert "অভিনন্দন" in message
    assert performance.month_label(PERIOD) in message
    assert "সেরা কর্মী" in message


def test_coaching_message_is_factual_and_never_labels_the_person():
    message = performance.build_message(_row(WEAK), "coaching", PERIOD)
    # It reports numbers and invites a conversation...
    assert "উপস্থিত" in message
    assert "HR" in message
    # ...and carries no verdict, ranking or comparison.
    for banned in ("খারাপ", "worst", "bad", "সর্বনিম্ন", "rank", "#"):
        assert banned.lower() not in message.lower()


def test_coaching_message_mentions_correction_route():
    message = performance.build_message(_row(WEAK), "coaching", PERIOD)
    assert "Correction" in message or "ভুল" in message


# --- sending ----------------------------------------------------------------

def _login(client):
    setup = client.post("/setup", data={"email": "admin@buraq.com", "password": "password123",
                                        "confirm_password": "password123"}, follow_redirects=False)
    if setup.status_code == 403:
        login = client.post("/login", data={"email": "admin@buraq.com", "password": "password123"},
                            follow_redirects=False)
        assert login.status_code == 303


def test_page_renders_ranking():
    with TestClient(app) as client:
        _login(client)
        page = client.get(f"/performance-awards?month={PERIOD}")
        assert page.status_code == 200
        assert "Monthly Performance" in page.text
        assert STAR in page.text


def test_nothing_is_sent_until_hr_presses_send():
    performance.monthly_ranking(PERIOD)
    with get_db() as db:
        count = db.execute("SELECT COUNT(*) c FROM performance_notices WHERE period=?",
                           (PERIOD,)).fetchone()["c"]
    assert count == 0


def test_send_records_the_notice_once(monkeypatch):
    async def delivered(_phone, _message):
        return {"sent": True}

    monkeypatch.setattr(main_module, "send_text", delivered)
    employee_id = _row(STAR)["employee_id"]
    with TestClient(app) as client:
        _login(client)
        first = client.post("/performance-awards/send",
                            data={"employee_id": employee_id, "period": PERIOD, "notice_type": "star"},
                            follow_redirects=False)
        assert first.status_code == 303 and "saved=1" in first.headers["location"]
        second = client.post("/performance-awards/send",
                             data={"employee_id": employee_id, "period": PERIOD, "notice_type": "star"},
                             follow_redirects=False)
        assert "error=duplicate" in second.headers["location"]

    with get_db() as db:
        rows = db.execute("SELECT * FROM performance_notices WHERE employee_id=? AND period=?",
                          (employee_id, PERIOD)).fetchall()
    assert len(rows) == 1
    assert rows[0]["notice_type"] == "star"
    assert rows[0]["message"]


def test_failed_delivery_is_not_marked_sent_and_can_retry(monkeypatch):
    async def failed(_phone, _message):
        return {"sent": False, "reason": "temporary test failure"}

    monkeypatch.setattr(main_module, "send_text", failed)
    employee_id = _row(STAR)["employee_id"]
    with TestClient(app) as client:
        _login(client)
        response = client.post(
            "/performance-awards/send",
            data={"employee_id": employee_id, "period": PERIOD, "notice_type": "star"},
            follow_redirects=False,
        )
    assert response.status_code == 303
    assert "error=send" in response.headers["location"]
    with get_db() as db:
        count = db.execute(
            "SELECT COUNT(*) c FROM performance_notices WHERE employee_id=? AND period=?",
            (employee_id, PERIOD),
        ).fetchone()["c"]
    assert count == 0


def test_modified_notice_category_is_rejected(monkeypatch):
    async def delivered(_phone, _message):
        return {"sent": True}

    monkeypatch.setattr(main_module, "send_text", delivered)
    employee_id = _row(STAR)["employee_id"]
    with TestClient(app) as client:
        _login(client)
        response = client.post(
            "/performance-awards/send",
            data={"employee_id": employee_id, "period": PERIOD, "notice_type": "good"},
            follow_redirects=False,
        )
    assert response.status_code == 409


def test_unknown_notice_type_is_rejected():
    employee_id = _row(STAR)["employee_id"]
    with TestClient(app) as client:
        _login(client)
        response = client.post("/performance-awards/send",
                               data={"employee_id": employee_id, "period": PERIOD,
                                     "notice_type": "worst_employee"},
                               follow_redirects=False)
        assert response.status_code == 400
