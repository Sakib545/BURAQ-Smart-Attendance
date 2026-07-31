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
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import MetaData, delete, inspect, text
from cryptography.fernet import Fernet, InvalidToken

from app.config import settings
from app import database

logger = logging.getLogger(__name__)
FORMAT = "buraq_full_backup"
VERSION = 1
MAGIC = b"BURAQBACKUP1\n"


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
    secret = (os.getenv("BACKUP_ENCRYPTION_KEY") or os.getenv("CONFIG_ENCRYPTION_KEY") or "").strip()
    if not secret:
        return None
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def create_full_backup(target: Path | None = None) -> Path:
    """Create an atomic compressed dump of every application table."""
    now = datetime.now(ZoneInfo(settings.timezone))
    target = target or _backup_dir() / f"buraq-full-{now:%Y%m%d-%H%M%S}.buraq"
    metadata = _metadata()
    tables = {}
    counts = {}
    with database.engine.connect() as conn:
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
        "app_version": "9.14.0",
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
    _prune_local_backups()
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
    if payload.get("format") != FORMAT or payload.get("version") != VERSION:
        raise ValueError("Unsupported or invalid BURAQ full backup")
    if not isinstance(payload.get("tables"), dict):
        raise ValueError("Backup has no database tables")
    return payload


def restore_full_backup(path: Path) -> dict:
    """Replace current rows transactionally after making a safety snapshot."""
    payload = read_backup(path)
    metadata = _metadata()
    known = {table.name: table for table in metadata.sorted_tables}
    unknown = set(payload["tables"]) - set(known)
    if unknown:
        raise ValueError("Backup contains unknown tables: " + ", ".join(sorted(unknown)))

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
    return {"restored": restored, "safety_backup": str(safety), "created_at": payload.get("created_at")}


def _prune_local_backups() -> None:
    keep = max(2, int(os.getenv("BACKUP_RETENTION_DAYS", "30")))
    files = sorted(_backup_dir().glob("buraq-full-*.buraq"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[keep:]:
        old.unlink(missing_ok=True)


def upload_offsite(path: Path) -> bool:
    """Upload to AWS S3, Cloudflare R2, Backblaze B2 or MinIO when configured."""
    bucket = os.getenv("BACKUP_S3_BUCKET", "").strip()
    if not bucket:
        return False
    import boto3
    client = boto3.client(
        "s3",
        endpoint_url=os.getenv("BACKUP_S3_ENDPOINT") or None,
        region_name=os.getenv("BACKUP_S3_REGION", "auto"),
        aws_access_key_id=os.getenv("BACKUP_S3_ACCESS_KEY_ID") or None,
        aws_secret_access_key=os.getenv("BACKUP_S3_SECRET_ACCESS_KEY") or None,
    )
    prefix = os.getenv("BACKUP_S3_PREFIX", "buraq-attendance").strip("/")
    client.upload_file(str(path), bucket, f"{prefix}/{path.name}")
    return True


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
        except Exception:
            logger.exception("Daily full backup failed")
        await asyncio.sleep(3600)


# Backwards-compatible names used by older imports.
create_payroll_backup = create_full_backup
payroll_backup_worker = backup_worker
