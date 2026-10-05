"""Browsing-time tracker fed by the BURAQ Chrome extension.

Deliberately narrow, because this is monitoring of real people:

* only the website *name* (domain) and seconds are stored — never a full URL,
  page title, search text or page content;
* time is accepted only while the employee has an open check-in. Off duty the
  server refuses the data, whatever the extension sends;
* a PC is tied to one employee by a one-time code an Admin creates, so staff
  cannot report under someone else's Staff ID.
"""
import hashlib
import logging
import re
import secrets
import threading
import time
from datetime import datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import text

from app.config import settings
from app.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()

PAIR_CODE_TTL_SECONDS = 15 * 60
PAIR_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
MAX_REPORT_SECONDS = 15 * 60          # one report can never add more than this
REPORT_SLACK_SECONDS = 90
MAX_ENTRIES_PER_REPORT = 60
OPEN_CHECKIN_MAX_HOURS = 16           # a forgotten check-out stops counting
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$")

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Max-Age": "86400",
    "Cache-Control": "no-store",
}

_pair_attempts: dict[str, list[float]] = {}
_pair_lock = threading.Lock()


def apply_browsing_migrations(engine, sqlite: bool) -> None:
    pk = "INTEGER PRIMARY KEY AUTOINCREMENT" if sqlite else "BIGSERIAL PRIMARY KEY"
    big = "INTEGER" if sqlite else "BIGINT"
    statements = [
        f"""CREATE TABLE IF NOT EXISTS browsing_pair_codes(
            id {pk}, employee_id {big} NOT NULL REFERENCES employees(id),
            code_hash TEXT NOT NULL UNIQUE, expires_at {big} NOT NULL,
            used_at {big}, created_by TEXT NOT NULL DEFAULT '')""",
        f"""CREATE TABLE IF NOT EXISTS browsing_devices(
            id {pk}, employee_id {big} NOT NULL REFERENCES employees(id),
            token_hash TEXT NOT NULL UNIQUE, label TEXT NOT NULL DEFAULT '',
            created_at {big} NOT NULL, last_seen_at {big}, last_report_at {big},
            revoked_at {big})""",
        f"""CREATE TABLE IF NOT EXISTS browsing_usage(
            id {pk}, employee_id {big} NOT NULL REFERENCES employees(id),
            work_date TEXT NOT NULL, domain TEXT NOT NULL,
            seconds {big} NOT NULL DEFAULT 0,
            UNIQUE(employee_id, work_date, domain))""",
        "CREATE INDEX IF NOT EXISTS ix_browsing_usage_date ON browsing_usage(work_date)",
    ]
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now_local() -> datetime:
    return datetime.now(ZoneInfo(settings.timezone))


def _json(payload: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status, headers=CORS)


def clean_domain(value) -> str:
    domain = str(value or "").strip().lower().rstrip(".")
    if domain.startswith("www."):
        domain = domain[4:]
    return domain if DOMAIN_RE.match(domain) and "." in domain else ""


def duty_work_date(db, employee_id: int) -> str:
    """Work date of the employee's open check-in, or '' when off duty."""
    now = _now_local()
    earliest = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    row = db.execute(
        "SELECT work_date,check_in FROM attendance WHERE employee_id=? AND check_in IS NOT NULL "
        "AND check_out IS NULL AND work_date>=? ORDER BY work_date DESC LIMIT 1",
        (employee_id, earliest),
    ).fetchone()
    if not row:
        return ""
    try:
        started = datetime.fromisoformat(str(row["check_in"]))
        if started.tzinfo is None:
            started = started.replace(tzinfo=ZoneInfo(settings.timezone))
        if now - started > timedelta(hours=OPEN_CHECKIN_MAX_HOURS):
            return ""
    except ValueError:
        if str(row["work_date"]) != now.strftime("%Y-%m-%d"):
            return ""
    return str(row["work_date"])


def _device(db, request: Request):
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if len(token) < 20:
        return None
    return db.execute(
        "SELECT d.id,d.employee_id,d.last_report_at,e.name,e.staff_id FROM browsing_devices d "
        "JOIN employees e ON e.id=d.employee_id WHERE d.token_hash=? AND d.revoked_at IS NULL AND e.is_active",
        (_hash(token),),
    ).fetchone()


def _pair_throttled(ip: str) -> bool:
    cutoff = time.time() - 600
    with _pair_lock:
        recent = [t for t in _pair_attempts.get(ip, []) if t > cutoff]
        recent.append(time.time())
        _pair_attempts[ip] = recent
        return len(recent) > 10


# ---------------------------------------------------------------- extension API

@router.options("/api/browsing/{path:path}")
def browsing_preflight(path: str):
    return Response(status_code=204, headers=CORS)


@router.post("/api/browsing/pair")
async def browsing_pair(request: Request):
    ip = request.client.host if request.client else "unknown"
    if _pair_throttled(ip):
        return _json({"ok": False, "message": "Too many attempts. Try again later."}, 429)
    try:
        data = await request.json()
    except Exception:
        return _json({"ok": False, "message": "Invalid request"}, 400)
    if not isinstance(data, dict):
        return _json({"ok": False, "message": "Invalid request"}, 400)
    code = re.sub(r"[^A-Z0-9]", "", str(data.get("code") or "").upper())
    label = str(data.get("label") or "").strip()[:60]
    now = int(time.time())
    with get_db() as c:
        row = c.execute(
            "SELECT p.id,p.employee_id,e.name,e.staff_id FROM browsing_pair_codes p JOIN employees e ON e.id=p.employee_id "
            "WHERE p.code_hash=? AND p.used_at IS NULL AND p.expires_at>=? AND e.is_active",
            (_hash(code), now),
        ).fetchone() if code else None
        if not row:
            return _json({"ok": False, "message": "Code is wrong or expired."}, 400)
        token = secrets.token_urlsafe(32)
        c.execute("UPDATE browsing_pair_codes SET used_at=? WHERE id=?", (now, row["id"]))
        c.execute(
            "INSERT INTO browsing_devices(employee_id,token_hash,label,created_at,last_seen_at) VALUES(?,?,?,?,?)",
            (row["employee_id"], _hash(token), label, now, now),
        )
        tracking = bool(duty_work_date(c, int(row["employee_id"])))
    logger.info("Browsing device paired employee_id=%s", row["employee_id"])
    return _json({"ok": True, "token": token, "employee": row["name"], "staff_id": row["staff_id"], "tracking": tracking})


@router.get("/api/browsing/status")
def browsing_status(request: Request):
    with get_db() as c:
        device = _device(c, request)
        if not device:
            return _json({"ok": False, "message": "Device is not paired."}, 401)
        c.execute("UPDATE browsing_devices SET last_seen_at=? WHERE id=?", (int(time.time()), device["id"]))
        tracking = bool(duty_work_date(c, int(device["employee_id"])))
    return _json({"ok": True, "tracking": tracking, "employee": device["name"], "staff_id": device["staff_id"]})


@router.post("/api/browsing/report")
async def browsing_report(request: Request):
    try:
        data = await request.json()
    except Exception:
        return _json({"ok": False, "message": "Invalid request"}, 400)
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return _json({"ok": False, "message": "Invalid request"}, 400)
    totals: dict[str, int] = {}
    for entry in entries[:MAX_ENTRIES_PER_REPORT]:
        if not isinstance(entry, dict):
            continue
        domain = clean_domain(entry.get("domain"))
        try:
            seconds = int(float(entry.get("seconds") or 0))
        except (TypeError, ValueError):
            continue
        if domain and seconds > 0:
            totals[domain] = totals.get(domain, 0) + seconds
    now = int(time.time())
    with get_db() as c:
        device = _device(c, request)
        if not device:
            return _json({"ok": False, "message": "Device is not paired."}, 401)
        work_date = duty_work_date(c, int(device["employee_id"]))
        # A report can never claim more time than actually passed since the
        # previous one, so a tampered extension cannot inflate or backfill.
        last = device["last_report_at"]
        allowed = MAX_REPORT_SECONDS if not last else min(MAX_REPORT_SECONDS, max(0, now - int(last)) + REPORT_SLACK_SECONDS)
        c.execute("UPDATE browsing_devices SET last_seen_at=?,last_report_at=? WHERE id=?", (now, now, device["id"]))
        if not work_date:
            return _json({"ok": True, "tracking": False, "saved": 0})
        claimed = sum(totals.values())
        scale = min(1.0, allowed / claimed) if claimed else 0
        saved = 0
        for domain, seconds in totals.items():
            seconds = int(seconds * scale)
            if seconds <= 0:
                continue
            c.execute(
                "INSERT INTO browsing_usage(employee_id,work_date,domain,seconds) VALUES(?,?,?,?) "
                "ON CONFLICT(employee_id,work_date,domain) DO UPDATE SET seconds=browsing_usage.seconds+excluded.seconds",
                (device["employee_id"], work_date, domain, seconds),
            )
            saved += seconds
    return _json({"ok": True, "tracking": True, "saved": saved})


# ------------------------------------------------------------------- dashboard

def fmt_duration(seconds) -> str:
    minutes = int(seconds or 0) // 60
    if minutes < 1:
        return "<1m"
    return f"{minutes // 60}h {minutes % 60:02d}m" if minutes >= 60 else f"{minutes}m"


def _valid_date(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return _now_local().strftime("%Y-%m-%d")


def _seen(epoch) -> str:
    if not epoch:
        return "never"
    return datetime.fromtimestamp(int(epoch), ZoneInfo(settings.timezone)).strftime("%d %b, %I:%M %p")


def _page(request: Request, date: str, new_code: dict | None = None, error: str = ""):
    from app.main import has_permission, layout
    manage = has_permission(request, "browsing_manage")
    with get_db() as c:
        employees = c.execute("SELECT id,staff_id,name FROM employees WHERE is_active ORDER BY staff_id").fetchall()
        usage = c.execute(
            "SELECT u.employee_id,e.name,e.staff_id,u.domain,u.seconds FROM browsing_usage u "
            "JOIN employees e ON e.id=u.employee_id WHERE u.work_date=? ORDER BY u.seconds DESC",
            (date,),
        ).fetchall()
        devices = c.execute(
            "SELECT d.id,d.label,d.last_seen_at,d.created_at,e.name,e.staff_id FROM browsing_devices d "
            "JOIN employees e ON e.id=d.employee_id WHERE d.revoked_at IS NULL ORDER BY e.staff_id,d.id"
        ).fetchall()
    people: dict[int, dict] = {}
    for row in usage:
        person = people.setdefault(row["employee_id"], {"name": row["name"], "staff_id": row["staff_id"], "total": 0, "top": []})
        person["total"] += int(row["seconds"])
        if len(person["top"]) < 3:
            person["top"].append(f"{escape(row['domain'])} <span class='sub'>{fmt_duration(row['seconds'])}</span>")
    ranked = sorted(people.items(), key=lambda item: item[1]["total"], reverse=True)
    rows = "".join(
        f"<tr><td><a href='/browsing/{eid}?date={date}'><b>{escape(p['name'])}</b></a><div class='sub'>{escape(p['staff_id'])}</div></td>"
        f"<td><b>{fmt_duration(p['total'])}</b></td><td>{' &nbsp;•&nbsp; '.join(p['top'])}</td></tr>"
        for eid, p in ranked
    ) or "<tr><td colspan='3'>No browsing recorded for this date.</td></tr>"
    device_rows = "".join(
        f"<tr><td><b>{escape(d['name'])}</b><div class='sub'>{escape(d['staff_id'])}</div></td><td>{escape(d['label'] or 'PC')}</td>"
        f"<td>{_seen(d['last_seen_at'])}</td><td>"
        + (f"<form method='post' action='/browsing/devices/{d['id']}/revoke'><button class='btn danger'>Disconnect</button></form>" if manage else "")
        + "</td></tr>"
        for d in devices
    ) or "<tr><td colspan='4'>No PC connected yet.</td></tr>"
    notice = f"<div class='notice' style='background:#fee2e2;color:#991b1b'>{escape(error)}</div>" if error else ""
    if new_code:
        notice += (
            f"<div class='card'><div class='eyebrow'>One-time code for {escape(new_code['name'])}</div>"
            f"<div class='metric' style='letter-spacing:.18em'>{escape(new_code['code'])}</div>"
            "<div class='sub'>Type this into the BURAQ extension on that employee's PC. Valid for 15 minutes, works once, "
            "and is not shown again.</div></div><div class='section-gap'></div>"
        )
    pair_form = ""
    if manage:
        options = "".join(f"<option value='{e['id']}'>{escape(e['staff_id'])} — {escape(e['name'])}</option>" for e in employees)
        pair_form = (
            "<div class='card'><h3>Connect a PC</h3><div class='sub'>Install the extension on the staff PC, then create a code "
            "for that employee.</div><form method='post' action='/browsing/pair-code'>"
            f"<input type='hidden' name='date' value='{date}'><label>Employee</label><select name='employee_id' required>{options}</select>"
            "<button class='btn'>Create code</button></form></div>"
        )
    body = f"""{notice}<div class='hero'><div><div class='eyebrow'>Duty hours only</div><h2>Browsing Time</h2>
    <div class='sub'>Website names and time while an employee is checked in. Full links, page titles and off-duty browsing are never recorded.</div></div>
    <form method='get' class='actions'><input type='date' name='date' value='{date}'><button class='btn secondary'>Open</button></form></div>
    <div class='card' style='overflow:auto'><table><thead><tr><th>Employee</th><th>Total</th><th>Top websites</th></tr></thead><tbody>{rows}</tbody></table></div>
    <div class='section-gap'></div><div class='two'>{pair_form}<div class='card' style='overflow:auto'><h3>Connected PCs</h3>
    <table><thead><tr><th>Employee</th><th>PC</th><th>Last seen</th><th></th></tr></thead><tbody>{device_rows}</tbody></table></div></div>"""
    return layout("Browsing Time", body, request, "browsing")


@router.get("/browsing", response_class=HTMLResponse)
def browsing_page(request: Request, date: str = "", error: str = ""):
    from app.main import require_permission
    require_permission(request, "browsing_view")
    return _page(request, _valid_date(date), error=error)


@router.get("/browsing/{employee_id}", response_class=HTMLResponse)
def browsing_employee_page(request: Request, employee_id: int, date: str = ""):
    from app.main import require_permission, layout
    require_permission(request, "browsing_view")
    date = _valid_date(date)
    with get_db() as c:
        employee = c.execute("SELECT name,staff_id FROM employees WHERE id=?", (employee_id,)).fetchone()
        if not employee:
            raise HTTPException(404, "Employee not found")
        usage = c.execute(
            "SELECT domain,seconds FROM browsing_usage WHERE employee_id=? AND work_date=? ORDER BY seconds DESC",
            (employee_id, date),
        ).fetchall()
    total = sum(int(r["seconds"]) for r in usage)
    top = int(usage[0]["seconds"]) if usage else 1
    rows = "".join(
        f"<tr><td><b>{escape(r['domain'])}</b></td><td>{fmt_duration(r['seconds'])}</td>"
        f"<td style='width:45%'><div style='background:var(--line,#e5e7eb);border-radius:6px;height:8px'>"
        f"<div style='width:{max(2, round(int(r['seconds']) * 100 / top))}%;height:8px;border-radius:6px;background:var(--accent,#2563eb)'></div></div></td></tr>"
        for r in usage
    ) or "<tr><td colspan='3'>No browsing recorded for this date.</td></tr>"
    body = f"""<div class='hero'><div><div class='eyebrow'>Browsing Time • {escape(date)}</div><h2>{escape(employee['name'])}</h2>
    <div class='sub'>{escape(employee['staff_id'])} • Total {fmt_duration(total)} across {len(usage)} websites</div></div>
    <form method='get' class='actions'><input type='date' name='date' value='{date}'><button class='btn secondary'>Open</button>
    <a class='btn secondary' href='/browsing?date={date}'>Back</a></form></div>
    <div class='card' style='overflow:auto'><table><thead><tr><th>Website</th><th>Time</th><th></th></tr></thead><tbody>{rows}</tbody></table></div>"""
    return layout("Browsing Time", body, request, "browsing")


@router.post("/browsing/pair-code", response_class=HTMLResponse)
def browsing_create_code(request: Request, employee_id: int = Form(...), date: str = Form("")):
    from app.main import require_permission, audit
    require_permission(request, "browsing_manage")
    code = "".join(secrets.choice(PAIR_CODE_ALPHABET) for _ in range(8))
    now = int(time.time())
    with get_db() as c:
        employee = c.execute("SELECT name FROM employees WHERE id=? AND is_active", (employee_id,)).fetchone()
        if not employee:
            return RedirectResponse("/browsing?error=Employee+not+found", 303)
        c.execute("DELETE FROM browsing_pair_codes WHERE employee_id=? AND used_at IS NULL", (employee_id,))
        c.execute(
            "INSERT INTO browsing_pair_codes(employee_id,code_hash,expires_at,created_by) VALUES(?,?,?,?)",
            (employee_id, _hash(code), now + PAIR_CODE_TTL_SECONDS, str(request.session.get("user_name", ""))),
        )
        audit(request, "browsing_pair_code", "employee", str(employee_id), "Browsing tracker pairing code created", db=c)
    return _page(request, _valid_date(date), new_code={"name": employee["name"], "code": f"{code[:4]}-{code[4:]}"})


@router.post("/browsing/devices/{device_id}/revoke")
def browsing_revoke_device(request: Request, device_id: int):
    from app.main import require_permission, audit
    require_permission(request, "browsing_manage")
    with get_db() as c:
        c.execute("UPDATE browsing_devices SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (int(time.time()), device_id))
        audit(request, "browsing_device_revoke", "browsing_device", str(device_id), "Browsing tracker PC disconnected", db=c)
    return RedirectResponse("/browsing", 303)
