import sqlite3
from contextlib import contextmanager
from app.config import settings
SCHEMA="""
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS employees(id INTEGER PRIMARY KEY AUTOINCREMENT,staff_id TEXT NOT NULL UNIQUE COLLATE NOCASE,name TEXT NOT NULL,phone TEXT UNIQUE,department TEXT,shift TEXT NOT NULL DEFAULT 'morning',registration_status TEXT NOT NULL DEFAULT 'unregistered',whatsapp_phone TEXT UNIQUE,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS conversation_states(phone TEXT PRIMARY KEY,state TEXT NOT NULL,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS attendance(id INTEGER PRIMARY KEY AUTOINCREMENT,employee_id INTEGER NOT NULL,work_date TEXT NOT NULL,check_in TEXT,check_out TEXT,late_minutes INTEGER NOT NULL DEFAULT 0,early_leave_minutes INTEGER NOT NULL DEFAULT 0,overtime_minutes INTEGER NOT NULL DEFAULT 0,source TEXT NOT NULL DEFAULT 'whatsapp',status TEXT NOT NULL DEFAULT 'present',created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(employee_id,work_date),FOREIGN KEY(employee_id) REFERENCES employees(id));
CREATE TABLE IF NOT EXISTS whatsapp_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,direction TEXT NOT NULL,phone TEXT,message_type TEXT,content TEXT,message_id TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS pending_registrations(id INTEGER PRIMARY KEY AUTOINCREMENT,employee_id INTEGER NOT NULL,whatsapp_phone TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,FOREIGN KEY(employee_id) REFERENCES employees(id));
"""
@contextmanager
def get_db():
    conn=sqlite3.connect(settings.database_path); conn.row_factory=sqlite3.Row; conn.execute("PRAGMA foreign_keys=ON")
    try: yield conn; conn.commit()
    finally: conn.close()
def init_db():
    with get_db() as conn: conn.executescript(SCHEMA)
