"""Shared pytest setup.

Every test module used to do this at import time:

    os.environ.setdefault("DATABASE_PATH", "/tmp/its_own_name.db")
    Path(os.environ["DATABASE_PATH"]).unlink(missing_ok=True)

pytest imports *all* test modules before running any of them, and `setdefault`
is a no-op once the first module has set the variable. So every later module
resolved `os.environ["DATABASE_PATH"]` to the FIRST module's file and deleted
it — after that module had already created its schema. The suite then failed
with "no such table" and wedged; running the files one at a time hid the whole
problem, which is why it kept getting missed.

Setting the variable here, before any test module is imported, makes all those
`setdefault` calls no-ops pointing at one shared file. The per-module unlinks
are removed; this file does the single delete instead.
"""
import os
from pathlib import Path

TEST_DB = Path("/tmp/buraq_test_suite.db")

os.environ["DATABASE_PATH"] = str(TEST_DB)
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("REQUIRE_SECURE_SECRETS", "false")
os.environ.setdefault("ALLOW_TEMP_DB_FALLBACK", "false")
os.environ.setdefault("SESSION_SECRET", "test-session-secret-01234567890123456789")
os.environ.setdefault("CONFIG_ENCRYPTION_KEY", "test-config-secret-0123456789012345678")

# One clean database for the run. Deleted here and nowhere else.
TEST_DB.unlink(missing_ok=True)

import pytest


@pytest.fixture(autouse=True)
def _forged_session_hr_account():
    """Tests forge session cookies for hr_id 987654. Sessions are now checked
    against hr_accounts on every request, so that account has to exist."""
    from app.database import get_db, init_db
    init_db()
    with get_db() as c:
        if not c.execute("SELECT 1 FROM hr_accounts WHERE id=?", (987654,)).fetchone():
            c.execute("INSERT INTO hr_accounts(id,name,email,password_hash,role,is_active) VALUES(?,?,?,?,?,?)",
                      (987654, "Test Session User", "session-user@test.invalid", "x", "viewer", True))
    yield
