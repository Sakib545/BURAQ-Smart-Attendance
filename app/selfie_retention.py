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


def cleanup_reviewed_historical_images():
    """One atomic pass over reviewed photos protected by the original rollout."""
    marker = "attendance_reviewed_historical_cleanup_v1"
    with database.engine.begin() as conn:
        boundary = conn.execute(text("SELECT value FROM system_settings WHERE key=:key"),
                                {"key": POLICY_KEY}).scalar_one_or_none()
        if boundary is None:
            return
        claimed = conn.execute(text("INSERT INTO system_settings(key,value) VALUES(:k,'running') "
                                    "ON CONFLICT(key) DO NOTHING RETURNING key"), {"k": marker}).first()
        if not claimed:
            return
        cutoff = ("CURRENT_TIMESTAMP - INTERVAL '48 hours'" if database.database_kind() == "postgresql"
                  else "datetime('now','-48 hours')")
        where = (f"id <= :boundary AND created_at <= {cutoff} "
                 "AND review_status IN ('approved','rejected') AND image_data IS NOT NULL AND image_data <> ''")
        params = {"boundary": int(boundary)}
        # Thumbnails are ASCII base64, so length is the payload size in bytes.
        count, size = conn.execute(text("SELECT COUNT(*),COALESCE(SUM(length(image_data)),0) "
                                        "FROM attendance_fingerprints WHERE " + where), params).one()
        updated = conn.execute(text("UPDATE attendance_fingerprints SET image_data=NULL WHERE " + where), params)
        if updated.rowcount != count:
            raise RuntimeError("Candidate count changed; rolling back historical cleanup")
        result = f"photos={count} payload_bytes={size}"
        conn.execute(text("UPDATE system_settings SET value=:v WHERE key=:k"), {"v": result, "k": marker})
    logger.info("Historical reviewed selfie cleanup %s", result)


async def selfie_retention_worker():
    try:
        await asyncio.to_thread(cleanup_reviewed_historical_images)
    except Exception:
        logger.exception("Historical selfie cleanup rolled back; retry on next startup")
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
