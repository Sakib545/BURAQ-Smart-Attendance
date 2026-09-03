"""Bulk payroll finalize, its readiness check, and undo."""
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("DATABASE_PATH", "/tmp/buraq_payops_test.db")
os.environ.setdefault("REQUIRE_SECURE_SECRETS", "false")
os.environ.setdefault("ALLOW_TEMP_DB_FALLBACK", "false")
os.environ.setdefault("SESSION_SECRET", "test-session-secret-01234567890123456789")
os.environ.setdefault("CONFIG_ENCRYPTION_KEY", "test-config-secret-0123456789012345678")

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app import payroll_ops

MONTH = "2026-07"
STAFF = ["POPS-READY-1", "POPS-READY-2", "POPS-NOSALARY", "POPS-NODUTY"]


def _good_snapshot(net=15000.0):
    return json.dumps({"scheduled": 26, "worked": 24, "net_salary": net,
                       "incomplete_dates": []})


def _seed(staff_id, fixed_salary, snapshot, status="draft"):
    with get_db() as db:
        row = db.execute("SELECT id FROM employees WHERE staff_id=?", (staff_id,)).fetchone()
        if row:
            db.execute("DELETE FROM payroll_records WHERE employee_id=?", (row["id"],))
        db.execute("DELETE FROM employees WHERE staff_id=?", (staff_id,))
        db.execute(
            "INSERT INTO employees(staff_id,name,shift,registration_status,is_active,fixed_salary) "
            "VALUES(?,?,?,?,?,?)",
            (staff_id, staff_id, "morning", "approved", True, fixed_salary),
        )
        eid = db.execute("SELECT id FROM employees WHERE staff_id=?", (staff_id,)).fetchone()["id"]
        db.execute(
            "INSERT INTO payroll_records(employee_id,salary_month,fixed_salary,net_salary,"
            "payment_status,calculation_snapshot,created_by,updated_by) VALUES(?,?,?,?,?,?,?,?)",
            (eid, MONTH, fixed_salary, 15000.0, status, snapshot, "test", "test"),
        )
        return eid


@pytest.fixture(autouse=True)
def seeded():
    with TestClient(app):
        _seed("POPS-READY-1", 15000.0, _good_snapshot())
        _seed("POPS-READY-2", 18000.0, _good_snapshot(18000.0))
        _seed("POPS-NOSALARY", 0.0, _good_snapshot())
        _seed("POPS-NODUTY", 12000.0,
              json.dumps({"scheduled": 0, "worked": 0, "net_salary": 0, "incomplete_dates": []}))
        yield
        with get_db() as db:
            for staff_id in STAFF:
                row = db.execute("SELECT id FROM employees WHERE staff_id=?", (staff_id,)).fetchone()
                if row:
                    db.execute("DELETE FROM payroll_change_logs WHERE payroll_id IN "
                               "(SELECT id FROM payroll_records WHERE employee_id=?)", (row["id"],))
                    db.execute("DELETE FROM payroll_records WHERE employee_id=?", (row["id"],))
                    db.execute("DELETE FROM employees WHERE id=?", (row["id"],))


def _status(staff_id):
    with get_db() as db:
        return db.execute(
            "SELECT p.payment_status FROM payroll_records p JOIN employees e ON e.id=p.employee_id "
            "WHERE e.staff_id=? AND p.salary_month=?", (staff_id, MONTH)
        ).fetchone()["payment_status"]


# --- readiness ---------------------------------------------------------------

def test_preview_separates_ready_from_blocked():
    preview = payroll_ops.finalize_preview(MONTH)
    ready = {r["staff_id"] for r in preview["ready"]}
    blocked = {b["staff_id"] for b in preview["blocked"]}
    assert "POPS-READY-1" in ready and "POPS-READY-2" in ready
    assert "POPS-NOSALARY" in blocked and "POPS-NODUTY" in blocked


def test_blocked_entries_say_why():
    preview = payroll_ops.finalize_preview(MONTH)
    reasons = {b["staff_id"]: " ".join(b["reasons"]) for b in preview["blocked"]}
    assert "Basic Salary" in reasons["POPS-NOSALARY"]
    assert "scheduled duty" in reasons["POPS-NODUTY"]


def test_incomplete_checkout_blocks_finalize():
    _seed("POPS-READY-1", 15000.0,
          json.dumps({"scheduled": 26, "worked": 24, "net_salary": 15000,
                      "incomplete_dates": ["2026-07-03", "2026-07-11"]}))
    preview = payroll_ops.finalize_preview(MONTH)
    blocked = {b["staff_id"]: b["reasons"] for b in preview["blocked"]}
    assert "POPS-READY-1" in blocked
    assert any("Check Out" in r for r in blocked["POPS-READY-1"])


def test_negative_net_salary_blocks_finalize():
    _seed("POPS-READY-1", 15000.0,
          json.dumps({"scheduled": 26, "worked": 24, "net_salary": -200,
                      "incomplete_dates": []}))
    preview = payroll_ops.finalize_preview(MONTH)
    assert "POPS-READY-1" in {b["staff_id"] for b in preview["blocked"]}


def test_ready_total_sums_only_ready_records():
    preview = payroll_ops.finalize_preview(MONTH)
    assert preview["ready_total"] == pytest.approx(30000.0)


# --- finalize ----------------------------------------------------------------

def test_bulk_finalize_locks_ready_and_leaves_blocked_as_draft():
    result = payroll_ops.bulk_finalize(MONTH, "tester")
    assert result["finalized"] == 2
    assert _status("POPS-READY-1") == "finalized"
    assert _status("POPS-READY-2") == "finalized"
    # The blocked ones must remain editable, not be silently skipped forever.
    assert _status("POPS-NOSALARY") == "draft"
    assert _status("POPS-NODUTY") == "draft"


def test_finalize_is_tagged_with_one_batch_id():
    result = payroll_ops.bulk_finalize(MONTH, "tester")
    with get_db() as db:
        batches = db.execute(
            "SELECT DISTINCT reason FROM payroll_change_logs WHERE action='bulk_finalized'"
        ).fetchall()
    assert {str(b["reason"]) for b in batches} == {result["batch_id"]}


def test_running_finalize_twice_does_nothing_the_second_time():
    payroll_ops.bulk_finalize(MONTH, "tester")
    again = payroll_ops.bulk_finalize(MONTH, "tester")
    assert again["finalized"] == 0


# --- undo --------------------------------------------------------------------

def test_undo_restores_every_record_in_the_batch():
    result = payroll_ops.bulk_finalize(MONTH, "tester")
    undone = payroll_ops.undo_batch(result["batch_id"], "tester")
    assert undone["restored"] == 2
    assert _status("POPS-READY-1") == "draft"
    assert _status("POPS-READY-2") == "draft"


def test_undo_clears_the_finalize_timestamps():
    result = payroll_ops.bulk_finalize(MONTH, "tester")
    payroll_ops.undo_batch(result["batch_id"], "tester")
    with get_db() as db:
        row = db.execute(
            "SELECT p.finalized_at, p.locked_at FROM payroll_records p "
            "JOIN employees e ON e.id=p.employee_id WHERE e.staff_id=? AND p.salary_month=?",
            ("POPS-READY-1", MONTH),
        ).fetchone()
    assert not row["finalized_at"]
    assert not row["locked_at"]


def test_a_paid_record_is_never_rewritten_by_undo():
    result = payroll_ops.bulk_finalize(MONTH, "tester")
    with get_db() as db:
        db.execute(
            "UPDATE payroll_records SET payment_status='paid' WHERE employee_id="
            "(SELECT id FROM employees WHERE staff_id=?) AND salary_month=?",
            ("POPS-READY-1", MONTH),
        )
    undone = payroll_ops.undo_batch(result["batch_id"], "tester")
    assert _status("POPS-READY-1") == "paid"
    assert _status("POPS-READY-2") == "draft"
    assert any(s["reason"] == "already paid" for s in undone["skipped"])


def test_batch_stops_being_undoable_once_anything_is_paid():
    payroll_ops.bulk_finalize(MONTH, "tester")
    with get_db() as db:
        db.execute(
            "UPDATE payroll_records SET payment_status='paid' WHERE employee_id="
            "(SELECT id FROM employees WHERE staff_id=?) AND salary_month=?",
            ("POPS-READY-1", MONTH),
        )
    batch = payroll_ops.last_undoable_batch(MONTH)
    assert batch["undoable"] is False
    assert "paid" in batch["why"].lower()


def test_undoing_twice_is_reported_not_repeated():
    result = payroll_ops.bulk_finalize(MONTH, "tester")
    payroll_ops.undo_batch(result["batch_id"], "tester")
    batch = payroll_ops.last_undoable_batch(MONTH)
    assert batch["undoable"] is False
    second = payroll_ops.undo_batch(result["batch_id"], "tester")
    assert second["restored"] == 0


def test_unknown_batch_reports_an_error():
    assert payroll_ops.undo_batch("finalize-nope-00000000", "tester")["error"]


# --- history -----------------------------------------------------------------

def test_history_reads_back_in_plain_language():
    result = payroll_ops.bulk_finalize(MONTH, "tester")
    payroll_ops.undo_batch(result["batch_id"], "tester")
    with get_db() as db:
        pid = db.execute(
            "SELECT p.id FROM payroll_records p JOIN employees e ON e.id=p.employee_id "
            "WHERE e.staff_id=? AND p.salary_month=?", ("POPS-READY-1", MONTH)
        ).fetchone()["id"]
    history = payroll_ops.record_history(pid)
    labels = [h["label"] for h in history]
    assert "Finalize undone" in labels
    assert "Finalized (bulk)" in labels


# --- legacy snapshot regression ---------------------------------------------

def test_salary_sheet_survives_a_snapshot_written_by_an_older_release():
    """A finalized record keeps whatever JSON its release wrote.

    A snapshot from before `total_deduction` existed used to take the whole
    /payroll page down with a KeyError — one old payslip made the month
    unviewable for everyone.
    """
    from app.main import _salary_sheet_rows

    with get_db() as db:
        eid = db.execute(
            "SELECT id FROM employees WHERE staff_id=?", ("POPS-READY-1",)
        ).fetchone()["id"]
        db.execute(
            "UPDATE payroll_records SET payment_status='finalized',calculation_snapshot=? "
            "WHERE employee_id=? AND salary_month=?",
            (json.dumps({"scheduled": 26, "worked": 24, "net_salary": 15000,
                         "incomplete_dates": []}), eid, MONTH),
        )

    rows = _salary_sheet_rows(MONTH)
    row = next(r for r in rows if r["staff_id"] == "POPS-READY-1")
    # Every key the salary sheet reads must be present, defaulted if absent.
    for key in ("total_deduction", "gross_salary", "overtime_amount", "late_minutes"):
        assert key in row, f"{key} missing — the page would raise KeyError"
