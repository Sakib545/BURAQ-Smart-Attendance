"""Overtime that HR approves before it is paid.

Attendance never creates overtime by itself (since v9.24 overtime is manual).
This adds the step in between: an overtime request for one employee and one
date, with the number of minutes allowed. HR can approve it in advance, or an
employee can ask from their own page and HR approves or rejects it.

What is paid for an approved day is the smaller of the approved minutes and
the time actually worked after the duty ended (from the real check-out). No
check-out yet means nothing payable yet. The month's payable hours are filled
into payroll as the overtime hours; HR can still change them on the payslip.
"""
import logging
import re
import time
from calendar import monthrange
from datetime import date, datetime, timedelta
from html import escape
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from app.config import settings
from app.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_MINUTES = 12 * 60
PORTAL_DAYS_BACK = 7
PORTAL_DAYS_AHEAD = 30
STATUS_LABELS = {"pending": "Pending", "approved": "Approved", "rejected": "Rejected"}
STATUS_BN = {"pending": "অপেক্ষায়", "approved": "মঞ্জুর", "rejected": "নামঞ্জুর"}


def apply_overtime_migrations(engine, sqlite: bool) -> None:
    pk = "INTEGER PRIMARY KEY AUTOINCREMENT" if sqlite else "BIGSERIAL PRIMARY KEY"
    big = "INTEGER" if sqlite else "BIGINT"
    with engine.begin() as conn:
        conn.execute(text(f"""CREATE TABLE IF NOT EXISTS overtime_requests(
            id {pk}, employee_id {big} NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
            work_date TEXT NOT NULL, minutes INTEGER NOT NULL, reason TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending', requested_by TEXT NOT NULL DEFAULT '',
            decided_by TEXT, decided_at {big}, created_at {big} NOT NULL,
            UNIQUE(employee_id, work_date))"""))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_overtime_requests_date ON overtime_requests(work_date,status)"))


def _today() -> date:
    return datetime.now(ZoneInfo(settings.timezone)).date()


def month_bounds(month: str) -> tuple[str, str]:
    year, mon = int(month[:4]), int(month[5:7])
    return f"{month}-01", f"{month}-{monthrange(year, mon)[1]:02d}"


def worked_after_duty(c, employee, work_date: str) -> int | None:
    """Minutes between the duty end and the real check-out, or None when the
    employee has not checked out for that duty yet."""
    from app.services import _employee_shift_for_attendance, duty_window
    record = c.execute("SELECT check_out,attendance_shift FROM attendance WHERE employee_id=? AND work_date=?",
                       (employee["id"], work_date)).fetchone()
    if not record or not record["check_out"]:
        return None
    try:
        out = datetime.fromisoformat(str(record["check_out"]))
    except ValueError:
        return None
    _, end = duty_window(employee, date.fromisoformat(work_date), db=c,
                         shift_override=_employee_shift_for_attendance(record["attendance_shift"]))
    if out.tzinfo is None:
        out = out.replace(tzinfo=end.tzinfo)
    return max(0, int((out - end).total_seconds() // 60))


def month_rows(c, month: str, employee_id: int | None = None) -> list[dict]:
    first, last = month_bounds(month)
    sql = ("SELECT o.*,e.staff_id,e.name,e.shift FROM overtime_requests o JOIN employees e ON e.id=o.employee_id "
           "WHERE o.work_date>=? AND o.work_date<=?")
    params: tuple = (first, last)
    if employee_id:
        sql += " AND o.employee_id=?"
        params += (employee_id,)
    rows = []
    for r in c.execute(sql + " ORDER BY o.work_date,e.staff_id", params).fetchall():
        item = dict(r)
        actual = worked_after_duty(c, {"id": r["employee_id"], "shift": r["shift"]}, r["work_date"])
        item["actual"] = actual
        item["payable"] = min(int(r["minutes"]), actual) if r["status"] == "approved" and actual is not None else 0
        rows.append(item)
    return rows


def approved_hours(employee_id: int, month: str) -> float:
    """Payable overtime hours for one employee's salary month."""
    if not re.fullmatch(r"\d{4}-\d{2}", month or ""):
        return 0.0
    with get_db() as c:
        minutes = sum(r["payable"] for r in month_rows(c, month, employee_id))
    return round(minutes / 60, 2)


def _minutes_text(value) -> str:
    if value is None:
        return "—"
    hours, minutes = divmod(int(value), 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


def _clean_minutes(value) -> int:
    try:
        return min(MAX_MINUTES, max(0, int(value)))
    except (TypeError, ValueError):
        return 0


async def _notify(phone: str, message: str):
    from app.whatsapp import send_text
    try:
        await send_text(phone, message)
    except Exception:
        logger.exception("Overtime decision message failed")


# ------------------------------------------------------------------ HR page

@router.get("/overtime", response_class=HTMLResponse)
def overtime_page(request: Request, month: str = "", notice: str = "", error: str = ""):
    from app.main import layout, require_permission
    require_permission(request, "overtime_manage")
    month = month if re.fullmatch(r"\d{4}-\d{2}", month or "") else _today().strftime("%Y-%m")
    with get_db() as c:
        rows = month_rows(c, month)
        pending = c.execute(
            "SELECT o.*,e.staff_id,e.name FROM overtime_requests o JOIN employees e ON e.id=o.employee_id "
            "WHERE o.status='pending' ORDER BY o.work_date,o.id").fetchall()
        employees = c.execute("SELECT id,staff_id,name FROM employees WHERE is_active ORDER BY staff_id").fetchall()
    top = ""
    if notice:
        top += f"<div class='notice'>{escape(notice[:200])}</div>"
    if error:
        top += f"<div class='notice' style='background:#fee2e2;color:#991b1b'>{escape(error[:200])}</div>"
    options = "".join(f"<option value='{e['id']}'>{escape(e['staff_id'])} - {escape(e['name'])}</option>" for e in employees)
    form = f"""<div class='card'><h3>Approve overtime in advance</h3><form method='post' action='/overtime'>
    <input type='hidden' name='month' value='{month}'>
    <label>Employee</label><select name='employee_id' required>{options}</select>
    <div class='two'><div><label>Date</label><input type='date' name='work_date' required></div>
    <div><label>Allowed minutes</label><input type='number' name='minutes' min='1' max='{MAX_MINUTES}' required value='60'></div></div>
    <label>Reason</label><input name='reason' maxlength='300' placeholder='যেমন: month-end stock count'>
    <button class='btn'>Approve</button></form>
    <p class='sub'>Paid = the smaller of the allowed minutes and the time actually worked after duty end.</p></div>"""

    def decide_form(r) -> str:
        return (f"<form method='post' action='/overtime/{r['id']}/decide' style='display:flex;gap:6px;align-items:center'>"
                f"<input type='hidden' name='month' value='{month}'>"
                f"<input type='number' name='minutes' min='1' max='{MAX_MINUTES}' value='{int(r['minutes'])}' style='width:90px'>"
                "<button class='btn' name='action' value='approve'>Approve</button>"
                "<button class='btn secondary' name='action' value='reject'>Reject</button></form>")

    def cancel_form(r) -> str:
        if r["status"] == "rejected":
            return ""
        return (f"<form method='post' action='/overtime/{r['id']}/decide'><input type='hidden' name='month' value='{month}'>"
                "<button class='btn secondary' name='action' value='reject'>Cancel</button></form>")

    pending_rows = "".join(
        f"<tr><td>{escape(r['work_date'])}</td><td><b>{escape(r['staff_id'])}</b><div class='sub'>{escape(r['name'])}</div></td>"
        f"<td>{_minutes_text(r['minutes'])}</td><td>{escape(r['reason'] or '')}</td><td>{decide_form(r)}</td></tr>"
        for r in pending) or "<tr><td colspan='5'>No overtime request waiting.</td></tr>"
    tone = {"approved": "ok", "pending": "warn", "rejected": "bad"}
    month_html = "".join(
        f"<tr><td>{escape(r['work_date'])}</td><td><b>{escape(r['staff_id'])}</b><div class='sub'>{escape(r['name'])}</div></td>"
        f"<td><span class='status {tone.get(r['status'], 'ok')}'>{STATUS_LABELS.get(r['status'], r['status'])}</span></td>"
        f"<td>{_minutes_text(r['minutes'])}</td><td>{_minutes_text(r['actual']) if r['actual'] is not None else 'No check-out yet'}</td>"
        f"<td><b>{_minutes_text(r['payable'])}</b></td>"
        f"<td>{cancel_form(r)}</td></tr>"
        for r in rows) or "<tr><td colspan='7'>No overtime this month.</td></tr>"
    totals: dict = {}
    for r in rows:
        key = (r["staff_id"], r["name"])
        totals[key] = totals.get(key, 0) + r["payable"]
    total_rows = "".join(
        f"<tr><td><b>{escape(staff)}</b><div class='sub'>{escape(name)}</div></td><td><b>{minutes / 60:.2f} h</b></td></tr>"
        for (staff, name), minutes in sorted(totals.items()) if minutes) or "<tr><td colspan='2'>Nothing payable yet.</td></tr>"
    body = f"""{top}<div class='hero'><div><div class='eyebrow'>Payroll • {escape(month)}</div><h2>Overtime Approval</h2>
    <div class='sub'>Only approved overtime is filled into payroll.</div></div>
    <form method='get' class='actions'><input type='month' name='month' value='{month}'><button class='btn secondary'>Open</button></form></div>
    <div class='two'>{form}<div class='card' style='overflow:auto'><h3>Payable this month</h3>
    <table><thead><tr><th>Employee</th><th>Hours</th></tr></thead><tbody>{total_rows}</tbody></table></div></div>
    <div class='section-gap'></div><div class='card' style='overflow:auto'><h3>Waiting for a decision</h3>
    <table><thead><tr><th>Date</th><th>Employee</th><th>Asked</th><th>Reason</th><th></th></tr></thead><tbody>{pending_rows}</tbody></table></div>
    <div class='section-gap'></div><div class='card' style='overflow:auto'><h3>All overtime in {escape(month)}</h3>
    <table><thead><tr><th>Date</th><th>Employee</th><th>Status</th><th>Allowed</th><th>Worked after duty</th><th>Payable</th><th></th></tr></thead>
    <tbody>{month_html}</tbody></table></div>"""
    return layout("Overtime", body, request, "overtime")


@router.post("/overtime")
def overtime_preapprove(request: Request, employee_id: int = Form(...), work_date: str = Form(...),
                        minutes: int = Form(...), reason: str = Form(""), month: str = Form("")):
    from app.main import audit, require_permission
    require_permission(request, "overtime_manage")
    back = f"/overtime?month={quote_plus(month or work_date[:7])}"
    minutes = _clean_minutes(minutes)
    try:
        day = date.fromisoformat(work_date)
    except ValueError:
        return RedirectResponse(back + "&error=" + quote_plus("Pick a valid date."), 303)
    if not minutes:
        return RedirectResponse(back + "&error=" + quote_plus("Enter the allowed minutes."), 303)
    actor = str(request.session.get("user_name") or "HR")
    now = int(time.time())
    with get_db() as c:
        if not c.execute("SELECT 1 FROM employees WHERE id=? AND is_active", (employee_id,)).fetchone():
            return RedirectResponse(back + "&error=" + quote_plus("Employee not found."), 303)
        c.execute(
            "INSERT INTO overtime_requests(employee_id,work_date,minutes,reason,status,requested_by,decided_by,decided_at,created_at) "
            "VALUES(?,?,?,?,'approved',?,?,?,?) ON CONFLICT(employee_id,work_date) DO UPDATE SET minutes=excluded.minutes,"
            "reason=excluded.reason,status='approved',decided_by=excluded.decided_by,decided_at=excluded.decided_at",
            (employee_id, day.isoformat(), minutes, reason.strip()[:300], f"hr:{actor}", actor, now, now))
        audit(request, "overtime_approve", "employee", str(employee_id), f"{day.isoformat()} {minutes} minutes", db=c)
    return RedirectResponse(back + "&notice=" + quote_plus("Overtime approved."), 303)


@router.post("/overtime/{request_id}/decide")
def overtime_decide(request: Request, background_tasks: BackgroundTasks, request_id: int,
                    action: str = Form(...), minutes: int = Form(0), month: str = Form("")):
    from app.main import audit, require_permission
    require_permission(request, "overtime_manage")
    back = f"/overtime?month={quote_plus(month)}" if re.fullmatch(r"\d{4}-\d{2}", month or "") else "/overtime"
    if action not in ("approve", "reject"):
        return RedirectResponse(back, 303)
    actor = str(request.session.get("user_name") or "HR")
    with get_db() as c:
        row = c.execute("SELECT o.*,e.whatsapp_phone,e.phone,e.name FROM overtime_requests o JOIN employees e ON e.id=o.employee_id "
                        "WHERE o.id=?", (request_id,)).fetchone()
        if not row:
            return RedirectResponse(back, 303)
        status = "approved" if action == "approve" else "rejected"
        allowed = _clean_minutes(minutes) or int(row["minutes"])
        c.execute("UPDATE overtime_requests SET status=?,minutes=?,decided_by=?,decided_at=? WHERE id=?",
                  (status, allowed, actor, int(time.time()), request_id))
        audit(request, f"overtime_{action}", "overtime", str(request_id), f"{row['work_date']} {allowed} minutes", db=c)
    phone = row["whatsapp_phone"] or row["phone"]
    if phone and row["status"] == "pending" and str(row["requested_by"]).startswith("employee"):
        word = "মঞ্জুর" if status == "approved" else "নামঞ্জুর"
        background_tasks.add_task(_notify, phone, f"⏱ Overtime আবেদন {word}\n\n{row['name']}, {row['work_date']} তারিখের "
                                  f"overtime ({_minutes_text(allowed)}) HR {word} করেছেন।")
    return RedirectResponse(back + "&notice=" + quote_plus(f"Overtime {status}."), 303)


# ------------------------------------------------------------- employee page

def portal_block(c, employee, today: date) -> str:
    rows = c.execute("SELECT work_date,minutes,status FROM overtime_requests WHERE employee_id=? ORDER BY work_date DESC LIMIT 6",
                     (int(employee["id"]),)).fetchall()
    items = "".join(
        f"<div><div class='main'><b>{escape(r['work_date'])}</b><div class='sub'>{_minutes_text(r['minutes'])}</div></div>"
        f"<span class='pill {escape(r['status'])}'>{STATUS_BN.get(r['status'], r['status'])}</span></div>"
        for r in rows) or "<div class='empty'>এখনো কোনো overtime আবেদন নেই।</div>"
    return ("<section id='overtime'><h2>Overtime</h2><p class='lede'>HR মঞ্জুর করলেই overtime বেতনে যোগ হয়।</p>"
            f"<div class='sheet rows'>{items}</div>"
            "<details><summary>Overtime-এর আবেদন</summary><form class='sheet form' method='post' action='/me/overtime'>"
            f"<div class='two'><div><label for='od'>তারিখ</label><input id='od' type='date' name='work_date' required "
            f"min='{today - timedelta(days=PORTAL_DAYS_BACK)}' max='{today + timedelta(days=PORTAL_DAYS_AHEAD)}'></div>"
            f"<div><label for='om'>কত মিনিট</label><input id='om' type='number' name='minutes' min='15' max='{MAX_MINUTES}' required></div></div>"
            "<label for='or'>কারণ</label><textarea id='or' name='reason' required maxlength='300'></textarea>"
            "<button class='btn'>HR-কে পাঠান</button></form></details></section>")


@router.post("/me/overtime")
def employee_overtime_request(request: Request, work_date: str = Form(...), minutes: int = Form(...), reason: str = Form(...)):
    from app.employee_portal import _flash, current_employee
    employee = current_employee(request)
    if not employee:
        return RedirectResponse("/me/login", 303)
    back = RedirectResponse("/me#overtime", 303)
    today = _today()
    try:
        day = date.fromisoformat(work_date)
    except ValueError:
        _flash(request, "bad", "তারিখ সঠিক নয়।")
        return back
    minutes = _clean_minutes(minutes)
    if not (today - timedelta(days=PORTAL_DAYS_BACK) <= day <= today + timedelta(days=PORTAL_DAYS_AHEAD)):
        _flash(request, "bad", "গত ৭ দিন থেকে সামনের ৩০ দিনের মধ্যে তারিখ দিন।")
        return back
    if minutes < 15 or not reason.strip():
        _flash(request, "bad", "কমপক্ষে ১৫ মিনিট আর কারণ লিখুন।")
        return back
    with get_db() as c:
        if c.execute("SELECT 1 FROM overtime_requests WHERE employee_id=? AND work_date=?",
                     (int(employee["id"]), day.isoformat())).fetchone():
            _flash(request, "bad", "এই তারিখের overtime আবেদন আগেই আছে।")
            return back
        c.execute("INSERT INTO overtime_requests(employee_id,work_date,minutes,reason,status,requested_by,created_at) "
                  "VALUES(?,?,?,?,'pending','employee:portal',?)",
                  (int(employee["id"]), day.isoformat(), minutes, reason.strip()[:300], int(time.time())))
    _flash(request, "good", f"{day.isoformat()} তারিখের overtime আবেদন HR-এর কাছে পাঠানো হয়েছে।")
    return back
