"""Staff loans and salary advances repaid in monthly installments.

HR records the amount, the monthly installment and the first salary month.
From then on the installment due is filled into that month's payroll as the
"Advance" deduction, until the loan is repaid.

What counts as repaid is worked out, never stored: an installment counts once
the payslip it sits on is finalized or paid. A draft that is discarded or
reopened stops counting by itself, so no payroll action needs a matching loan
action. Each payroll save rewrites that month's installments from the
payslip's advance amount, so lowering the advance on a payslip lowers the
installment too.
"""
import logging
import re
import time
from datetime import datetime
from html import escape
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from app.config import settings
from app.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()

KINDS = {"loan": "Loan", "advance": "Salary advance"}
SETTLED = ("finalized", "paid")
LOAN_REASON = "Loan / advance installment"


def apply_loan_migrations(engine, sqlite: bool) -> None:
    pk = "INTEGER PRIMARY KEY AUTOINCREMENT" if sqlite else "BIGSERIAL PRIMARY KEY"
    big = "INTEGER" if sqlite else "BIGINT"
    real = "REAL" if sqlite else "DOUBLE PRECISION"
    with engine.begin() as conn:
        conn.execute(text(f"""CREATE TABLE IF NOT EXISTS employee_loans(
            id {pk}, employee_id {big} NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
            kind TEXT NOT NULL DEFAULT 'loan', principal {real} NOT NULL, installment {real} NOT NULL,
            start_month TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active',
            created_by TEXT NOT NULL DEFAULT '', created_at {big} NOT NULL, cancelled_by TEXT, cancelled_at {big})"""))
        conn.execute(text(f"""CREATE TABLE IF NOT EXISTS loan_installments(
            id {pk}, loan_id {big} NOT NULL REFERENCES employee_loans(id) ON DELETE CASCADE,
            employee_id {big} NOT NULL, salary_month TEXT NOT NULL, amount {real} NOT NULL,
            created_at {big} NOT NULL, UNIQUE(loan_id, salary_month))"""))


def _cents(value) -> float:
    return round(float(value or 0) + 1e-9, 2)


def _repaid(c, loan_id: int, except_month: str = "") -> float:
    row = c.execute(
        "SELECT COALESCE(SUM(i.amount),0) s FROM loan_installments i JOIN payroll_records p "
        "ON p.employee_id=i.employee_id AND p.salary_month=i.salary_month "
        f"WHERE i.loan_id=? AND i.salary_month<>? AND p.payment_status IN ('{SETTLED[0]}','{SETTLED[1]}')",
        (loan_id, except_month)).fetchone()
    return _cents(row["s"])


def due_for_month(c, employee_id: int, month: str) -> list[tuple[int, float]]:
    """(loan id, amount) for each active loan with an installment due this month."""
    loans = c.execute("SELECT id,principal,installment FROM employee_loans WHERE employee_id=? AND status='active' "
                      "AND start_month<=? ORDER BY start_month,id", (employee_id, month)).fetchall()
    due = []
    for loan in loans:
        remaining = _cents(float(loan["principal"]) - _repaid(c, int(loan["id"]), month))
        amount = _cents(min(float(loan["installment"]), remaining))
        if amount > 0:
            due.append((int(loan["id"]), amount))
    return due


def total_due(employee_id: int, month: str) -> float:
    if not re.fullmatch(r"\d{4}-\d{2}", month or ""):
        return 0.0
    with get_db() as c:
        return _cents(sum(amount for _, amount in due_for_month(c, employee_id, month)))


def record_for_payroll(c, employee_id: int, month: str, advance_amount: float) -> float:
    """Split a payslip's advance deduction over the loans due that month.
    Anything above the installments due is a one-off advance, not a loan."""
    due = due_for_month(c, employee_id, month)
    c.execute("DELETE FROM loan_installments WHERE employee_id=? AND salary_month=?", (employee_id, month))
    left = _cents(advance_amount)
    used = 0.0
    now = int(time.time())
    for loan_id, amount in due:
        part = _cents(min(amount, left))
        if part <= 0:
            break
        c.execute("INSERT INTO loan_installments(loan_id,employee_id,salary_month,amount,created_at) VALUES(?,?,?,?,?)",
                  (loan_id, employee_id, month, part, now))
        left = _cents(left - part)
        used = _cents(used + part)
    return used


def loan_rows(c) -> list[dict]:
    rows = c.execute("SELECT l.*,e.staff_id,e.name FROM employee_loans l JOIN employees e ON e.id=l.employee_id "
                     "ORDER BY CASE WHEN l.status='active' THEN 0 ELSE 1 END,l.start_month DESC,l.id DESC").fetchall()
    output = []
    for r in rows:
        item = dict(r)
        item["repaid"] = _repaid(c, int(r["id"]))
        item["remaining"] = max(0.0, _cents(float(r["principal"]) - item["repaid"]))
        item["state"] = "cancelled" if r["status"] == "cancelled" else ("closed" if item["remaining"] <= 0 else "active")
        output.append(item)
    return output


def _money(value) -> str:
    return f"{float(value or 0):,.2f}"


@router.get("/loans", response_class=HTMLResponse)
def loans_page(request: Request, notice: str = "", error: str = ""):
    from app.main import has_permission, layout, require_permission
    require_permission(request, "payroll_view")
    manage = has_permission(request, "payroll_manage")
    with get_db() as c:
        rows = loan_rows(c)
        employees = c.execute("SELECT id,staff_id,name FROM employees WHERE is_active ORDER BY staff_id").fetchall()
    top = ""
    if notice:
        top += f"<div class='notice'>{escape(notice[:200])}</div>"
    if error:
        top += f"<div class='notice' style='background:#fee2e2;color:#991b1b'>{escape(error[:200])}</div>"
    this_month = datetime.now(ZoneInfo(settings.timezone)).strftime("%Y-%m")
    form = ""
    if manage:
        options = "".join(f"<option value='{e['id']}'>{escape(e['staff_id'])} - {escape(e['name'])}</option>" for e in employees)
        kinds = "".join(f"<option value='{k}'>{v}</option>" for k, v in KINDS.items())
        form = f"""<div class='card'><h3>New loan or advance</h3><form method='post' action='/loans'>
        <label>Employee</label><select name='employee_id' required>{options}</select>
        <div class='two'><div><label>Type</label><select name='kind'>{kinds}</select></div>
        <div><label>First salary month to deduct</label><input type='month' name='start_month' value='{this_month}' required></div></div>
        <div class='two'><div><label>Total amount (BDT)</label><input type='number' name='principal' min='1' step='0.01' required></div>
        <div><label>Monthly installment (BDT)</label><input type='number' name='installment' min='1' step='0.01' required></div></div>
        <label>Note</label><input name='note' maxlength='300'>
        <button class='btn'>Save</button></form>
        <p class='sub'>The installment is filled into payroll as the Advance deduction each month until it is repaid.
        A month counts as repaid once its payslip is finalized.</p></div>"""
    labels = {"active": ("Active", "warn"), "closed": ("Repaid", "ok"), "cancelled": ("Cancelled", "bad")}

    def actions(r) -> str:
        if not manage or r["state"] != "active":
            return ""
        return (f"<form method='post' action='/loans/{r['id']}/cancel' onsubmit=\"return confirm('Stop deducting this loan?')\">"
                "<button class='btn secondary'>Stop</button></form>")

    table = "".join(
        f"<tr><td><b>{escape(r['staff_id'])}</b><div class='sub'>{escape(r['name'])}</div></td>"
        f"<td>{KINDS.get(r['kind'], r['kind'])}<div class='sub'>{escape(r['note'] or '')}</div></td>"
        f"<td>{escape(r['start_month'])}</td><td class='num'>{_money(r['principal'])}</td><td class='num'>{_money(r['installment'])}</td>"
        f"<td class='num'>{_money(r['repaid'])}</td><td class='num'><b>{_money(r['remaining'])}</b></td>"
        f"<td><span class='status {labels[r['state']][1]}'>{labels[r['state']][0]}</span></td><td>{actions(r)}</td></tr>"
        for r in rows) or "<tr><td colspan='9'>No loans or advances yet.</td></tr>"
    outstanding = sum(r["remaining"] for r in rows if r["state"] == "active")
    body = f"""{top}<div class='hero'><div><div class='eyebrow'>Payroll • Private</div><h2>Loans & Advances</h2>
    <div class='sub'>Outstanding: BDT {_money(outstanding)} across {sum(1 for r in rows if r['state'] == 'active')} active</div></div>
    <div class='actions'><a class='btn secondary' href='/payroll'>Payroll</a></div></div>
    {form}<div class='section-gap'></div><div class='card' style='overflow:auto'><table><thead><tr><th>Employee</th><th>Type</th>
    <th>From</th><th>Total</th><th>Per month</th><th>Repaid</th><th>Remaining</th><th>Status</th><th></th></tr></thead>
    <tbody>{table}</tbody></table></div>"""
    return layout("Loans & Advances", body, request, "loans")


@router.post("/loans")
def create_loan(request: Request, employee_id: int = Form(...), kind: str = Form("loan"), start_month: str = Form(...),
                principal: float = Form(...), installment: float = Form(...), note: str = Form("")):
    from app.main import audit, require_permission
    require_permission(request, "payroll_manage")
    if kind not in KINDS or not re.fullmatch(r"\d{4}-\d{2}", start_month):
        return RedirectResponse("/loans?error=" + quote_plus("Check the type and month."), 303)
    principal, installment = _cents(principal), _cents(installment)
    if principal <= 0 or installment <= 0 or principal > 10_000_000:
        return RedirectResponse("/loans?error=" + quote_plus("Amounts must be more than zero."), 303)
    installment = min(installment, principal)
    actor = str(request.session.get("user_name") or "HR")
    with get_db() as c:
        if not c.execute("SELECT 1 FROM employees WHERE id=? AND is_active", (employee_id,)).fetchone():
            return RedirectResponse("/loans?error=" + quote_plus("Employee not found."), 303)
        c.execute("INSERT INTO employee_loans(employee_id,kind,principal,installment,start_month,note,created_by,created_at) "
                  "VALUES(?,?,?,?,?,?,?,?)", (employee_id, kind, principal, installment, start_month, note.strip()[:300],
                                              actor, int(time.time())))
        audit(request, "loan_create", "employee", str(employee_id),
              f"{KINDS[kind]} {principal:.2f}, {installment:.2f}/month from {start_month}", db=c)
    return RedirectResponse("/loans?notice=" + quote_plus("Saved. It will be deducted from payroll."), 303)


@router.post("/loans/{loan_id}/cancel")
def cancel_loan(request: Request, loan_id: int):
    from app.main import audit, require_permission
    require_permission(request, "payroll_manage")
    actor = str(request.session.get("user_name") or "HR")
    with get_db() as c:
        c.execute("UPDATE employee_loans SET status='cancelled',cancelled_by=?,cancelled_at=? WHERE id=? AND status='active'",
                  (actor, int(time.time()), loan_id))
        audit(request, "loan_cancel", "loan", str(loan_id), "Loan deductions stopped", db=c)
    return RedirectResponse("/loans?notice=" + quote_plus("Stopped. Already finalized installments stay recorded."), 303)
