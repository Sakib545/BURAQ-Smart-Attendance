from __future__ import annotations

from contextlib import contextmanager
import logging
from pathlib import Path
import re
import time

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine, Result
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings

logger = logging.getLogger(__name__)

SQLITE_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS employees(id INTEGER PRIMARY KEY AUTOINCREMENT,staff_id TEXT NOT NULL UNIQUE COLLATE NOCASE,name TEXT NOT NULL,phone TEXT UNIQUE,department TEXT,shift TEXT NOT NULL DEFAULT 'morning',registration_status TEXT NOT NULL DEFAULT 'unregistered',whatsapp_phone TEXT UNIQUE,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS conversation_states(phone TEXT PRIMARY KEY,state TEXT NOT NULL,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS attendance(id INTEGER PRIMARY KEY AUTOINCREMENT,employee_id INTEGER NOT NULL,work_date TEXT NOT NULL,check_in TEXT,check_out TEXT,late_minutes INTEGER NOT NULL DEFAULT 0,early_leave_minutes INTEGER NOT NULL DEFAULT 0,overtime_minutes INTEGER NOT NULL DEFAULT 0,source TEXT NOT NULL DEFAULT 'whatsapp',status TEXT NOT NULL DEFAULT 'present',created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(employee_id,work_date),FOREIGN KEY(employee_id) REFERENCES employees(id));
CREATE TABLE IF NOT EXISTS whatsapp_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,direction TEXT NOT NULL,phone TEXT,message_type TEXT,content TEXT,message_id TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE UNIQUE INDEX IF NOT EXISTS ux_whatsapp_incoming_message_id ON whatsapp_logs(message_id) WHERE direction='incoming' AND message_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS pending_registrations(id INTEGER PRIMARY KEY AUTOINCREMENT,employee_id INTEGER NOT NULL,whatsapp_phone TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,FOREIGN KEY(employee_id) REFERENCES employees(id));
CREATE TABLE IF NOT EXISTS system_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
"""

POSTGRES_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS employees(id BIGSERIAL PRIMARY KEY,staff_id TEXT NOT NULL UNIQUE,name TEXT NOT NULL,phone TEXT UNIQUE,department TEXT,shift TEXT NOT NULL DEFAULT 'morning',registration_status TEXT NOT NULL DEFAULT 'unregistered',whatsapp_phone TEXT UNIQUE,created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS ux_employees_staff_id_lower ON employees(LOWER(staff_id))""",
    """CREATE TABLE IF NOT EXISTS conversation_states(phone TEXT PRIMARY KEY,state TEXT NOT NULL,updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS attendance(id BIGSERIAL PRIMARY KEY,employee_id BIGINT NOT NULL REFERENCES employees(id),work_date TEXT NOT NULL,check_in TEXT,check_out TEXT,late_minutes INTEGER NOT NULL DEFAULT 0,early_leave_minutes INTEGER NOT NULL DEFAULT 0,overtime_minutes INTEGER NOT NULL DEFAULT 0,source TEXT NOT NULL DEFAULT 'whatsapp',status TEXT NOT NULL DEFAULT 'present',created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(employee_id,work_date))""",
    """CREATE TABLE IF NOT EXISTS whatsapp_logs(id BIGSERIAL PRIMARY KEY,direction TEXT NOT NULL,phone TEXT,message_type TEXT,content TEXT,message_id TEXT,created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS ux_whatsapp_incoming_message_id ON whatsapp_logs(message_id) WHERE direction='incoming' AND message_id IS NOT NULL""",
    """CREATE TABLE IF NOT EXISTS pending_registrations(id BIGSERIAL PRIMARY KEY,employee_id BIGINT NOT NULL REFERENCES employees(id),whatsapp_phone TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS system_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
]


def _make_engine(url: str) -> Engine:
    kwargs = {
        "pool_pre_ping": True,
        "future": True,
    }
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    else:
        kwargs.update({"pool_size": 5, "max_overflow": 10, "pool_recycle": 300})
    return create_engine(url, **kwargs)


engine: Engine = _make_engine(settings.database_url)
_active_url = settings.database_url
_startup_error = ""


class DBResult:
    def __init__(self, result: Result):
        self.result = result

    def fetchone(self):
        return self.result.mappings().fetchone()

    def fetchall(self):
        return self.result.mappings().fetchall()


class DBConnection:
    def __init__(self, conn: Connection):
        self.conn = conn

    def execute(self, sql: str, params=()):
        if isinstance(params, dict):
            result = self.conn.execute(text(sql), params)
        else:
            params = tuple(params or ())
            names: list[str] = []

            def repl(_match):
                name = f"p{len(names)}"
                names.append(name)
                return f":{name}"

            converted = re.sub(r"\?", repl, sql)
            if len(names) != len(params):
                raise ValueError(f"SQL parameter mismatch: expected {len(names)}, got {len(params)}")
            result = self.conn.execute(text(converted), {name: params[i] for i, name in enumerate(names)})
        return DBResult(result)


@contextmanager
def get_db():
    with engine.begin() as conn:
        yield DBConnection(conn)


def _create_schema() -> None:
    if _active_url.startswith("sqlite"):
        raw = engine.raw_connection()
        try:
            raw.executescript(SQLITE_SCHEMA)
            raw.commit()
        finally:
            raw.close()
    else:
        with engine.begin() as conn:
            for statement in POSTGRES_STATEMENTS:
                conn.execute(text(statement))


def _fallback_to_sqlite(reason: Exception) -> None:
    global engine, _active_url, _startup_error
    _startup_error = str(reason)
    fallback_path = Path("/tmp/buraq_attendance.db")
    fallback_path.parent.mkdir(parents=True, exist_ok=True)
    fallback_url = f"sqlite:///{fallback_path}"
    logger.error("Primary database unavailable; starting with temporary SQLite fallback: %s", reason)
    try:
        engine.dispose()
    except Exception:
        pass
    engine = _make_engine(fallback_url)
    _active_url = fallback_url
    _create_schema()
    apply_feature_migrations()


def init_db(max_attempts: int = 5) -> None:
    global _startup_error
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            _create_schema()
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            _startup_error = ""
            apply_feature_migrations()
            logger.info("Database ready (%s)", database_kind())
            return
        except Exception as exc:
            last_error = exc
            logger.warning("Database startup attempt %s/%s failed: %s", attempt, max_attempts, exc)
            if attempt < max_attempts:
                time.sleep(min(attempt * 2, 8))
    if _active_url.startswith("sqlite"):
        raise RuntimeError(f"Could not initialize SQLite database: {last_error}") from last_error
    _fallback_to_sqlite(last_error or RuntimeError("Unknown database error"))


def database_ok() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError:
        return False


def database_kind() -> str:
    if _active_url.startswith("postgresql"):
        return "postgresql"
    if _startup_error:
        return "temporary-sqlite-fallback"
    return "sqlite"


def database_warning() -> str:
    if _startup_error:
        return "PostgreSQL পাওয়া যায়নি; app temporary storage-এ চলছে। Railway PostgreSQL যুক্ত করলে data স্থায়ী হবে।"
    return ""


def apply_feature_migrations() -> None:
    """Add v5.2 guided-flow tables without deleting existing Railway data."""
    statements = [
        """CREATE TABLE IF NOT EXISTS face_profiles(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL UNIQUE,
            reference_media_id TEXT NOT NULL,
            registered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(employee_id) REFERENCES employees(id)
        )""" if _active_url.startswith("sqlite") else """CREATE TABLE IF NOT EXISTS face_profiles(
            id BIGSERIAL PRIMARY KEY,
            employee_id BIGINT NOT NULL UNIQUE REFERENCES employees(id),
            reference_media_id TEXT NOT NULL,
            registered_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS attendance_evidence(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            latitude REAL,
            longitude REAL,
            distance_meters REAL,
            image_media_id TEXT,
            verified INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(employee_id) REFERENCES employees(id)
        )""" if _active_url.startswith("sqlite") else """CREATE TABLE IF NOT EXISTS attendance_evidence(
            id BIGSERIAL PRIMARY KEY,
            employee_id BIGINT NOT NULL REFERENCES employees(id),
            action TEXT NOT NULL,
            latitude DOUBLE PRECISION,
            longitude DOUBLE PRECISION,
            distance_meters DOUBLE PRECISION,
            image_media_id TEXT,
            verified BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
    ]
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))
