import base64
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

import app.ai_insights as ai
from app.config import settings
from app.database import get_db
from app.main import app

TZ = ZoneInfo(settings.timezone)


def _client(role="super_admin"):
    client = TestClient(app)
    session = {"role": role, "user_name": "Tester"}
    session.update({"admin": True} if role == "super_admin" else {"hr_id": 987654})
    client.cookies.set("session", TimestampSigner(os.environ["SESSION_SECRET"]).sign(
        base64.b64encode(json.dumps(session).encode())).decode())
    return client


@pytest.fixture
def gemini(monkeypatch):
    """Fake Gemini: records what was sent and replies with whatever the test queued."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    calls, replies = [], []

    def fake_post(url, headers, body):
        calls.append({"url": url, "headers": headers, "body": body,
                      "prompt": body["contents"][0]["parts"][0]["text"]})
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return {"candidates": [{"content": {"parts": [{"text": reply if isinstance(reply, str) else json.dumps(reply)}]}}],
                "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20}}

    monkeypatch.setattr(ai, "_post", fake_post)
    with get_db() as c:
        c.execute("DELETE FROM ai_usage")
        c.execute("DELETE FROM ai_reports")
        c.execute("DELETE FROM browsing_site_categories")
        c.execute("DELETE FROM browsing_usage")
    return calls, replies


@pytest.fixture
def staff():
    staff_id = f"AI{int(time.time() * 1000) % 100000000}"
    yesterday = (datetime.now(TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
    with get_db() as c:
        c.execute("INSERT INTO employees(staff_id,name,phone,is_active) VALUES(?,?,?,?)",
                  (staff_id, "Secret Name Person", "0170" + staff_id[-7:], True))
        employee_id = c.execute("SELECT id FROM employees WHERE staff_id=?", (staff_id,)).fetchone()["id"]
        c.execute("INSERT INTO attendance(employee_id,work_date,check_in,late_minutes) VALUES(?,?,?,?)",
                  (employee_id, yesterday, yesterday + "T10:40:00+06:00", 40))
        c.execute("INSERT INTO browsing_usage(employee_id,work_date,domain,seconds) VALUES(?,?,?,?)",
                  (employee_id, yesterday, "facebook.com", 5400))
        c.execute("INSERT INTO browsing_usage(employee_id,work_date,domain,seconds) VALUES(?,?,?,?)",
                  (employee_id, yesterday, "youtube.com", 1800))
    return {"id": employee_id, "staff_id": staff_id, "yesterday": yesterday}


def test_sites_are_sorted_and_admin_choice_wins(gemini, staff):
    calls, replies = gemini
    replies.append({"facebook.com": "social", "youtube.com": "nonsense-category"})
    assert ai.classify_new_domains() == 2
    assert calls[0]["headers"]["x-goog-api-key"] == "test-key"
    with get_db() as c:
        assert ai.category_map(c) == {"facebook.com": "social", "youtube.com": "other"}
    assert ai.classify_new_domains() == 0 and len(calls) == 1          # nothing new, no call
    admin = _client()
    r = admin.post("/browsing/categories", data={"domain": "facebook.com", "category": "work",
                                                  "back": "https://evil.example/"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/browsing"
    with get_db() as c:
        row = c.execute("SELECT category,source FROM browsing_site_categories WHERE domain='facebook.com'").fetchone()
    assert (row["category"], row["source"]) == ("work", "manual")
    page = admin.get(f"/browsing?date={staff['yesterday']}").text
    assert "75%" in page                                               # 5400 work of 7200 sorted
    assert "set by you" in admin.get(f"/browsing/{staff['id']}?date={staff['yesterday']}").text


def test_reports_send_staff_ids_not_names(gemini, staff):
    calls, replies = gemini
    replies.append({staff["staff_id"]: "গত সপ্তাহে ১ দিন late।", "UNKNOWN": "ignored"})
    assert ai.generate_weekly() == 1
    replies.append({"flags": [{"staff_id": staff["staff_id"], "severity": "HIGH", "note": "৪০ মিনিট late।"},
                              {"staff_id": "NOT-REAL", "severity": "low", "note": "x"}]})
    assert ai.generate_flags() == 1
    for call in calls:
        assert staff["staff_id"] in call["prompt"]
        assert "Secret Name Person" not in call["prompt"] and "0170" not in call["prompt"]
    page = _client().get("/ai").text
    assert "গত সপ্তাহে ১ দিন late।" in page and "৪০ মিনিট late।" in page and "Secret Name Person" in page


def test_question_runs_readonly_and_only_schema_leaves(gemini, staff):
    calls, replies = gemini
    replies.append({"sql": "SELECT e.staff_id, e.name, SUM(a.late_minutes) AS late FROM attendance a "
                           "JOIN employees e ON e.id=a.employee_id GROUP BY e.id ORDER BY late DESC LIMIT 50;",
                    "title": "Late"})
    result = ai.answer_question("ke beshi late?")
    assert (staff["staff_id"], "Secret Name Person", 40) in [tuple(r) for r in result["rows"]]
    assert "Secret Name Person" not in json.dumps(calls[0]["body"], ensure_ascii=False)
    for bad in ("DELETE FROM employees", "SELECT 1; DROP TABLE employees", "UPDATE attendance SET late_minutes=0",
                "PRAGMA table_info(employees)", "SELECT * FROM system_settings", "SELECT phone FROM employees",
                "WITH x AS (SELECT 1) INSERT INTO employees(id) VALUES(9)"):
        replies.append({"sql": bad, "title": "t"})
        with pytest.raises(ai.AIUnavailable):
            ai.answer_question("anything")
    replies.append({"sql": "", "message": "বেতনের তথ্য নেই"})
    with pytest.raises(ai.AIUnavailable, match="বেতনের তথ্য নেই"):
        ai.answer_question("salary?")
    with get_db() as c:
        assert c.execute("SELECT COUNT(*) n FROM employees WHERE id=?", (staff["id"],)).fetchone()["n"] == 1


def test_page_permissions_limits_and_errors(gemini, staff, monkeypatch):
    calls, replies = gemini
    assert TestClient(app).get("/ai").status_code == 401
    assert _client("viewer").get("/ai").status_code == 403
    assert _client("viewer").post("/ai/run/flags", follow_redirects=False).status_code == 403
    admin = _client()
    replies.append(ai.AIUnavailable("Gemini rejected the API key. Check GEMINI_API_KEY."))
    assert "rejected the API key" in admin.post("/ai/ask", data={"question": "x"}).text
    replies.append("this is not json")
    assert "could not be read" in admin.post("/ai/ask", data={"question": "x"}).text
    monkeypatch.setenv("AI_DAILY_CALL_LIMIT", "1")
    assert "call limit" in admin.post("/ai/ask", data={"question": "x"}).text
    monkeypatch.delenv("GEMINI_API_KEY")
    assert "GEMINI_API_KEY" in admin.get("/ai").text
    ai.run_cycle()                                                     # no key: does nothing, no error


def test_background_cycle_runs_each_job_once(gemini, staff, monkeypatch):
    calls, replies = gemini
    monkeypatch.setattr(ai, "_now", lambda: datetime.now(TZ).replace(hour=11))
    replies.extend([{"facebook.com": "social", "youtube.com": "entertainment"}, {"flags": []}, {staff["staff_id"]: "ঠিক আছে।"}])
    ai.run_cycle()
    assert len(calls) == 3
    ai.run_cycle()
    assert len(calls) == 3                                             # already done today
