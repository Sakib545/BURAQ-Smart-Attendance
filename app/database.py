from contextlib import contextmanager
import re
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Result
from app.config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    future=True,
    connect_args=connect_args,
)

SQLITE_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS employees(id INTEGER PRIMARY KEY AUTOINCREMENT,staff_id TEXT NOT NULL UNIQUE COLLATE NOCASE,name TEXT NOT NULL,phone TEXT UNIQUE,department TEXT,shift TEXT NOT NULL DEFAULT 'morning',registration_status TEXT NOT NULL DEFAULT 'unregistered',whatsapp_phone TEXT UNIQUE,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS conversation_states(phone TEXT PRIMARY KEY,state TEXT NOT NULL,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS attendance(id INTEGER PRIMARY KEY AUTOINCREMENT,employee_id INTEGER NOT NULL,work_date TEXT NOT NULL,check_in TEXT,check_out TEXT,late_minutes INTEGER NOT NULL DEFAULT 0,early_leave_minutes INTEGER NOT NULL DEFAULT 0,overtime_minutes INTEGER NOT NULL DEFAULT 0,source TEXT NOT NULL DEFAULT 'whatsapp',status TEXT NOT NULL DEFAULT 'present',created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(employee_id,work_date),FOREIGN KEY(employee_id) REFERENCES employees(id));
CREATE TABLE IF NOT EXISTS whatsapp_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,direction TEXT NOT NULL,phone TEXT,message_type TEXT,content TEXT,message_id TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE UNIQUE INDEX IF NOT EXISTS ux_whatsapp_incoming_message_id ON whatsapp_logs(message_id) WHERE direction='incoming' AND message_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS pending_registrations(id INTEGER PRIMARY KEY AUTOINCREMENT,employee_id INTEGER NOT NULL,whatsapp_phone TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,FOREIGN KEY(employee_id) REFERENCES employees(id));
"""

POSTGRES_STATEMENTS = [
"""CREATE TABLE IF NOT EXISTS employees(id BIGSERIAL PRIMARY KEY,staff_id TEXT NOT NULL UNIQUE,name TEXT NOT NULL,phone TEXT UNIQUE,department TEXT,shift TEXT NOT NULL DEFAULT 'morning',registration_status TEXT NOT NULL DEFAULT 'unregistered',whatsapp_phone TEXT UNIQUE,created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
"""CREATE UNIQUE INDEX IF NOT EXISTS ux_employees_staff_id_lower ON employees(LOWER(staff_id))""",
"""CREATE TABLE IF NOT EXISTS conversation_states(phone TEXT PRIMARY KEY,state TEXT NOT NULL,updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
"""CREATE TABLE IF NOT EXISTS attendance(id BIGSERIAL PRIMARY KEY,employee_id BIGINT NOT NULL REFERENCES employees(id),work_date TEXT NOT NULL,check_in TEXT,check_out TEXT,late_minutes INTEGER NOT NULL DEFAULT 0,early_leave_minutes INTEGER NOT NULL DEFAULT 0,overtime_minutes INTEGER NOT NULL DEFAULT 0,source TEXT NOT NULL DEFAULT 'whatsapp',status TEXT NOT NULL DEFAULT 'present',created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(employee_id,work_date))""",
"""CREATE TABLE IF NOT EXISTS whatsapp_logs(id BIGSERIAL PRIMARY KEY,direction TEXT NOT NULL,phone TEXT,message_type TEXT,content TEXT,message_id TEXT,created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
"""CREATE UNIQUE INDEX IF NOT EXISTS ux_whatsapp_incoming_message_id ON whatsapp_logs(message_id) WHERE direction='incoming' AND message_id IS NOT NULL""",
"""CREATE TABLE IF NOT EXISTS pending_registrations(id BIGSERIAL PRIMARY KEY,employee_id BIGINT NOT NULL REFERENCES employees(id),whatsapp_phone TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
]


class DBResult:
    def __init__(self, result: Result):
        self.result = result

    def fetchone(self):
        row = self.result.mappings().fetchone()
        return row

    def fetchall(self):
        return self.result.mappings().fetchall()


class DBConnection:
    def __init__(self, conn: Connection):
        self.conn = conn

    def execute(self, sql: str, params=()):
        # Backward-compatible adapter for the original SQLite-style ? placeholders.
        if isinstance(params, dict):
            result = self.conn.execute(text(sql), params)
        else:
            params = tuple(params or ())
            names = []
            def repl(_):
                name = f"p{len(names)}"
                names.append(name)
                return f":{name}"
            converted = re.sub(r"\?", repl, sql)
            result = self.conn.execute(text(converted), {name: params[i] for i, name in enumerate(names)})
        return DBResult(result)


@contextmanager
def get_db():
    with engine.begin() as conn:
        yield DBConnection(conn)


def init_db():
    if settings.database_url.startswith("sqlite"):
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


def database_ok() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
