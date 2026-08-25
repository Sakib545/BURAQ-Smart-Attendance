import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("DATABASE_PATH", "/tmp/buraq_v9_test.db")
os.environ.setdefault("REQUIRE_SECURE_SECRETS", "false")
os.environ.setdefault("ALLOW_TEMP_DB_FALLBACK", "false")
os.environ.setdefault("SESSION_SECRET", "test-session-secret-01234567890123456789")
os.environ.setdefault("CONFIG_ENCRYPTION_KEY", "test-config-secret-0123456789012345678")

Path("/tmp/buraq_v9_test.db").unlink(missing_ok=True)

from fastapi.testclient import TestClient
from app.main import app
import app.main as main_module
from app.database import get_db
from app.services import (
    approve_pending_attendance,
    automatic_attendance_shift,
    begin_attendance_action,
    set_state,
    state,
)
from app.location_links import create_location_token, verify_location_token


def test_liveness_and_readiness():
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["version"] == "9.26.0"
        assert health.headers.get("x-request-id")

        ready = client.get("/ready")
        assert ready.status_code == 200
        assert ready.json()["database_ok"] is True


def test_login_page_is_available():
    with TestClient(app) as client:
        response = client.get("/login")
        assert response.status_code == 200
        assert "BURAQ" in response.text


def test_location_fallback_token_is_signed_and_scoped():
    token = create_location_token(42, "checkin", 60)
    payload = verify_location_token(token)
    assert payload["employee_id"] == 42
    assert payload["action"] == "checkin"

    mid = len(token) // 2
    tampered = token[:mid] + ("A" if token[mid] != "A" else "B") + token[mid + 1:]
    try:
        verify_location_token(tampered)
    except ValueError:
        pass
    else:
        raise AssertionError("Tampered location token was accepted")


def test_location_fallback_accepts_once_and_closes_pending_state():
    with TestClient(app) as client:
        with get_db() as db:
            db.execute("DELETE FROM employees WHERE staff_id=?", ("TEST-LOCATION-001",))
            db.execute(
                """INSERT INTO employees(staff_id,name,phone,whatsapp_phone,shift,registration_status)
                   VALUES(?,?,?,?,?,?)""",
                ("TEST-LOCATION-001", "Location Test", "01700000001", "01700000001", "morning", "approved"),
            )
            employee = db.execute(
                "SELECT id FROM employees WHERE staff_id=?", ("TEST-LOCATION-001",)
            ).fetchone()
        set_state("8801700000001", "checkin_location")
        token = create_location_token(employee["id"], "checkin", 60)

        page = client.get("/attendance/location", params={"t": token})
        assert page.status_code == 200
        assert "Location Allow করুন" in page.text
        assert "geolocation=(self)" in page.headers["permissions-policy"]

        submitted = client.post(
            "/attendance/location/submit",
            json={"token": token, "latitude": 23.8103, "longitude": 90.4125},
        )
        assert submitted.status_code == 200
        assert submitted.json()["ok"] is True
        assert state("8801700000001")["state"].startswith("checkin_selfie:")

        reused = client.post(
            "/attendance/location/submit",
            json={"token": token, "latitude": 23.8103, "longitude": 90.4125},
        )
        assert reused.status_code == 400


def test_pending_selfie_approval_finalizes_once():
    with TestClient(app):
        with get_db() as db:
            db.execute("DELETE FROM employees WHERE staff_id=?", ("TEST-PENDING-001",))
            db.execute("INSERT INTO employees(staff_id,name,shift,registration_status) VALUES(?,?,?,?)",
                       ("TEST-PENDING-001", "Pending Test", "morning", "approved"))
            employee = db.execute("SELECT id FROM employees WHERE staff_id=?", ("TEST-PENDING-001",)).fetchone()
            db.execute("""INSERT INTO attendance_fingerprints(
                employee_id,action,media_id,image_data,latitude,longitude,distance_meters,
                phash,ahash,dhash,embedding,decision,review_status,face_score,duplicate_score,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (employee["id"], "checkin", "test-media-001", "", 23.0, 90.0, 12.0,
                 "p", "a", "d", "[]", "accept", "pending", 0.91, 0.02, "2026-08-02 09:02:00"))
            fingerprint = db.execute("SELECT id FROM attendance_fingerprints WHERE media_id=?", ("test-media-001",)).fetchone()

        approved = approve_pending_attendance(int(fingerprint["id"]), "test-admin")
        assert approved and "Check-in" in approved["result"]
        assert approve_pending_attendance(int(fingerprint["id"]), "test-admin") is None

        with get_db() as db:
            selfie = db.execute("SELECT review_status,attendance_applied FROM attendance_fingerprints WHERE id=?", (fingerprint["id"],)).fetchone()
            attendance = db.execute("SELECT COUNT(*) c,MAX(attendance_shift) attendance_shift FROM attendance WHERE employee_id=? AND work_date=?", (employee["id"], "2026-08-02")).fetchone()
        assert selfie["review_status"] == "approved"
        assert bool(selfie["attendance_applied"]) is True
        assert int(attendance["c"]) == 1
        # 09:02 is before the second-shift cutoff, so this is a first-shift arrival.
        assert attendance["attendance_shift"] == "first"


def test_shift_is_automatic_and_checkout_requires_checkin():
    assert automatic_attendance_shift("2026-08-02T08:15:00+06:00") == "first"
    assert automatic_attendance_shift("2026-08-02T15:00:00+06:00") == "first"
    assert automatic_attendance_shift("2026-08-02T16:00:00+06:00") == "second"
    with TestClient(app):
        with get_db() as db:
            db.execute("DELETE FROM employees WHERE staff_id=?", ("TEST-SHIFT-002",))
            db.execute(
                "INSERT INTO employees(staff_id,name,phone,whatsapp_phone,shift,registration_status) VALUES(?,?,?,?,?,?)",
                ("TEST-SHIFT-002", "Shift Test", "01700000002", "01700000002", "morning", "approved"),
            )
            employee = db.execute("SELECT id FROM employees WHERE staff_id=?", ("TEST-SHIFT-002",)).fetchone()

        no_checkout = begin_attendance_action("01700000002", "checkout")
        assert "আগে Check In" in no_checkout

        with get_db() as db:
            db.execute("""INSERT INTO attendance_fingerprints(
                employee_id,action,media_id,image_data,latitude,longitude,distance_meters,
                phash,ahash,dhash,embedding,decision,review_status,face_score,duplicate_score,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (employee["id"], "check_in", "test-media-shift-002", "", 23.0, 90.0, 12.0,
                 "p2", "a2", "d2", "[]", "accept", "pending", 0.91, 0.02,
                 "2026-08-02T16:02:00+06:00"))
            fingerprint = db.execute("SELECT id FROM attendance_fingerprints WHERE media_id=?", ("test-media-shift-002",)).fetchone()

        approved = approve_pending_attendance(int(fingerprint["id"]), "test-admin")
        assert approved and "Second Shift" in approved["result"]
        with get_db() as db:
            attendance = db.execute(
                "SELECT attendance_shift FROM attendance WHERE employee_id=? AND work_date=?",
                (employee["id"], "2026-08-02"),
            ).fetchone()
        assert attendance["attendance_shift"] == "second"


def test_dashboard_approve_button_is_idempotent(monkeypatch):
    async def notification_ok(*_args, **_kwargs):
        return {"sent": True}

    monkeypatch.setattr(main_module, "send_selfie_review_result", notification_ok)
    with TestClient(app) as client:
        setup = client.post(
            "/setup",
            data={"email": "admin@buraq.com", "password": "password123", "confirm_password": "password123"},
            follow_redirects=False,
        )
        if setup.status_code == 403:
            login = client.post(
                "/login",
                data={"email": "admin@buraq.com", "password": "password123"},
                follow_redirects=False,
            )
            assert login.status_code == 303

        with get_db() as db:
            db.execute("DELETE FROM employees WHERE staff_id=?", ("TEST-DASH-APPROVE",))
            db.execute(
                """INSERT INTO employees(staff_id,name,phone,whatsapp_phone,shift,registration_status)
                   VALUES(?,?,?,?,?,?)""",
                ("TEST-DASH-APPROVE", "Dashboard Approve", "01700000003", "8801700000003", "morning", "approved"),
            )
            employee = db.execute("SELECT id FROM employees WHERE staff_id=?", ("TEST-DASH-APPROVE",)).fetchone()
            db.execute(
                """INSERT INTO attendance_fingerprints(
                    employee_id,action,media_id,image_data,latitude,longitude,distance_meters,
                    phash,ahash,dhash,embedding,decision,review_status,face_score,duplicate_score,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (employee["id"], "check_in", "dashboard-approve-media", "", 23.0, 90.0, 5.0,
                 "pd", "ad", "dd", "[]", "accept", "pending", 0.93, 0.01, "2026-08-04 08:05:00"),
            )
            fingerprint = db.execute(
                "SELECT id FROM attendance_fingerprints WHERE media_id=?", ("dashboard-approve-media",)
            ).fetchone()

        page = client.get("/duplicates?review=pending")
        assert page.status_code == 200
        assert f"/duplicates/{fingerprint['id']}/approve" in page.text

        first = client.post(f"/duplicates/{fingerprint['id']}/approve", follow_redirects=False)
        second = client.post(f"/duplicates/{fingerprint['id']}/approve", follow_redirects=False)
        assert first.status_code == 303
        assert first.headers["location"].endswith("saved=approved")
        assert second.status_code == 303
        assert second.headers["location"].endswith("saved=approved")

        with get_db() as db:
            selfie = db.execute(
                "SELECT review_status,attendance_applied FROM attendance_fingerprints WHERE id=?",
                (fingerprint["id"],),
            ).fetchone()
            evidence = db.execute(
                "SELECT COUNT(*) c FROM attendance_evidence WHERE image_media_id=?",
                ("dashboard-approve-media",),
            ).fetchone()
        assert selfie["review_status"] == "approved"
        assert bool(selfie["attendance_applied"]) is True
        assert int(evidence["c"]) == 1


def test_duty_range_assigns_custom_duties_and_skips_fridays():
    with TestClient(app) as client:
        setup = client.post(
            "/setup",
            data={"email": "admin@buraq.com", "password": "password123",
                  "confirm_password": "password123"},
            follow_redirects=False,
        )
        if setup.status_code == 403:
            client.post(
                "/login",
                data={"email": "admin@buraq.com", "password": "password123"},
                follow_redirects=False,
            )
        with get_db() as db:
            db.execute("DELETE FROM employees WHERE staff_id=?", ("TEST-RANGE",))
            db.execute(
                "INSERT INTO employees(staff_id,name,shift,registration_status,is_active) VALUES(?,?,?,?,?)",
                ("TEST-RANGE", "Range Worker", "morning", "approved", 1),
            )
            eid = db.execute("SELECT id FROM employees WHERE staff_id=?", ("TEST-RANGE",)).fetchone()["id"]

        ok = client.post(
            f"/employees/{eid}/duty/range",
            data={"date_from": "2026-09-01", "date_to": "2026-09-10",
                  "preset": "custom", "start_time": "16:00", "end_time": "22:00",
                  "scope": "skip_fri"},
            follow_redirects=False,
        )
        assert ok.status_code == 303

        with get_db() as db:
            rows = db.execute(
                "SELECT duty_date,start_time FROM custom_duties WHERE employee_id=? ORDER BY duty_date",
                (eid,),
            ).fetchall()
        dates = [row["duty_date"] for row in rows]
        assert len(dates) == 9                 # 10 calendar days minus one Friday
        assert "2026-09-04" not in dates       # 2026-09-04 is a Friday -> skipped
        assert rows[0]["start_time"] == "16:00"  # explicit time applied to every day

        bad = client.post(
            f"/employees/{eid}/duty/range",
            data={"date_from": "2026-09-10", "date_to": "2026-09-01",
                  "preset": "custom", "start_time": "16:00", "end_time": "22:00"},
            follow_redirects=False,
        )
        assert bad.status_code == 400          # end before start is rejected


def test_hr_correction_recomputes_late_against_assigned_duty():
    with TestClient(app) as client:
        setup = client.post(
            "/setup",
            data={"email": "admin@buraq.com", "password": "password123",
                  "confirm_password": "password123"},
            follow_redirects=False,
        )
        if setup.status_code == 403:
            client.post(
                "/login",
                data={"email": "admin@buraq.com", "password": "password123"},
                follow_redirects=False,
            )
        with get_db() as db:
            db.execute("DELETE FROM employees WHERE staff_id=?", ("TEST-CORR",))
            db.execute(
                "INSERT INTO employees(staff_id,name,shift,registration_status,is_active) VALUES(?,?,?,?,?)",
                ("TEST-CORR", "Correction Worker", "morning", "approved", 1),
            )
            eid = db.execute("SELECT id FROM employees WHERE staff_id=?", ("TEST-CORR",)).fetchone()["id"]
            db.execute("DELETE FROM duty_schedules WHERE employee_id=?", (eid,))
            for wd in range(7):
                db.execute(
                    "INSERT INTO duty_schedules(employee_id,weekday,start_time,end_time,is_active) VALUES(?,?,?,?,1)",
                    (eid, wd, "16:00", "22:00"),
                )
            # A stale row measured against the 08:30 global start.
            db.execute(
                "INSERT INTO attendance(employee_id,work_date,check_in,attendance_shift,late_minutes,status,source) "
                "VALUES(?,?,?,?,?,?,?)",
                (eid, "2026-08-20", "2026-08-20T15:55:00", "first", 445, "present", "whatsapp"),
            )

        client.post(
            "/correction",
            data={"employee_id": str(eid), "work_date": "2026-08-20",
                  "check_in": "15:55", "check_out": "22:05", "reason": "fix time"},
            follow_redirects=False,
        )
        with get_db() as db:
            cid = db.execute(
                "SELECT id FROM attendance_corrections WHERE employee_id=? ORDER BY id DESC", (eid,)
            ).fetchone()["id"]
        approved = client.post(f"/correction/{cid}/approve", follow_redirects=False)
        assert approved.status_code == 303

        with get_db() as db:
            a = db.execute(
                "SELECT attendance_shift,late_minutes,early_leave_minutes FROM attendance WHERE employee_id=? AND work_date=?",
                (eid, "2026-08-20"),
            ).fetchone()
        # Recomputed against the assigned 16:00-22:00 duty, not the 08:30 global start.
        assert a["attendance_shift"] == "second"
        assert a["late_minutes"] == 0
        assert a["early_leave_minutes"] == 0
