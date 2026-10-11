"""Daily HR summary, overtime approval, loans and the notice board."""
import asyncio
import base64
import json
import os
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from app import daily_summary, loans, notices, overtime
from app.config import settings
from app.database import get_db
from app.main import app
from app.runtime import set_setting


def _client(session):
    client = TestClient(app)
    client.cookies.set("session", TimestampSigner(os.environ["SESSION_SECRET"]).sign(
        base64.b64encode(json.dumps(session).encode())).decode())
    return client


def _admin():
    return _client({"role": "super_admin", "user_name": "Tester", "admin": True})


def _viewer():
    return _client({"role": "viewer", "user_name": "V", "hr_id": 987654})


@pytest.fixture
def people():
    """Isolated staff for each test: everyone else is made inactive meanwhile."""
    stamp = int(time.time() * 1000) % 10000000
    with get_db() as c:
        before = [r["id"] for r in c.execute("SELECT id FROM employees WHERE is_active").fetchall()]
        c.execute("UPDATE employees SET is_active=?", (False,))
        ids = []
        for n, dept in enumerate(("Sales", "Sales", "Store", "Store", "Office")):
            staff_id = f"HT{stamp}{n}"
            c.execute("INSERT INTO employees(staff_id,name,department,is_active,whatsapp_phone,registration_status,fixed_salary,default_overtime_rate) "
                      "VALUES(?,?,?,?,?,?,?,?)", (staff_id, f"Person {n}", dept, True, f"8801{stamp:07d}{n}", "approved", 30000, 100))
            ids.append(c.execute("SELECT id FROM employees WHERE staff_id=?", (staff_id,)).fetchone()["id"])
    yield ids
    with get_db() as c:
        for table in ("overtime_requests", "loan_installments", "employee_loans", "payroll_records", "attendance",
                      "leave_requests", "custom_duties", "duty_schedules", "web_checkin_pins"):
            c.execute(f"DELETE FROM {table} WHERE employee_id IN ({','.join('?' * len(ids))})", ids)
        c.execute(f"DELETE FROM employees WHERE id IN ({','.join('?' * len(ids))})", ids)
        for employee_id in before:
            c.execute("UPDATE employees SET is_active=? WHERE id=?", (True, employee_id))
        c.execute("DELETE FROM notices")


def _sent(monkeypatch, ok=True):
    calls = []

    async def fake_text(to, body):
        calls.append(("text", to, body))
        return {"sent": ok}

    async def fake_template(to, name, values):
        calls.append(("template", to, name, values))
        return {"sent": True}

    monkeypatch.setattr("app.whatsapp.send_text", fake_text)
    monkeypatch.setattr("app.whatsapp.send_template", fake_template)
    return calls


# ------------------------------------------------------------ daily summary

def test_summary_sorts_staff_into_present_late_missing_leave_and_off(people):
    day = date(2026, 10, 12)  # a Monday
    present, late, missing, on_leave, off = people
    with get_db() as c:
        c.execute("INSERT INTO attendance(employee_id,work_date,check_in,late_minutes) VALUES(?,?,?,?)",
                  (present, day.isoformat(), f"{day}T09:00:00+06:00", 0))
        c.execute("INSERT INTO attendance(employee_id,work_date,check_in,late_minutes) VALUES(?,?,?,?)",
                  (late, day.isoformat(), f"{day}T09:25:00+06:00", 25))
        c.execute("INSERT INTO leave_requests(employee_id,leave_type,start_date,end_date,status) VALUES(?,?,?,?,?)",
                  (on_leave, "Sick", day.isoformat(), day.isoformat(), "approved"))
        # Weekly roster with no duty on Monday -> off today, not missing.
        c.execute("INSERT INTO duty_schedules(employee_id,weekday,start_time,end_time) VALUES(?,?,?,?)", (off, 2, "09:00", "17:00"))
        c.execute("INSERT INTO overtime_requests(employee_id,work_date,minutes,status,created_at) VALUES(?,?,?,?,?)",
                  (present, day.isoformat(), 30, "pending", 1))
    s = daily_summary.build_summary(day)
    assert [len(s[k]) for k in ("present", "late", "missing", "leave", "off")] == [2, 1, 1, 1, 1]
    assert s["missing"][0]["name"] == "Person 2" and s["pending"]["overtime"] == 1
    text = daily_summary.format_summary(s)
    assert "উপস্থিত: 2 জন (মোট 5)" in text and "Person 1" in text and "25 মি." in text
    assert "এখনো আসেনি: 1 জন" in text and "Overtime: 1" in text


def test_summary_is_sent_once_a_day_after_the_chosen_time(people, monkeypatch):
    calls = _sent(monkeypatch, ok=False)  # free text refused -> template fallback
    tz = ZoneInfo(settings.timezone)
    set_setting(daily_summary.ENABLED_KEY, "1")
    set_setting(daily_summary.PHONES_KEY, "8801711111111, +880 1722-222222")
    set_setting(daily_summary.TIME_KEY, "10:30")
    set_setting(daily_summary.LAST_SENT_KEY, "")
    try:
        assert not asyncio.run(daily_summary.run_summary_cycle(datetime(2026, 10, 12, 10, 0, tzinfo=tz)))
        assert asyncio.run(daily_summary.run_summary_cycle(datetime(2026, 10, 12, 10, 31, tzinfo=tz)))
        assert not asyncio.run(daily_summary.run_summary_cycle(datetime(2026, 10, 12, 11, 0, tzinfo=tz)))
        assert {call[1] for call in calls} == {"8801711111111", "8801722222222"}
        assert [c[0] for c in calls].count("template") == 2
        assert calls[1][2] == daily_summary.TEMPLATE and len(calls[1][3]) == 6
    finally:
        set_setting(daily_summary.ENABLED_KEY, "0")


def test_summary_page_needs_permission_and_saves(people, monkeypatch):
    _sent(monkeypatch)
    assert _viewer().get("/daily-summary").status_code == 403
    admin = _admin()
    assert "Today's message" in admin.get("/daily-summary").text
    r = admin.post("/daily-summary", data={"enabled": "1", "phones": "", "send_time": "09:00"}, follow_redirects=False)
    assert "error=" in r.headers["location"]
    admin.post("/daily-summary", data={"enabled": "1", "phones": "01711-111111", "send_time": "9:05"})
    from app.runtime import get_setting
    assert get_setting(daily_summary.TIME_KEY) == "09:05" and get_setting(daily_summary.PHONES_KEY) == "01711111111"
    r = admin.post("/daily-summary/send", follow_redirects=False)
    assert "Sent+to+1" in r.headers["location"]
    set_setting(daily_summary.ENABLED_KEY, "0")


# ---------------------------------------------------------------- overtime

def _duty_and_checkout(c, employee_id, day, check_out):
    c.execute("INSERT INTO custom_duties(employee_id,duty_date,start_time,end_time) VALUES(?,?,?,?)",
              (employee_id, day, "09:00", "17:00"))
    c.execute("INSERT INTO attendance(employee_id,work_date,check_in,check_out) VALUES(?,?,?,?)",
              (employee_id, day, f"{day}T09:00:00+06:00", f"{day}T{check_out}:00+06:00" if check_out else None))


def test_only_approved_overtime_actually_worked_is_payable(people):
    a, b, c_id, *_ = people
    admin = _admin()
    with get_db() as c:
        _duty_and_checkout(c, a, "2026-09-10", "18:30")   # 90 min after duty, 60 approved -> 60
        _duty_and_checkout(c, b, "2026-09-10", "17:20")   # 20 min after duty, 60 approved -> 20
        _duty_and_checkout(c, c_id, "2026-09-10", None)   # no check-out yet -> 0
        _duty_and_checkout(c, a, "2026-09-11", "19:00")   # worked late but nothing approved -> 0
    for employee_id in (a, b, c_id):
        r = admin.post("/overtime", data={"employee_id": employee_id, "work_date": "2026-09-10", "minutes": 60}, follow_redirects=False)
        assert "notice=" in r.headers["location"]
    assert overtime.approved_hours(a, "2026-09") == 1.0
    assert overtime.approved_hours(b, "2026-09") == round(20 / 60, 2)
    assert overtime.approved_hours(c_id, "2026-09") == 0
    page = admin.get("/overtime?month=2026-09").text
    assert "No check-out yet" in page and "1.00 h" in page
    # The payroll form starts from the approved hours.
    assert admin.get(f"/payroll/inputs?employee_id={a}&month=2026-09").json()["overtime_hours"] == 1.0
    assert _viewer().get("/overtime").status_code == 403


def test_employee_asks_and_hr_decides(people, monkeypatch):
    calls = _sent(monkeypatch)
    from app.web_checkin import set_pin
    employee_id = people[0]
    set_pin(employee_id, "4826")
    with get_db() as c:
        staff_id = c.execute("SELECT staff_id FROM employees WHERE id=?", (employee_id,)).fetchone()["staff_id"]
    me = TestClient(app)
    me.post("/me/login", data={"staff_id": staff_id, "pin": "4826"})
    today = overtime._today()
    assert "Overtime-এর আবেদন" in me.get("/me").text
    me.post("/me/overtime", data={"work_date": today.isoformat(), "minutes": 90, "reason": "Stock count"})
    me.post("/me/overtime", data={"work_date": (today - timedelta(days=30)).isoformat(), "minutes": 90, "reason": "x"})
    with get_db() as c:
        rows = c.execute("SELECT id,status,minutes FROM overtime_requests WHERE employee_id=?", (employee_id,)).fetchall()
    assert len(rows) == 1 and rows[0]["status"] == "pending"
    admin = _admin()
    assert "Stock count" in admin.get("/overtime").text
    admin.post(f"/overtime/{rows[0]['id']}/decide", data={"action": "approve", "minutes": 60})
    with get_db() as c:
        row = c.execute("SELECT status,minutes FROM overtime_requests WHERE id=?", (rows[0]["id"],)).fetchone()
    assert (row["status"], row["minutes"]) == ("approved", 60)
    assert any("মঞ্জুর" in call[2] for call in calls if call[0] == "text")


# ------------------------------------------------------------------- loans

def _payslip(c, employee_id, month, status, advance):
    c.execute("INSERT INTO payroll_records(employee_id,salary_month,payment_status,advance_amount) VALUES(?,?,?,?)",
              (employee_id, month, status, advance))
    loans.record_for_payroll(c, employee_id, month, advance)


def test_loan_installments_count_once_the_payslip_is_final(people):
    employee_id = people[0]
    admin = _admin()
    admin.post("/loans", data={"employee_id": employee_id, "kind": "loan", "start_month": "2026-07",
                               "principal": 2500, "installment": 1000})
    with get_db() as c:
        assert loans.due_for_month(c, employee_id, "2026-06") == []
        assert [a for _, a in loans.due_for_month(c, employee_id, "2026-07")] == [1000]
        _payslip(c, employee_id, "2026-07", "finalized", 1000)
        _payslip(c, employee_id, "2026-08", "draft", 1000)        # a draft does not count yet
        assert loans.loan_rows(c)[0]["repaid"] == 1000
        c.execute("UPDATE payroll_records SET payment_status='paid' WHERE employee_id=? AND salary_month='2026-08'", (employee_id,))
        assert [a for _, a in loans.due_for_month(c, employee_id, "2026-09")] == [500]   # only what is left
        _payslip(c, employee_id, "2026-09", "finalized", 700)     # extra 200 is a one-off advance
        row = loans.loan_rows(c)[0]
    assert (row["repaid"], row["remaining"], row["state"]) == (2500, 0, "closed")
    assert loans.total_due(employee_id, "2026-10") == 0
    assert "Repaid" in admin.get("/loans").text


def test_payroll_prefills_and_bulk_prepare_deducts_the_installment(people):
    employee_id = people[0]
    admin = _admin()
    admin.post("/loans", data={"employee_id": employee_id, "kind": "advance", "start_month": "2026-09",
                               "principal": 3000, "installment": 1500})
    inputs = admin.get(f"/payroll/inputs?employee_id={employee_id}&month=2026-09").json()
    assert inputs["advance_amount"] == 1500 and inputs["adjustment_reason"] == loans.LOAN_REASON
    admin.post("/payroll/bulk-prepare", data={"month": "2026-09"})
    with get_db() as c:
        slip = c.execute("SELECT advance_amount,adjustment_reason,payment_status FROM payroll_records "
                         "WHERE employee_id=? AND salary_month='2026-09'", (employee_id,)).fetchone()
        installment = c.execute("SELECT amount FROM loan_installments WHERE employee_id=? AND salary_month='2026-09'",
                                (employee_id,)).fetchone()
    assert float(slip["advance_amount"]) == 1500 and slip["adjustment_reason"] == loans.LOAN_REASON
    assert float(installment["amount"]) == 1500
    # Stopping a loan stops future deductions.
    with get_db() as c:
        loan_id = c.execute("SELECT id FROM employee_loans WHERE employee_id=?", (employee_id,)).fetchone()["id"]
    admin.post(f"/loans/{loan_id}/cancel")
    assert loans.total_due(employee_id, "2026-10") == 0
    assert _viewer().post("/loans", data={"employee_id": employee_id, "start_month": "2026-09",
                                          "principal": 1, "installment": 1}).status_code == 403


# ------------------------------------------------------------------ notices

def test_notice_reaches_the_right_staff_and_their_page(people, monkeypatch):
    calls = _sent(monkeypatch)
    admin = _admin()
    r = admin.post("/notices", data={"title": "Friday closed", "body": "অফিস বন্ধ", "department": "Store",
                                     "via_whatsapp": "1"}, follow_redirects=False)
    assert r.status_code == 303
    texts = [call for call in calls if call[0] == "text"]
    assert len(texts) == 2 and all("Friday closed" in call[2] for call in texts)
    with get_db() as c:
        row = c.execute("SELECT id,sent,recipients FROM notices WHERE title='Friday closed'").fetchone()
        store = c.execute("SELECT * FROM employees WHERE id=?", (people[2],)).fetchone()
        sales = c.execute("SELECT * FROM employees WHERE id=?", (people[0],)).fetchone()
        assert (row["sent"], row["recipients"]) == (2, 2)
        assert "Friday closed" in notices.portal_block(c, store)
        assert notices.portal_block(c, sales) == ""
    admin.post(f"/notices/{row['id']}/remove")
    with get_db() as c:
        assert notices.portal_block(c, store) == ""
    assert "Taken down" in admin.get("/notices").text
    assert _viewer().get("/notices").status_code == 403
