"""Face AI telemetry and the cached cross-employee gallery.

Every enrolment and verification attempt writes one row to `face_events`. That
table is what makes threshold tuning possible: without recorded score
distributions, any change to FACE_MATCH_THRESHOLD is guesswork.

Logging must never break attendance, so every failure here is swallowed.
"""

from __future__ import annotations

import json
import logging
import threading
import time

from app.database import get_db

logger = logging.getLogger(__name__)

# The impostor gallery is read on every check-in. Re-parsing every embedding
# each time is wasteful, so it is cached and refreshed when the sample table
# changes or the TTL expires.
_CACHE_TTL_SECONDS = 300
_cache_lock = threading.Lock()
_cache: dict[str, object] = {"loaded_at": 0.0, "signature": None, "rows": []}


def load_impostor_gallery(employee_id: int) -> list[tuple[int, list]]:
    """Return (employee_id, embedding) for every employee except this one."""
    try:
        with get_db() as c:
            stamp = c.execute("SELECT COUNT(*) n, COALESCE(MAX(id),0) m FROM face_samples").fetchone()
        signature = (int(stamp["n"] or 0), int(stamp["m"] or 0))

        with _cache_lock:
            fresh = (time.time() - float(_cache["loaded_at"])) < _CACHE_TTL_SECONDS
            if not fresh or _cache["signature"] != signature:
                with get_db() as c:
                    rows = c.execute("SELECT employee_id, embedding FROM face_samples").fetchall()
                _cache["rows"] = [(int(r["employee_id"]), json.loads(r["embedding"])) for r in rows]
                _cache["signature"] = signature
                _cache["loaded_at"] = time.time()
            cached = list(_cache["rows"])

        return [(eid, embedding) for eid, embedding in cached if eid != employee_id]
    except Exception:
        logger.exception("impostor gallery load failed")
        return []


def invalidate_gallery_cache() -> None:
    with _cache_lock:
        _cache["loaded_at"] = 0.0


def log_face_event(
    *,
    employee_id: int | None,
    stage: str,
    decision: str,
    reason: str = "",
    action: str = "",
    match_score: float = 0.0,
    impostor_score: float = 0.0,
    impostor_employee_id: int | None = None,
    quality: float = 0.0,
    elapsed_ms: float = 0.0,
    diagnostics: dict | None = None,
) -> None:
    diagnostics = diagnostics or {}
    try:
        with get_db() as c:
            c.execute(
                """INSERT INTO face_events(
                    employee_id, stage, action, decision, reason,
                    match_score, impostor_score, margin, impostor_employee_id,
                    quality, blur, brightness, face_ratio, pose,
                    liveness_score, liveness_verdict, liveness_detail, liveness_model, elapsed_ms)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    employee_id, stage, action, decision, reason[:200],
                    round(float(match_score), 6), round(float(impostor_score), 6),
                    round(float(match_score) - float(impostor_score), 6), impostor_employee_id,
                    round(float(quality), 3),
                    float(diagnostics.get("blur", 0.0)),
                    float(diagnostics.get("brightness", 0.0)),
                    float(diagnostics.get("face_ratio", 0.0)),
                    diagnostics.get("pose", ""),
                    float(diagnostics.get("liveness_score", 0.0)),
                    diagnostics.get("liveness_verdict", ""),
                    json.dumps(diagnostics.get("liveness_components", {})),
                    # Must be a real bool: on Postgres this column is BOOLEAN and
                    # an integer 0/1 is rejected outright. SQLite stores it as 1/0.
                    bool(diagnostics.get("liveness_model")),
                    round(float(elapsed_ms), 2),
                ),
            )
    except Exception:
        # Telemetry is never allowed to stop an employee from checking in.
        logger.exception("face event logging failed")
