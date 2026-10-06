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
    assert r.json() == {"ok": True, "tracking": False, "saved": 0, "idle_seconds": browsing.idle_minutes() * 60}
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


def test_public_install_page_and_download():
    import io, zipfile
    client = TestClient(app)
    page = client.get("/tracker?code=abcd-efgh<script>")
    assert page.status_code == 200 and "ABCD-EFGH" in page.text and "<script>" not in page.text.split("ABCD-EFGH")[1][:40]
    download = client.get("/tracker/extension.zip")
    assert download.status_code == 200 and download.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(download.content))
    assert sorted(archive.namelist()) == sorted(f"buraq-tracker/{n}" for n in browsing.EXTENSION_FILES)
    assert archive.read("buraq-tracker/icons/icon128.png")[:4] == b"\x89PNG"
    assert "http://testserver" in archive.read("buraq-tracker/background.js").decode()
    assert "/tracker" in _admin_client().get("/browsing").text


def test_personal_link_pairs_without_a_code(employee):
    import io, zipfile
    admin = _admin_client()
    page = admin.get("/browsing").text
    with get_db() as c:
        token = browsing.invite_token(c, employee)
    assert f"/tracker/i/{token}" in page
    client = TestClient(app)
    personal = client.get(f"/tracker/i/{token}")
    assert personal.status_code == 200 and "Browse Tester" in personal.text
    archive = zipfile.ZipFile(io.BytesIO(client.get(f"/tracker/i/{token}/extension.zip").content))
    config = archive.read("buraq-tracker/config.js").decode()
    assert token in config and "http://testserver" in config
    paired = client.post("/api/browsing/pair", json={"invite": token})
    assert paired.status_code == 200 and paired.json()["employee"] == "Browse Tester"
    auth = {"Authorization": "Bearer " + paired.json()["token"]}

    # Admin cancels the link: it stops working, the connected PC does not.
    assert admin.post(f"/browsing/invites/{employee}/reset", follow_redirects=False).status_code == 303
    assert client.get(f"/tracker/i/{token}").status_code == 404
    assert client.get(f"/tracker/i/{token}/extension.zip").status_code == 404
    assert client.post("/api/browsing/pair", json={"invite": token}).status_code == 400
    assert client.get("/api/browsing/status", headers=auth).status_code == 200
    assert client.post("/api/browsing/pair", json={"invite": token[:-2] + "xx"}).status_code == 400


def test_personal_page_reports_connection_and_store_button(employee, monkeypatch):
    client = TestClient(app)
    with get_db() as c:
        token = browsing.invite_token(c, employee)
    assert client.get(f"/tracker/i/{token}/status").json() == {"connected": False}
    assert "extension.zip" in client.get(f"/tracker/i/{token}").text
    client.post("/api/browsing/pair", json={"invite": token})
    assert client.get(f"/tracker/i/{token}/status").json() == {"connected": True}
    assert client.get("/tracker/i/not-a-token/status").status_code == 404
    monkeypatch.setenv("BROWSING_EXTENSION_STORE_URL", "https://chromewebstore.google.com/detail/buraq/abcdefghijklmnopabcdefghijklmnop")
    page = client.get(f"/tracker/i/{token}").text
    assert "Add to Chrome" in page and "extension.zip" not in page
    monkeypatch.setenv("BROWSING_EXTENSION_STORE_URL", "javascript:alert(1)")
    assert "extension.zip" in client.get(f"/tracker/i/{token}").text
    assert client.get("/tracker/privacy").status_code == 200


def test_staff_id_pairs_from_the_extension(employee):
    with get_db() as c:
        staff_id = c.execute("SELECT staff_id FROM employees WHERE id=?", (employee,)).fetchone()["staff_id"]
    client = TestClient(app)
    assert client.post("/api/browsing/pair", json={"staff_id": "NO-SUCH-ID"}).status_code == 400
    paired = client.post("/api/browsing/pair", json={"staff_id": f"  {staff_id.lower()} "})
    assert paired.status_code == 200 and paired.json()["employee"] == "Browse Tester"
    auth = {"Authorization": "Bearer " + paired.json()["token"]}
    assert client.get("/api/browsing/status", headers=auth).status_code == 200
    client.post("/api/browsing/pair", json={"staff_id": staff_id})
    page = _admin_client().get("/browsing").text
    assert "(Staff ID)" in page and "more than one PC" in page
    with get_db() as c:
        c.execute("UPDATE employees SET is_active=? WHERE id=?", (False, employee))
    assert client.post("/api/browsing/pair", json={"staff_id": staff_id}).status_code == 400


def test_outside_and_no_signal_are_tracked(employee):
    client = TestClient(app)
    _, auth = _pair(employee)
    date = _check_in(employee, hours_ago=2)
    with get_db() as c:   # pretend the PC last checked in a minute ago
        c.execute("UPDATE browsing_devices SET last_seen_at=?,last_report_at=? WHERE employee_id=?",
                  (int(time.time()) - 60, int(time.time()) - 60, employee))
    r = client.post("/api/browsing/report", headers=auth, json={"entries": [
        {"domain": "~outside", "seconds": 40}, {"domain": "facebook.com", "seconds": 20}]})
    assert r.json()["saved"] == 20
    assert _usage(employee) == {"facebook.com": 20}                       # "outside" is never a website
    with get_db() as c:
        row = c.execute("SELECT online_seconds,outside_seconds FROM browsing_presence WHERE employee_id=? AND work_date=?",
                        (employee, date)).fetchone()
        assert int(row["outside_seconds"]) == 40 and 55 <= int(row["online_seconds"]) <= 65
        assert 7100 <= browsing.duty_seconds(c, employee, date) <= 7300
    client.get("/api/browsing/status", headers=auth)                       # heartbeats count as signal too
    with get_db() as c:
        assert int(c.execute("SELECT online_seconds FROM browsing_presence WHERE employee_id=?", (employee,)).fetchone()["online_seconds"]) <= 70
    page = _admin_client().get(f"/browsing?date={date}").text
    assert "Outside tracker" in page and "No signal" in page and "Browse Tester" in page
    assert "1h 5" in page                                                  # ~2h duty, ~1 min signal -> ~1h 58m silent


def test_silent_tracker_still_listed(employee):
    _pair(employee)
    date = _check_in(employee, hours_ago=3)
    page = _admin_client().get(f"/browsing?date={date}").text
    assert "Browse Tester" in page and "100%" in page                      # connected, on duty, never heard from


def test_idle_rule_and_idle_time(employee):
    client = TestClient(app)
    admin = _admin_client()
    assert admin.post("/browsing/idle-rule", data={"minutes": 10}, follow_redirects=False).status_code == 303
    _, auth = _pair(employee)
    assert client.get("/api/browsing/status", headers=auth).json()["idle_seconds"] == 600
    admin.post("/browsing/idle-rule", data={"minutes": 99})                # clamped
    assert browsing.idle_minutes() == browsing.IDLE_MAX_MINUTES
    admin.post("/browsing/idle-rule", data={"minutes": 6})
    date = _check_in(employee, hours_ago=1)
    with get_db() as c:
        c.execute("UPDATE browsing_devices SET last_seen_at=?,last_report_at=? WHERE employee_id=?",
                  (int(time.time()) - 400, int(time.time()) - 400, employee))
    r = client.post("/api/browsing/report", headers=auth, json={"entries": [
        {"domain": "~idle", "seconds": 360}, {"domain": "facebook.com", "seconds": 30}]})
    assert r.json()["saved"] == 30 and r.json()["idle_seconds"] == 360
    assert _usage(employee) == {"facebook.com": 30}
    with get_db() as c:
        assert int(c.execute("SELECT idle_seconds FROM browsing_presence WHERE employee_id=? AND work_date=?",
                             (employee, date)).fetchone()["idle_seconds"]) == 360
    page = admin.get(f"/browsing?date={date}").text
    assert "<th>Idle</th>" in page and "Idle rule" in page and "6 minutes or more" in page
    viewer = TestClient(app)
    assert viewer.post("/browsing/idle-rule", data={"minutes": 2}).status_code == 401
