import hashlib
import hmac
import json

from fastapi.testclient import TestClient

import app.main as main
from app.main import app, hash_password
from app.database import get_db


def test_webhook_rejects_bad_signature(monkeypatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "s3cret")
    body = json.dumps({"entry": []}).encode()
    good = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    headers = {"content-type": "application/json"}
    with TestClient(app) as client:
        assert client.post("/webhook/whatsapp", content=body, headers=headers).status_code == 403
        assert client.post("/webhook/whatsapp", content=body, headers={**headers, "x-hub-signature-256": "sha256=bad"}).status_code == 403
        assert client.post("/webhook/whatsapp", content=body, headers={**headers, "x-hub-signature-256": good}).status_code == 200


def test_webhook_unsigned_still_accepted_without_secret(monkeypatch):
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    with TestClient(app) as client:
        assert client.post("/webhook/whatsapp", json={"entry": []}).status_code == 200
        assert client.post("/webhook/whatsapp", content=b"not json").status_code == 400


def test_login_is_throttled(monkeypatch):
    monkeypatch.setattr(main, "LOGIN_MAX_FAILURES", 3)
    main._login_failures.clear()
    with TestClient(app) as client:
        for _ in range(3):
            r = client.post("/login", data={"email": "x@buraq.com", "password": "wrong"}, follow_redirects=False)
            assert r.headers["location"] == "/login?error=1"
        r = client.post("/login", data={"email": "x@buraq.com", "password": "wrong"}, follow_redirects=False)
        assert r.headers["location"] == "/login?error=locked"
    main._login_failures.clear()


def test_disabled_hr_account_loses_session():
    main._login_failures.clear()
    with TestClient(app) as client:
        with get_db() as c:
            c.execute("DELETE FROM hr_accounts WHERE email=?", ("gone@buraq.com",))
            c.execute("INSERT INTO hr_accounts(name,email,password_hash,role,is_active) VALUES(?,?,?,?,?)",
                      ("Gone", "gone@buraq.com", hash_password("password123"), "hr_manager", True))
        r = client.post("/login", data={"email": "gone@buraq.com", "password": "password123"}, follow_redirects=False)
        assert r.headers["location"] == "/dashboard"
        assert client.get("/employees").status_code == 200
        with get_db() as c:
            c.execute("UPDATE hr_accounts SET is_active=? WHERE email=?", (False, "gone@buraq.com"))
        assert client.get("/employees").status_code == 401
