"""Shared test setup.

The individual test modules each call ``os.environ.setdefault("DATABASE_PATH", ...)``
with their own path, but ``setdefault`` means whichever module imports first wins
for the whole process — so in a full run every file quietly shares one database.
Worse, some tests change the *global shift rules* and those changes then leak into
later files, which is what made a few assertions order-dependent and flaky.

This conftest makes the environment deterministic: it pins a single fresh test
database before any app module imports, and resets the global shift rules to
their defaults before every test so no test inherits another's configuration.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_TEST_DB = "/tmp/buraq_test_suite.db"
os.environ.setdefault("ENVIRONMENT", "development")
os.environ["DATABASE_PATH"] = _TEST_DB  # force one path, overriding per-file setdefault
os.environ.setdefault("REQUIRE_SECURE_SECRETS", "false")
os.environ.setdefault("ALLOW_TEMP_DB_FALLBACK", "false")
os.environ.setdefault("SESSION_SECRET", "test-session-secret-01234567890123456789")
os.environ.setdefault("CONFIG_ENCRYPTION_KEY", "test-config-secret-0123456789012345678")

# Start each full run from a clean database file.
Path(_TEST_DB).unlink(missing_ok=True)

import pytest  # noqa: E402


DEFAULT_SHIFT_RULES = ("08:30", "16:00", "16:00", "22:00", "16:00", 0)


@pytest.fixture(scope="session", autouse=True)
def _initialise_database():
    from app.database import init_db
    init_db(max_attempts=1)
    yield


@pytest.fixture(autouse=True)
def _reset_shift_rules(_initialise_database):
    """Restore the default global shift rules before every test so that a rule
    change in one test can never leak into another."""
    from app.shift_rules import save_shift_rules
    try:
        save_shift_rules(*DEFAULT_SHIFT_RULES)
    except Exception:
        pass
    yield
