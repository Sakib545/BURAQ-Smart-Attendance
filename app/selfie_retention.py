"""Expire new attendance thumbnails only; never delete attendance rows.

The persistent high-water mark protects every pre-policy photo, including on
restart. Face profiles, embeddings, hashes and review metadata are untouched.
"""
import asyncio
import logging

from sqlalchemy import text
from app import database

logger = logging.getLogger(__name__)
POLICY_KEY = "attendance_selfie_retention_v1_after_id"
RETENTION_HOURS = 48


def initialize_policy():
    # One atomic, idempotent insert: multiple workers cannot move the boundary.
    with database.engine.begin() as conn:
        conn.execute(text("""INSERT INTO system_settings(key,value)
            SELECT :key, CAST(COALESCE(MAX(id),0) AS TEXT)
            FROM attendance_fingerprints WHERE 1=1
            ON CONFLICT(key) DO NOTHING"""), {"key": POLICY_KEY})


def _expired_predicate(alias=""):
    prefix = f"{alias}." if alias else ""
    cutoff = ("datetime('now', '-48 hours')" if database.database_kind() == "sqlite"
              else "CURRENT_TIMESTAMP - INTERVAL '48 hours'")
    return (f"{prefix}id > (SELECT CAST(value AS BIGINT) FROM system_settings "
            f"WHERE key='{POLICY_KEY}') AND {prefix}created_at <= {cutoff}")


def preview_sql(alias="f"):
    """Hide expired images even between cleanup runs; preserve pre-policy images."""
    return f"CASE WHEN {_expired_predicate(alias)} THEN NULL ELSE {alias}.image_data END AS image_data"


def expire_selfie_images():
    """Only clear thumbnail payloads. No DELETE, schema change or status change."""
    with database.engine.begin() as conn:
        result = conn.execute(text("UPDATE attendance_fingerprints SET image_data=NULL "
                                   f"WHERE image_data IS NOT NULL AND {_expired_predicate()}"))
        return result.rowcount


async def selfie_retention_worker():
    while True:
        try:
            count = await asyncio.to_thread(expire_selfie_images)
            if count:
                logger.info("Expired attendance selfie thumbnails count=%s", count)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Attendance selfie cleanup failed; will retry")
        await asyncio.sleep(60)
