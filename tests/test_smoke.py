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


def test_liveness_and_readiness():
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["version"] == "9.1.0"
        assert health.headers.get("x-request-id")

        ready = client.get("/ready")
        assert ready.status_code == 200
        assert ready.json()["database_ok"] is True


def test_login_page_is_available():
    with TestClient(app) as client:
        response = client.get("/login")
        assert response.status_code == 200
        assert "BURAQ" in response.text
