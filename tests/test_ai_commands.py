import base64
import json
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

import app.ai_commands as cmd
import app.ai_insights as ai
from app.config import settings
from app.database import get_db
from app.main import app

TZ = ZoneInfo(settings.timezone)
TODAY = datetime.now(TZ).date()
MONTH = TODAY.strftime("%Y-%m")


def _client(role="super_admin"):
    client = TestClient(app)
    session = {"role": role, "user_name": "Tester"}
    session.update({"admin": True} if role == "super_admin" else {"hr_id": 987654})
    client.cookies.set("session", TimestampSigner(os.environ["SESSION_SECRET"]).sign(
        base64.b64encode(json.dumps(session).encode())).decode())
    return client


@pytest.fixture
def gemini(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    calls, replies = [], []

    def fake_post(url, headers, body):
        calls.append(body["contents"][0]["parts"][0]["text"])
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(replies.pop(0))}]}}], "usageMetadata": {}}

    monkeypatch.setattr(ai, "_post", fake_post)
    with get_db() as c:
        c.execute("DELETE FROM ai_usage")
    return calls, replies


@pytest.fixture
def staff():
    stamp = int(time.time() * 1000) % 10000000
    people = {}
    with get_db() as c:
        for key, name, salary in (("rana", "Rana Ahmed", 15000), ("sojib", "Sojib Hasan", 12000), ("nosal", "No Salary", 0)):
            staff_id = f"C{key[:2].upper()}{stamp}"
            c.execute("INSERT INTO employees(staff_id,name,is_active,fixed_salary) VALUES(?,?,?,?)", (staff_id, name, True, salary))
            people[key] = {"staff_id": staff_id, "id": c.execute("SELECT id FROM employees WHERE staff_id=?", (staff_id,)).fetchone()["id"]}
    return people


def _draft_id(html):
    return int(html.split("/ai/command/")[1].split("/apply")[0])


def _duties(employee_id):
    with get_db() as c:
        return [(r["duty_date"], r["start_time"], r["end_time"]) for r in c.execute(
            "SELECT duty_date,start_time,end_time FROM custom_duties WHERE employee_id=? ORDER BY duty_date", (employee_id,)).fetchall()]


def test_duty_is_assigned_only_after_confirm(gemini, staff):
    calls, replies = gemini
    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    replies.append({"actions": [{"type": "duty_assign", "staff_ids": [staff["rana"]["staff_id"], staff["sojib"]["staff_id"].lower()],
                                 "start_date": tomorrow, "end_date": tomorrow, "weekdays": None,
                                 "start_time": "10:00", "end_time": "16:00", "break_minutes": 0, "note": ""}], "unclear": []})
    admin = _client()
    page = admin.post("/ai/command", data={"command": "রানা, সজীব এদের কাল সকাল ১০টা থেকে ৪টা পর্যন্ত duty দাও"})
    assert page.status_code == 200 and "Confirm and apply" in page.text
    assert "10:00 AM – 4:00 PM" in page.text and "Rana Ahmed" in page.text and "Sojib Hasan" in page.text
    assert "Rana Ahmed" in calls[0] and '"today"' in calls[0]
    assert _duties(staff["rana"]["id"]) == []                              # nothing changed yet
    draft = _draft_id(page.text)
    assert "Done" in admin.post(f"/ai/command/{draft}/apply").text
    assert _duties(staff["rana"]["id"]) == [(tomorrow, "10:00", "16:00")]
    assert _duties(staff["sojib"]["id"]) == [(tomorrow, "10:00", "16:00")]
    assert "already applied" in admin.post(f"/ai/command/{draft}/apply").text   # no double apply

    replies.append({"actions": [{"type": "duty_cancel", "staff_ids": [staff["rana"]["staff_id"]],
                                 "start_date": tomorrow, "end_date": tomorrow, "weekdays": None}], "unclear": []})
    page = admin.post("/ai/command", data={"command": "Rana er kal er duty batil"})
    assert "1 duty day(s) will be removed" in page.text
    admin.post(f"/ai/command/{_draft_id(page.text)}/apply")
    assert _duties(staff["rana"]["id"]) == [] and len(_duties(staff["sojib"]["id"])) == 1


def test_unclear_or_unknown_names_block_the_whole_plan(gemini, staff):
    _, replies = gemini
    day = TODAY.isoformat()
    replies.append({"actions": [{"type": "duty_assign", "staff_ids": [staff["rana"]["staff_id"], "NOBODY-1"], "start_date": day,
                                 "end_date": day, "weekdays": None, "start_time": "10:00", "end_time": "16:00"}],
                    "unclear": ["“কুদ্দুস” নামে দুইজন আছে"]})
    page = _client().post("/ai/command", data={"command": "rana kuddus duty"}).text
    assert "Nothing can be applied yet" in page and "কুদ্দুস" in page and "NOBODY-1" in page
    assert "Confirm and apply" not in page and _duties(staff["rana"]["id"]) == []
    for bad in ({"type": "delete_employee", "staff_ids": [staff["rana"]["staff_id"]]},
                {"type": "duty_assign", "staff_ids": [staff["rana"]["staff_id"]], "start_date": "tomorrow", "end_date": day,
                 "start_time": "10:00", "end_time": "16:00"},
                {"type": "payroll_adjust", "staff_ids": [staff["rana"]["staff_id"]], "month": MONTH, "field": "net_salary",
                 "op": "set", "amount": 99999},
                {"type": "payroll_adjust", "staff_ids": [staff["rana"]["staff_id"]], "month": MONTH, "field": "bonus",
                 "op": "add", "amount": -500}):
        replies.append({"actions": [bad], "unclear": []})
        assert "Confirm and apply" not in _client().post("/ai/command", data={"command": "x"}).text


def test_payroll_adjustments_show_old_and_new_and_respect_locks(gemini, staff):
    _, replies = gemini
    admin = _client()
    rana = staff["rana"]
    replies.append({"actions": [{"type": "payroll_adjust", "staff_ids": [rana["staff_id"]], "month": MONTH, "field": "bonus",
                                 "op": "add", "amount": 500, "reason": "Eid"}], "unclear": []})
    page = admin.post("/ai/command", data={"command": "Rana ke 500 taka bonus dao, Eid er jonno"}).text
    assert "৳0.00 → ৳500.00" in page and "draft will be created" in page
    assert "Done" in admin.post(f"/ai/command/{_draft_id(page)}/apply").text
    with get_db() as c:
        row = c.execute("SELECT bonus,adjustment_reason,payment_status,fixed_salary FROM payroll_records WHERE employee_id=? AND salary_month=?",
                        (rana["id"], MONTH)).fetchone()
        assert (float(row["bonus"]), row["adjustment_reason"], row["payment_status"], float(row["fixed_salary"])) == (500.0, "Eid", "draft", 15000.0)
        assert c.execute("SELECT COUNT(*) n FROM payroll_change_logs l JOIN payroll_records p ON p.id=l.payroll_id WHERE p.employee_id=?",
                         (rana["id"],)).fetchone()["n"] >= 1

    replies.append({"actions": [{"type": "payroll_adjust", "staff_ids": [rana["staff_id"]], "month": MONTH, "field": "bonus", "op": "add", "amount": 250, "reason": ""},
                                {"type": "payroll_adjust", "staff_ids": [rana["staff_id"]], "month": MONTH, "field": "overtime_hours", "op": "set", "amount": 6, "reason": ""}],
                    "unclear": []})
    page = admin.post("/ai/command", data={"command": "aro 250 bonus, overtime mot 6 ghonta"}).text
    assert "৳500.00 → ৳750.00" in page and "0h → 6h" in page
    admin.post(f"/ai/command/{_draft_id(page)}/apply")
    with get_db() as c:
        row = c.execute("SELECT bonus,overtime_hours FROM payroll_records WHERE employee_id=? AND salary_month=?", (rana["id"], MONTH)).fetchone()
        assert (float(row["bonus"]), float(row["overtime_hours"])) == (750.0, 6.0)
        c.execute("UPDATE payroll_records SET payment_status='finalized' WHERE employee_id=? AND salary_month=?", (rana["id"], MONTH))

    replies.append({"actions": [{"type": "payroll_adjust", "staff_ids": [rana["staff_id"], staff["nosal"]["staff_id"]], "month": MONTH,
                                 "field": "fine", "op": "add", "amount": 100, "reason": "late"}], "unclear": []})
    page = admin.post("/ai/command", data={"command": "fine 100"}).text
    assert "finalized and locked" in page and "no basic salary" in page and "Confirm and apply" not in page


def test_salary_change_and_plan_locked_after_preview(gemini, staff):
    _, replies = gemini
    admin = _client()
    sojib = staff["sojib"]
    replies.append({"actions": [{"type": "salary_set", "staff_ids": [sojib["staff_id"]], "fixed_salary": 14000}], "unclear": []})
    page = admin.post("/ai/command", data={"command": "Sojib er beton 14000 koro"}).text
    assert "৳12,000.00 → ৳14,000.00" in page
    admin.post(f"/ai/command/{_draft_id(page)}/apply")
    with get_db() as c:
        assert float(c.execute("SELECT fixed_salary FROM employees WHERE id=?", (sojib["id"],)).fetchone()["fixed_salary"]) == 14000.0

    # A plan is re-checked at apply time: the employee was deactivated after the preview.
    day = TODAY.isoformat()
    replies.append({"actions": [{"type": "duty_assign", "staff_ids": [sojib["staff_id"]], "start_date": day, "end_date": day,
                                 "weekdays": None, "start_time": "09:00", "end_time": "17:00"}], "unclear": []})
    page = admin.post("/ai/command", data={"command": "Sojib aj 9-5"}).text
    with get_db() as c:
        c.execute("UPDATE employees SET is_active=? WHERE id=?", (False, sojib["id"]))
    assert "no longer active" in admin.post(f"/ai/command/{_draft_id(page)}/apply").text
    assert _duties(sojib["id"]) == []


def test_permissions_ownership_and_expiry(gemini, staff, monkeypatch):
    _, replies = gemini
    assert TestClient(app).get("/ai/command").status_code == 401
    assert _client("viewer").post("/ai/command", data={"command": "x"}).status_code == 403
    day = TODAY.isoformat()
    replies.append({"actions": [{"type": "duty_assign", "staff_ids": [staff["rana"]["staff_id"]], "start_date": day, "end_date": day,
                                 "weekdays": None, "start_time": "09:00", "end_time": "17:00"}], "unclear": []})
    admin = _client()
    draft = _draft_id(admin.post("/ai/command", data={"command": "Rana aj 9-5"}).text)
    with get_db() as c:   # an HR account with AI + duty permission still cannot apply someone else's plan
        c.execute("DELETE FROM account_permissions WHERE account_id=987654")
        for permission in ("__configured__", "ai_manage", "duty_manage"):
            c.execute("INSERT INTO account_permissions(account_id,permission) VALUES(?,?)", (987654, permission))
    try:
        assert _client("hr_manager").post(f"/ai/command/{draft}/apply").status_code == 404
        replies.append({"actions": [{"type": "salary_set", "staff_ids": [staff["rana"]["staff_id"]], "fixed_salary": 1}], "unclear": []})
        page = _client("hr_manager").post("/ai/command", data={"command": "beton 1"}).text
        assert "do not have permission" in page and "Confirm and apply" not in page
    finally:
        with get_db() as c:
            c.execute("DELETE FROM account_permissions WHERE account_id=987654")
    monkeypatch.setattr(cmd, "DRAFT_TTL_SECONDS", -1)
    assert "expired" in admin.post(f"/ai/command/{draft}/apply").text
    assert _duties(staff["rana"]["id"]) == []
