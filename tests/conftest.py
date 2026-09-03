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
