"""Bulk payroll operations: finalize the whole month in one click, and undo it.

Two things HR asked for, both built on tables that already exist:

* **One click to finalize.** ``/payroll`` could prepare every draft in one go
  (``bulk-prepare``) but then required finalizing them one at a time. This adds
  the missing counterpart — with a readiness check first, so nobody discovers
  after the fact that three payslips were silently skipped.

* **Undo a mistaken finalize.** ``payroll_change_logs`` has always stored a full
  JSON snapshot of every record before each action; nothing ever read it back.
  ``undo_last_batch`` does, restoring every record a batch touched to exactly
  the state it was in beforehand.

Bulk undo never rewrites ``paid`` records. An Admin can separately return a
paid record to Finalized with a reason and preserved payment snapshots.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from app.database import get_db
from app.special_duties import snapshot_blockers, lock_employee

logger = logging.getLogger(__name__)

# How long a bulk action stays one-click reversible. After this the per-record
# reopen is still available; this only governs the batch-level undo button.
UNDO_WINDOW_HOURS = 24

# Columns restored by an undo. Identity and audit columns are excluded on
# purpose — undoing a status change must not rewrite who created the record.
RESTORABLE_COLUMNS = (
    "fixed_salary", "overtime_hours", "overtime_rate", "overtime_amount",
    "bonus", "deduction", "net_salary", "payment_status", "note",
    "finalized_at", "paid_at", "locked_at", "locked_by",
    "payment_method", "payment_reference", "advance_amount", "fine_amount",
    "gross_salary", "total_deduction", "reopened_at", "reopen_reason",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- readiness

def _month_is_over(month: str) -> bool:
    """Has `month` (YYYY-MM) finished in the configured local timezone?

    Finalizing before the month ends locks a payslip whose remaining scheduled
    days all still count as absent, so the employee is short-paid for days they
    have not yet had the chance to work.
    """
    try:
        first = datetime.strptime(f"{month}-01", "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return True  # an unparseable month is not this check's problem
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    try:
        from zoneinfo import ZoneInfo

        from app.config import settings
        today = datetime.now(ZoneInfo(settings.timezone)).date()
    except Exception:
        today = _now().date()
    return today > last


def finalize_blockers(record, connection=None) -> list[str]:
    """Why this draft cannot be finalized yet. Empty list means it can.

    Mirrors the per-record checks in the single-finalize route exactly, so the
    preview never promises something the finalize would then refuse.
    """
    reasons: list[str] = []
    snapshot = {}
    try:
        snapshot = json.loads(record["calculation_snapshot"] or "{}")
    except (TypeError, ValueError):
        reasons.append("Calculation data is unreadable — re-prepare this payroll")

    try:
        month = str(record["salary_month"] or "")
    except (KeyError, IndexError, TypeError):
        month = ""
    if month and not _month_is_over(month):
        reasons.append("This month is not over yet — recalculate after month end before locking salary")

    if float(record["fixed_salary"] or 0) <= 0:
        reasons.append("Basic Salary is not set")
    if float(snapshot.get("scheduled") or 0) <= 0:
        reasons.append("No scheduled duty found for this month")
    if float(snapshot.get("net_salary") or 0) < 0:
        reasons.append("Net salary is negative")
    incomplete = snapshot.get("incomplete_dates") or []
    if incomplete:
        count = len(incomplete)
        reasons.append(
            f"{count} day{'s' if count != 1 else ''} without Check Out — review first"
        )
    reasons.extend(snapshot.get("special_duty_errors") or [])
    reasons.extend(snapshot_blockers(record, snapshot, connection))
    return reasons


def finalize_preview(month: str) -> dict:
    """What a bulk finalize would do, without doing it.

    Returns ready/blocked lists so HR sees the blocked names *before* pressing
    the button, rather than finding a silent skip count afterwards.
    """
    with get_db() as c:
        rows = c.execute(
            "SELECT p.*, e.staff_id, e.name, e.department "
            "FROM payroll_records p JOIN employees e ON e.id = p.employee_id "
            "WHERE p.salary_month = ? ORDER BY e.staff_id",
            (month,),
        ).fetchall()

    ready, blocked, already = [], [], []
    for row in rows:
        status = str(row["payment_status"] or "")
        entry = {
            "id": int(row["id"]),
            "staff_id": row["staff_id"],
            "name": row["name"],
            "department": row["department"],
            "net_salary": float(row["net_salary"] or 0),
            "status": status,
        }
        if status != "draft":
            already.append(entry)
            continue
        reasons = finalize_blockers(row)
        if reasons:
            entry["reasons"] = reasons
            blocked.append(entry)
        else:
            ready.append(entry)

    return {
        "month": month,
        "ready": ready,
        "blocked": blocked,
        "already": already,
        "ready_total": round(sum(r["net_salary"] for r in ready), 2),
    }


# ---------------------------------------------------------------- finalize

def bulk_finalize(month: str, actor: str) -> dict:
    """Finalize every draft in `month` that passes its checks.

    Records that fail are left as drafts and reported by name. Every record
    finalized is tagged with one batch id so the whole action can be undone
    together.
    """
    preview = finalize_preview(month)
    if not preview["ready"]:
        return {**preview, "batch_id": None, "finalized": 0}

    batch_id = f"finalize-{month}-{uuid.uuid4().hex[:8]}"
    finalized = 0

    with get_db() as c:
        for entry in preview["ready"]:
            row = c.execute(
                "SELECT * FROM payroll_records WHERE id=?", (entry["id"],)
            ).fetchone()
            # Re-check under the same connection: a colleague may have
            # finalized or discarded this record while HR read the preview.
            if not row or str(row["payment_status"]) != "draft":
                continue
            lock_employee(c,row["employee_id"])
            row=c.execute("SELECT * FROM payroll_records WHERE id=?",(entry["id"],)).fetchone()
            if str(row["payment_status"]) != "draft" or finalize_blockers(row, connection=c):
                continue

            # Snapshot BEFORE the update — this is what undo restores.
            c.execute(
                "INSERT INTO payroll_change_logs(payroll_id,action,actor,reason,snapshot) "
                "VALUES(?,?,?,?,?)",
                (entry["id"], "bulk_finalized", actor, batch_id,
                 json.dumps(dict(row), default=str)),
            )
            c.execute(
                "UPDATE payroll_records SET payment_status='finalized',"
                "finalized_at=CURRENT_TIMESTAMP,locked_at=CURRENT_TIMESTAMP,locked_by=?,"
                "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (actor, entry["id"]),
            )
            finalized += 1

    logger.info("Bulk finalize %s: %s record(s), batch=%s", month, finalized, batch_id)
    return {**preview, "batch_id": batch_id, "finalized": finalized}


# ---------------------------------------------------------------- undo

def last_undoable_batch(month: str) -> dict | None:
    """The most recent bulk finalize for `month` that can still be undone.

    A batch stops being undoable once any of its records has been paid, or once
    the undo window has passed.
    """
    with get_db() as c:
        rows = c.execute(
            "SELECT l.reason batch_id, MAX(l.created_at) at, COUNT(*) n "
            "FROM payroll_change_logs l JOIN payroll_records p ON p.id = l.payroll_id "
            "WHERE l.action='bulk_finalized' AND p.salary_month=? "
            "GROUP BY l.reason ORDER BY MAX(l.created_at) DESC LIMIT 1",
            (month,),
        ).fetchall()
        if not rows:
            return None
        batch = rows[0]
        batch_id = str(batch["batch_id"])

        states = c.execute(
            "SELECT DISTINCT p.payment_status FROM payroll_change_logs l "
            "JOIN payroll_records p ON p.id = l.payroll_id "
            "WHERE l.action='bulk_finalized' AND l.reason=?",
            (batch_id,),
        ).fetchall()

    statuses = {str(s["payment_status"]) for s in states}
    if "paid" in statuses:
        return {"batch_id": batch_id, "count": int(batch["n"]), "at": batch["at"],
                "undoable": False, "why": "Some payslips in this batch are already paid"}
    if "finalized" not in statuses:
        return {"batch_id": batch_id, "count": int(batch["n"]), "at": batch["at"],
                "undoable": False, "why": "This batch has already been undone"}

    age_ok = True
    try:
        when = datetime.fromisoformat(str(batch["at"]).replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        age_ok = _now() - when <= timedelta(hours=UNDO_WINDOW_HOURS)
    except (TypeError, ValueError):
        pass

    return {
        "batch_id": batch_id,
        "count": int(batch["n"]),
        "at": batch["at"],
        "undoable": age_ok,
        "why": None if age_ok else f"Older than {UNDO_WINDOW_HOURS} hours — reopen individually",
    }


def undo_batch(batch_id: str, actor: str) -> dict:
    """Restore every record in `batch_id` to its pre-finalize snapshot."""
    restored, skipped = 0, []

    with get_db() as c:
        entries = c.execute(
            "SELECT payroll_id, snapshot FROM payroll_change_logs "
            "WHERE action='bulk_finalized' AND reason=? ORDER BY id",
            (batch_id,),
        ).fetchall()

        if not entries:
            return {"restored": 0, "skipped": [], "error": "Batch not found"}

        for entry in entries:
            payroll_id = int(entry["payroll_id"])
            current = c.execute(
                "SELECT payment_status, employee_id FROM payroll_records WHERE id=?",
                (payroll_id,),
            ).fetchone()
            if not current:
                continue
            status = str(current["payment_status"])
            if status == "paid":
                # Never rewrite a paid record — see the module docstring.
                skipped.append({"payroll_id": payroll_id, "reason": "already paid"})
                continue
            if status != "finalized":
                skipped.append({"payroll_id": payroll_id, "reason": f"now {status}"})
                continue

            try:
                snapshot = json.loads(entry["snapshot"] or "{}")
            except (TypeError, ValueError):
                skipped.append({"payroll_id": payroll_id, "reason": "snapshot unreadable"})
                continue

            columns = [col for col in RESTORABLE_COLUMNS if col in snapshot]
            if not columns:
                skipped.append({"payroll_id": payroll_id, "reason": "empty snapshot"})
                continue

            assignments = ",".join(f"{col}=?" for col in columns)
            values = [snapshot[col] for col in columns]
            c.execute(
                f"UPDATE payroll_records SET {assignments},updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (*values, payroll_id),
            )
            c.execute(
                "INSERT INTO payroll_change_logs(payroll_id,action,actor,reason,snapshot) "
                "VALUES(?,?,?,?,?)",
                (payroll_id, "undo_finalize", actor, batch_id, entry["snapshot"]),
            )
            restored += 1

    logger.info("Undo batch %s: restored %s, skipped %s", batch_id, restored, len(skipped))
    return {"restored": restored, "skipped": skipped, "error": None}


# ---------------------------------------------------------------- history

def record_history(payroll_id: int, limit: int = 12) -> list[dict]:
    """Human-readable 'who changed what, when' for one payslip."""
    with get_db() as c:
        rows = c.execute(
            "SELECT action, actor, reason, created_at, snapshot FROM payroll_change_logs "
            "WHERE payroll_id=? ORDER BY id DESC LIMIT ?",
            (payroll_id, limit),
        ).fetchall()

    labels = {
        "draft": "Prepared as draft",
        "finalized": "Finalized",
        "bulk_finalized": "Finalized (bulk)",
        "undo_finalize": "Finalize undone",
        "reopened": "Reopened for editing",
        "paid": "Marked paid",
        "payment_returned": "Previous payment preserved",
        "returned_to_finalized": "Returned from Paid to Finalized",
        "updated": "Edited",
        "discarded": "Discarded",
    }
    def payment_details(row):
        if row['action'] not in {'paid', 'payment_returned'}:
            return ''
        try:
            snapshot = json.loads(row['snapshot'] or '{}')
            return ' · '.join(str(snapshot.get(key) or '—') for key in
                              ('payment_method', 'payment_reference', 'paid_at', 'net_salary'))
        except (ValueError, TypeError, AttributeError):
            return ''

    return [
        {
            "action": str(r["action"]),
            "label": labels.get(str(r["action"]), str(r["action"]).replace("_", " ").title()),
            "actor": r["actor"] or "—",
            "reason": r["reason"] or "",
            "at": r["created_at"],
            "payment_details": payment_details(r),
        }
        for r in rows
    ]
