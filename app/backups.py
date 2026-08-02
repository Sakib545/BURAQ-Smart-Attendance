"""Portable, encrypted-capable disaster recovery backups.

The backup is intentionally database-engine independent: a dump created from
SQLite can be restored into PostgreSQL and vice versa after the application has
created its schema.  Face embeddings, attendance, payroll, settings and audit
history all live in the database and are therefore included.
"""
from __future__ import annotations

import asyncio
import base64
import gzip
import json
import logging
import os
import hashlib
import time as time_module
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import MetaData, delete, inspect, text
from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

APP_VERSION = "9.19.7"
from app import database

logger = logging.getLogger(__name__)
FORMAT = "buraq_full_backup"
VERSION = 1
MAGIC = b"BURAQBACKUP1\n"
REQUIRED_TABLES = {"employees", "attendance", "system_settings"}


def _backup_dir() -> Path:
    path = Path(os.getenv("BACKUP_DIR", "/data/backups"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _encode(value):
    if isinstance(value, (datetime, date, time)):
        return {"__type__": value.__class__.__name__, "value": value.isoformat()}
    if isinstance(value, Decimal):
        return {"__type__": "decimal", "value": str(value)}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"__type__": "bytes", "value": base64.b64encode(bytes(value)).decode("ascii")}
    return value


def _decode(value):
    if not isinstance(value, dict) or set(value) != {"__type__", "value"}:
        return value
    kind, raw = value["__type__"], value["value"]
    if kind == "datetime": return datetime.fromisoformat(raw)
    if kind == "date": return date.fromisoformat(raw)
    if kind == "time": return time.fromisoformat(raw)
    if kind == "decimal": return Decimal(raw)
    if kind == "bytes": return base64.b64decode(raw)
    return value


def _metadata() -> MetaData:
    metadata = MetaData()
    metadata.reflect(bind=database.engine)
    return metadata


def _fernet() -> Fernet | None:
    backup_secret = os.getenv("BACKUP_ENCRYPTION_KEY", "").strip()
    # A bad optional backup variable must not weaken encryption or crash the app.
    secret = backup_secret if len(backup_secret) >= 32 else os.getenv("CONFIG_ENCRYPTION_KEY", "").strip()
    if not secret:
        return None
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def _status_path() -> Path:
    return _backup_dir() / "backup-status.json"


def _write_status(**values) -> None:
    status = backup_status()
    status.update(values)
    status["updated_at"] = datetime.now(ZoneInfo(settings.timezone)).isoformat()
    target = _status_path(); temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(status, ensure_ascii=False, default=str), encoding="utf-8")
    temporary.replace(target)


def backup_status() -> dict:
    try:
        status = json.loads(_status_path().read_text(encoding="utf-8"))
    except Exception:
        status = {}
    files = sorted(_backup_dir().glob("buraq-full-*.buraq"), key=lambda p: p.stat().st_mtime, reverse=True)
    status.update({
        "local_count": len(files),
        "latest_file": files[0].name if files else "",
        "latest_size": files[0].stat().st_size if files else 0,
        "offsite_configured": all(os.getenv(key, "").strip() for key in
                                  ("BACKUP_S3_BUCKET", "BACKUP_S3_ACCESS_KEY_ID", "BACKUP_S3_SECRET_ACCESS_KEY")),
        "encrypted": _fernet() is not None,
    })
    return status


def validate_payload(payload: dict, known_tables: set[str] | None = None) -> dict:
    if payload.get("format") != FORMAT or payload.get("version") != VERSION:
        raise ValueError("Unsupported or invalid BURAQ full backup")
    tables = payload.get("tables")
    if not isinstance(tables, dict):
        raise ValueError("Backup has no database tables")
    missing = REQUIRED_TABLES - set(tables)
    if missing:
        raise ValueError("Backup is incomplete; missing: " + ", ".join(sorted(missing)))
    if known_tables is not None:
        unknown = set(tables) - known_tables
        if unknown:
            raise ValueError("Backup contains unknown tables: " + ", ".join(sorted(unknown)))
    expected = payload.get("table_counts", {})
    for name, rows in tables.items():
        if not isinstance(rows, list) or int(expected.get(name, -1)) != len(rows):
            raise ValueError(f"Backup row-count verification failed: {name}")
    return {"tables": len(tables), "rows": sum(len(rows) for rows in tables.values())}


def create_full_backup(target: Path | None = None) -> Path:
    """Create an atomic compressed dump of every application table."""
    now = datetime.now(ZoneInfo(settings.timezone))
    target = target or _backup_dir() / f"buraq-full-{now:%Y%m%d-%H%M%S}.buraq"
    metadata = _metadata()
    tables = {}
    counts = {}
    with database.engine.begin() as conn:
        if database.database_kind() == "postgresql":
            conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        for table in metadata.sorted_tables:
            rows = [
                {key: _encode(value) for key, value in row.items()}
                for row in conn.execute(table.select()).mappings()
            ]
            tables[table.name] = rows
            counts[table.name] = len(rows)
    payload = {
        "format": FORMAT,
        "version": VERSION,
        "created_at": now.isoformat(),
        "app_version": APP_VERSION,
        "source_database": database.database_kind(),
        "table_counts": counts,
        "tables": tables,
        "restore_notes": "Use the same CONFIG_ENCRYPTION_KEY to decrypt stored WhatsApp credentials.",
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=6)
    cipher = _fernet()
    content = MAGIC + cipher.encrypt(compressed) if cipher else compressed
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(target)
    verified = read_backup(target)
    totals = validate_payload(verified, {table.name for table in metadata.sorted_tables})
    _prune_local_backups()
    _write_status(last_local_success=now.isoformat(), last_error="", verified=True,
                  latest_rows=totals["rows"], latest_tables=totals["tables"])
    return target


def read_backup(path: Path) -> dict:
    content = path.read_bytes()
    if content.startswith(MAGIC):
        cipher = _fernet()
        if not cipher:
            raise ValueError("BACKUP_ENCRYPTION_KEY is required for this backup")
        try:
            content = cipher.decrypt(content[len(MAGIC):])
        except InvalidToken as exc:
            raise ValueError("Backup encryption key does not match") from exc
    try:
        payload = json.loads(gzip.decompress(content).decode("utf-8"))
    except Exception as exc:
        raise ValueError("Backup file is damaged or invalid") from exc
    validate_payload(payload)
    return payload


def inspect_backup(path: Path) -> dict:
    payload = read_backup(path)
    totals = validate_payload(payload)
    return {
        "valid": True,
        "created_at": payload.get("created_at"),
        "app_version": payload.get("app_version"),
        "source_database": payload.get("source_database"),
        **totals,
    }


def restore_full_backup(path: Path) -> dict:
    """Replace current rows transactionally after making a safety snapshot."""
    payload = read_backup(path)
    metadata = _metadata()
    known = {table.name: table for table in metadata.sorted_tables}
    validate_payload(payload, set(known))

    # A failed or mistaken restore remains recoverable.
    safety = create_full_backup(_backup_dir() / f"before-restore-{datetime.now():%Y%m%d-%H%M%S}.buraq")
    restored = {}
    with database.engine.begin() as conn:
        for table in reversed(metadata.sorted_tables):
            conn.execute(delete(table))
        for table in metadata.sorted_tables:
            rows = payload["tables"].get(table.name, [])
            decoded = [{key: _decode(value) for key, value in row.items()} for row in rows]
            if decoded:
                conn.execute(table.insert(), decoded)
            restored[table.name] = len(decoded)
        if database.database_kind() == "postgresql":
            for table in metadata.sorted_tables:
                pk = list(table.primary_key.columns)
                if len(pk) == 1 and str(pk[0].type).upper() in {"BIGINT", "INTEGER"}:
                    conn.execute(text(
                        "SELECT setval(pg_get_serial_sequence(:table,:column), "
                        "COALESCE((SELECT MAX(\"" + pk[0].name + "\") FROM \"" + table.name + "\"),1), "
                        "EXISTS(SELECT 1 FROM \"" + table.name + "\"))"
                    ), {"table": table.name, "column": pk[0].name})
    # Verify committed row counts using a fresh connection.
    with database.engine.connect() as conn:
        for table in metadata.sorted_tables:
            actual = conn.execute(text(f'SELECT COUNT(*) FROM "{table.name}"')).scalar_one()
            if actual != restored[table.name]:
                raise RuntimeError(f"Post-restore verification failed: {table.name}")
    _write_status(last_restore_success=datetime.now(ZoneInfo(settings.timezone)).isoformat(),
                  last_restore_source=payload.get("created_at"), last_error="")
    return {"restored": restored, "safety_backup": str(safety), "created_at": payload.get("created_at")}


def _prune_local_backups() -> None:
    keep = max(2, int(os.getenv("BACKUP_RETENTION_DAYS", "30")))
    files = sorted(_backup_dir().glob("buraq-full-*.buraq"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[keep:]:
        old.unlink(missing_ok=True)


def upload_offsite(path: Path) -> bool:
    """Upload to AWS S3, Cloudflare R2, Backblaze B2 or MinIO when configured."""
    bucket = os.getenv("BACKUP_S3_BUCKET", "").strip()
    access_key = os.getenv("BACKUP_S3_ACCESS_KEY_ID", "").strip()
    secret_key = os.getenv("BACKUP_S3_SECRET_ACCESS_KEY", "").strip()
    if not bucket or not access_key or not secret_key:
        return False
    import boto3
    client = boto3.client(
        "s3",
        endpoint_url=os.getenv("BACKUP_S3_ENDPOINT") or None,
        region_name=os.getenv("BACKUP_S3_REGION", "auto"),
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )
    prefix = os.getenv("BACKUP_S3_PREFIX", "buraq-attendance").strip("/")
    key = f"{prefix}/{path.name}"
    last_error = None
    for attempt in range(1, 4):
        try:
            client.upload_file(str(path), bucket, key)
            remote = client.head_object(Bucket=bucket, Key=key)
            if int(remote.get("ContentLength", -1)) != path.stat().st_size:
                raise RuntimeError("Remote backup size verification failed")
            _write_status(last_offsite_success=datetime.now(ZoneInfo(settings.timezone)).isoformat(),
                          last_offsite_key=key, last_error="")
            return True
        except Exception as exc:
            last_error = exc
            logger.warning("Off-site backup attempt %s/3 failed: %s", attempt, exc)
            if attempt < 3:
                time_module.sleep(attempt * 2)
    _write_status(last_error=f"Off-site upload failed: {last_error}")
    raise RuntimeError(f"Off-site backup failed after 3 attempts: {last_error}") from last_error


async def backup_worker():
    last_date = ""
    while True:
        try:
            today = datetime.now(ZoneInfo(settings.timezone)).date().isoformat()
            if today != last_date:
                path = await asyncio.to_thread(create_full_backup)
                offsite = await asyncio.to_thread(upload_offsite, path)
                last_date = today
                logger.info("Daily full backup saved path=%s offsite=%s", path, offsite)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            try: _write_status(last_error=str(exc), last_failure=datetime.now(ZoneInfo(settings.timezone)).isoformat())
            except Exception: pass
            logger.exception("Daily full backup failed")
        await asyncio.sleep(3600)


# Backwards-compatible names used by older imports.
create_payroll_backup = create_full_backup
payroll_backup_worker = backup_worker
