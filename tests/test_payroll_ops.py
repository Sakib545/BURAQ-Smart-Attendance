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

# --- Admin-only return from Paid to Finalized ---------------------------------

def _session_client(role=None):
    from base64 import b64encode
    from itsdangerous import TimestampSigner
    client = TestClient(app)
    if role:
        session = {'role': role, 'user_name': 'Payroll test admin'}
        session.update({'admin': True} if role == 'super_admin' else {'hr_id': 987654})
        token = TimestampSigner(os.environ['SESSION_SECRET']).sign(
            b64encode(json.dumps(session).encode())).decode()
        client.cookies.set('session', token)
    return client


def _paid_record():
    with get_db() as db:
        row = db.execute("SELECT p.id FROM payroll_records p JOIN employees e ON e.id=p.employee_id "
                         "WHERE e.staff_id=?", (STAFF[0],)).fetchone()
        pid = row['id']
        db.execute("UPDATE payroll_records SET payment_status='paid',payment_method='Bank',"
                   "payment_reference='OLD-123',paid_at=CURRENT_TIMESTAMP,"
                   "finalized_at=CURRENT_TIMESTAMP,locked_at=CURRENT_TIMESTAMP,locked_by='original' "
                   "WHERE id=?", (pid,))
        return dict(db.execute('SELECT * FROM payroll_records WHERE id=?', (pid,)).fetchone())


@pytest.mark.parametrize('role', ['admin', 'super_admin'])
def test_admin_can_return_paid_preserving_payment_and_salary(role):
    before = _paid_record()
    client = _session_client(role)
    response = client.post(f"/payroll/{before['id']}/return-to-finalized",
                           data={'month': 'wrong', 'reason': 'Correct mistaken payment'},
                           follow_redirects=False)
    assert response.status_code == 303
    assert f'month={MONTH}' in response.headers['location']
    with get_db() as db:
        after = dict(db.execute('SELECT * FROM payroll_records WHERE id=?', (before['id'],)).fetchone())
        logs = db.execute('SELECT * FROM payroll_change_logs WHERE payroll_id=? ORDER BY id',
                          (before['id'],)).fetchall()
    assert after['payment_status'] == 'finalized'
    assert after['paid_at'] is None and after['payment_reference'] is None
    assert after['payment_method'] is None
    for key in ['net_salary', 'calculation_snapshot', 'fixed_salary', 'locked_at', 'finalized_at']:
        assert after[key] == before[key]
    assert json.loads(logs[-2]['snapshot']) == before
    assert logs[-2]['actor'] == 'Payroll test admin'
    assert json.loads(logs[-1]['snapshot'])['payment_status'] == 'finalized'
    page = client.get(response.headers['location'])
    assert page.status_code == 200
    assert 'Previous payment details are preserved' in page.text
    assert 'OLD-123' in page.text
    # A repeated submission must not create another reversal.
    repeat = client.post(f"/payroll/{before['id']}/return-to-finalized",
                         data={'month': MONTH, 'reason': 'Correct mistaken payment'})
    assert repeat.status_code == 409
    # The original payment workflow still accepts a fresh method and reference.
    paid = client.post(f"/payroll/{before['id']}/status",
                       data={'month': MONTH, 'status': 'paid', 'payment_method': 'Cash',
                             'payment_reference': 'NEW-456'}, follow_redirects=False)
    assert paid.status_code == 303
    assert _status(STAFF[0]) == 'paid'


@pytest.mark.parametrize('role,expected', [(None, 401), ('hr_manager', 403),
                                          ('hr_executive', 403), ('viewer', 403)])
def test_non_admin_cannot_return_paid_even_with_payroll_permissions(role, expected):
    before = _paid_record()
    client = _session_client(role)
    response = client.post(f"/payroll/{before['id']}/return-to-finalized",
                           data={'month': MONTH, 'reason': 'Correct mistaken payment'})
    assert response.status_code == expected
    assert _status(STAFF[0]) == 'paid'
    if role == 'hr_manager':
        assert 'Return to Finalized' not in client.get(f'/payroll?month={MONTH}').text


@pytest.mark.parametrize('reason', ['', '   ', 'oops', 'x' * 1001])
def test_return_paid_requires_valid_reason(reason):
    before = _paid_record()
    response = _session_client('admin').post(f"/payroll/{before['id']}/return-to-finalized",
                                            data={'month': MONTH, 'reason': reason})
    assert response.status_code in (400, 422)
    assert _status(STAFF[0]) == 'paid'


def test_return_paid_rolls_back_if_history_cannot_be_written(monkeypatch):
    from app import main as main_module
    before = _paid_record()
    def fail_history(*args, **kwargs):
        raise RuntimeError('simulated log failure')
    monkeypatch.setattr(main_module, '_log_payroll_change', fail_history)
    response = _session_client('super_admin').post(
        f"/payroll/{before['id']}/return-to-finalized",
        data={'month': MONTH, 'reason': 'Correct mistaken payment'})
    assert response.status_code == 500
    assert _status(STAFF[0]) == 'paid'
    with get_db() as db:
        assert not db.execute('SELECT id FROM payroll_change_logs WHERE payroll_id=?',
                              (before['id'],)).fetchall()
