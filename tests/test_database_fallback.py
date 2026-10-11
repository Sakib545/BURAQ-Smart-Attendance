"""Production must never silently swap PostgreSQL for a throwaway /tmp SQLite file."""
from dataclasses import replace

import pytest

from app import database
from app.config import Settings


def _postgres_down(monkeypatch, **settings_overrides):
    monkeypatch.setattr(database, "_active_url", "postgresql+psycopg://user:pw@unreachable/db")
    monkeypatch.setattr(database, "_create_schema", lambda: (_ for _ in ()).throw(OSError("connection refused")))
    monkeypatch.setattr(database.time, "sleep", lambda _s: None)
    monkeypatch.setattr(database, "settings", replace(database.settings, **settings_overrides))
    calls = []
    monkeypatch.setattr(database, "_fallback_to_sqlite", lambda reason: calls.append(reason))
    return calls


def test_production_refuses_sqlite_fallback_even_when_flag_is_set(monkeypatch):
    calls = _postgres_down(monkeypatch, environment="production", allow_temp_db_fallback=True)
    with pytest.raises(RuntimeError, match="refusing temporary SQLite fallback in production"):
        database.init_db(max_attempts=2)
    assert calls == []


def test_production_fails_loudly_with_default_settings(monkeypatch):
    calls = _postgres_down(monkeypatch, environment="production", allow_temp_db_fallback=False)
    with pytest.raises(RuntimeError, match="Primary database unavailable"):
        database.init_db(max_attempts=1)
    assert calls == []


def test_development_can_still_opt_into_fallback(monkeypatch):
    calls = _postgres_down(monkeypatch, environment="development", allow_temp_db_fallback=True)
    database.init_db(max_attempts=1)
    assert len(calls) == 1


def test_production_config_rejects_fallback_flag(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@host/db")
    base = Settings()
    issues = replace(base, environment="production", require_secure_secrets=False, allow_temp_db_fallback=True).production_issues()
    assert any("ALLOW_TEMP_DB_FALLBACK" in issue for issue in issues)
    issues = replace(base, environment="production", require_secure_secrets=False, allow_temp_db_fallback=False).production_issues()
    assert not any("ALLOW_TEMP_DB_FALLBACK" in issue for issue in issues)
