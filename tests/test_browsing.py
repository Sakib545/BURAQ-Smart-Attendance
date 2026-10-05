import base64
import json
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

import app.browsing as browsing
from app.config import settings
from app.database import get_db
from app.main import app

TZ = ZoneInfo(settings.timezone)


def _admin_client():
    client = TestClient(app)
    session = {"role": "super_admin", "user_name": "Tester", "admin": True}
    client.cookies.set("session", TimestampSigner(os.environ["SESSION_SECRET"]).sign(
        base64.b64encode(json.dumps(session).encode())).decode())
    return client


@pytest.fixture
def employee():
    staff_id = f"BRW{int(time.time() * 1000) % 100000000}"
    with get_db() as c:
        c.execute("INSERT INTO employees(staff_id,name,is_active) VALUES(?,?,?)", (staff_id, "Browse Tester", True))
        employee_id = c.execute("SELECT id FROM employees WHERE staff_id=?", (staff_id,)).fetchone()["id"]
    browsing._pair_attempts.clear()
    return employee_id


def _check_in(employee_id, hours_ago=1, checked_out=False):
    started = datetime.now(TZ) - timedelta(hours=hours_ago)
    with get_db() as c:
        c.execute("DELETE FROM attendance WHERE employee_id=?", (employee_id,))
        c.execute("INSERT INTO attendance(employee_id,work_date,check_in,check_out) VALUES(?,?,?,?)",
                  (employee_id, started.strftime("%Y-%m-%d"), started.isoformat(timespec="seconds"),
                   datetime.now(TZ).isoformat(timespec="seconds") if checked_out else None))
    return started.strftime("%Y-%m-%d")


def _pair(employee_id):
    admin = _admin_client()
    page = admin.post("/browsing/pair-code", data={"employee_id": employee_id})
    assert page.status_code == 200
    code = page.text.split("letter-spacing:.18em'>")[1].split("<")[0]
    result = TestClient(app).post("/api/browsing/pair", json={"code": code, "label": "Desk"})
    assert result.status_code == 200, result.text
    return code, {"Authorization": "Bearer " + result.json()["token"]}


def _usage(employee_id):
    with get_db() as c:
        return {r["domain"]: int(r["seconds"]) for r in c.execute(
            "SELECT domain,seconds FROM browsing_usage WHERE employee_id=?", (employee_id,)).fetchall()}


def test_pair_code_is_single_use(employee):
    code, _ = _pair(employee)
    again = TestClient(app).post("/api/browsing/pair", json={"code": code})
    assert again.status_code == 400
    assert TestClient(app).post("/api/browsing/pair", json={"code": "WRONG123"}).status_code == 400


def test_off_duty_reports_are_not_saved(employee):
    _, auth = _pair(employee)
    client = TestClient(app)
    assert client.get("/api/browsing/status", headers=auth).json()["tracking"] is False
    r = client.post("/api/browsing/report", headers=auth, json={"entries": [{"domain": "facebook.com", "seconds": 120}]})
    assert r.json() == {"ok": True, "tracking": False, "saved": 0}
    _check_in(employee, checked_out=True)
    client.post("/api/browsing/report", headers=auth, json={"entries": [{"domain": "facebook.com", "seconds": 120}]})
    assert _usage(employee) == {}


def test_on_duty_reports_accumulate_by_domain(employee):
    _, auth = _pair(employee)
    _check_in(employee)
    client = TestClient(app)
    assert client.get("/api/browsing/status", headers=auth).json()["tracking"] is True
    entries = [{"domain": "www.Facebook.com", "seconds": 100}, {"domain": "facebook.com", "seconds": 20},
               {"domain": "https://evil/path?q=secret", "seconds": 50}, {"domain": "localhost", "seconds": 50},
               {"domain": "youtube.com", "seconds": -5}]
    r = client.post("/api/browsing/report", headers=auth, json={"entries": entries})
    assert r.json()["saved"] == 120
    assert _usage(employee) == {"facebook.com": 120}
    page = _admin_client().get("/browsing")
    assert page.status_code == 200 and "facebook.com" in page.text and "Browse Tester" in page.text


def test_report_cannot_claim_more_time_than_passed(employee):
    _, auth = _pair(employee)
    _check_in(employee)
    client = TestClient(app)
    first = client.post("/api/browsing/report", headers=auth, json={"entries": [{"domain": "a.com", "seconds": 99999}]})
    assert first.json()["saved"] == browsing.MAX_REPORT_SECONDS
    second = client.post("/api/browsing/report", headers=auth, json={"entries": [{"domain": "a.com", "seconds": 99999}]})
    assert second.json()["saved"] <= browsing.REPORT_SLACK_SECONDS + 2


def test_forgotten_checkout_stops_counting(employee):
    _, auth = _pair(employee)
    _check_in(employee, hours_ago=browsing.OPEN_CHECKIN_MAX_HOURS + 1)
    assert TestClient(app).get("/api/browsing/status", headers=auth).json()["tracking"] is False


def test_revoked_device_and_bad_token_rejected(employee):
    _, auth = _pair(employee)
    client = TestClient(app)
    assert client.get("/api/browsing/status", headers={"Authorization": "Bearer " + "x" * 40}).status_code == 401
    with get_db() as c:
        device_id = c.execute("SELECT id FROM browsing_devices WHERE employee_id=?", (employee,)).fetchone()["id"]
    assert _admin_client().post(f"/browsing/devices/{device_id}/revoke", follow_redirects=False).status_code == 303
    assert client.get("/api/browsing/status", headers=auth).status_code == 401


def test_dashboard_requires_permission():
    assert TestClient(app).get("/browsing").status_code == 401
    viewer = TestClient(app)
    session = {"role": "viewer", "user_name": "V", "hr_id": 987654}
    viewer.cookies.set("session", TimestampSigner(os.environ["SESSION_SECRET"]).sign(
        base64.b64encode(json.dumps(session).encode())).decode())
    assert viewer.get("/browsing").status_code == 403
    assert viewer.post("/browsing/pair-code", data={"employee_id": 1}).status_code == 403
