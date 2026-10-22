"""A once-a-day WhatsApp summary for HR.

At the time HR picks (default 10:30) one message goes to each HR number:
who is in, who is late, who has not checked in yet, who is on leave, and how
many approvals are waiting. Nothing here changes attendance; it only reads.

"Not checked in yet" means: active, not on approved leave, and either has a
duty today on the roster or has no weekly roster at all. Someone whose weekly
roster has no duty today is counted as "off today", not as missing.

WhatsApp only delivers free text to a number that wrote to the business number
in the last 24 hours. If the text is refused, the summary falls back to an
approved template (WHATSAPP_HR_SUMMARY_TEMPLATE, default ``hr_daily_summary``)
with six numbers: date, present, late, not in yet, on leave, pending approvals.
"""
import asyncio
import logging
import os
import re
from datetime import date, datetime
from html import escape
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import settings
from app.database import get_db
from app.runtime import get_setting, set_setting
from app.time_format import format_time_12h

logger = logging.getLogger(__name__)
router = APIRouter()

ENABLED_KEY = "daily_summary_enabled"
PHONES_KEY = "daily_summary_phones"
TIME_KEY = "daily_summary_time"
LAST_SENT_KEY = "daily_summary_last_sent"
DEFAULT_TIME = "10:30"
TEMPLATE = os.getenv("WHATSAPP_HR_SUMMARY_TEMPLATE", "hr_daily_summary")
MAX_NAMES = 15
WORKER_INTERVAL_SECONDS = 60


def _now() -> datetime:
    return datetime.now(ZoneInfo(settings.timezone))


def clean_phones(raw: str) -> list[str]:
    """Comma or newline separated numbers -> digits only, no duplicates."""
    phones = []
    for part in re.split(r"[,;\n]+", raw or ""):
        digits = re.sub(r"\D", "", part)
        if 8 <= len(digits) <= 15 and digits not in phones:
            phones.append(digits)
    return phones


def clean_time(raw: str) -> str:
    match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", (raw or "").strip())
    return f"{int(match.group(1)):02d}:{match.group(2)}" if match else DEFAULT_TIME


def build_summary(day: date) -> dict:
    today = day.isoformat()
    with get_db() as c:
        rows = c.execute("""
            SELECT e.id,e.staff_id,e.name,a.check_in,a.late_minutes,
                   CASE WHEN l.employee_id IS NOT NULL THEN 1 ELSE 0 END on_leave,
                   CASE WHEN cd.employee_id IS NOT NULL OR ws.employee_id IS NOT NULL THEN 1 ELSE 0 END duty_today,
                   CASE WHEN anyws.employee_id IS NOT NULL THEN 1 ELSE 0 END has_roster
            FROM employees e
            LEFT JOIN attendance a ON a.employee_id=e.id AND a.work_date=?
            LEFT JOIN (SELECT DISTINCT employee_id FROM leave_requests
                       WHERE status='approved' AND start_date<=? AND end_date>=?) l ON l.employee_id=e.id
            LEFT JOIN (SELECT DISTINCT employee_id FROM custom_duties WHERE duty_date=? AND is_active) cd ON cd.employee_id=e.id
            LEFT JOIN (SELECT DISTINCT employee_id FROM duty_schedules WHERE weekday=? AND is_active) ws ON ws.employee_id=e.id
            LEFT JOIN (SELECT DISTINCT employee_id FROM duty_schedules WHERE is_active) anyws ON anyws.employee_id=e.id
            WHERE e.is_active
            ORDER BY e.staff_id""", (today, today, today, today, day.weekday())).fetchall()
        pending = {
            "registrations": int(c.execute("SELECT COUNT(*) c FROM pending_registrations WHERE status='pending'").fetchone()["c"] or 0),
            "selfies": int(c.execute("SELECT COUNT(*) c FROM attendance_fingerprints WHERE review_status='pending'").fetchone()["c"] or 0),
            "leave": int(c.execute("SELECT COUNT(*) c FROM leave_requests WHERE status='pending'").fetchone()["c"] or 0),
            "corrections": int(c.execute("SELECT COUNT(*) c FROM attendance_corrections WHERE status='pending'").fetchone()["c"] or 0),
            "overtime": int(c.execute("SELECT COUNT(*) c FROM overtime_requests WHERE status='pending'").fetchone()["c"] or 0),
        }
    present, late, missing, leave, off = [], [], [], [], []
    for r in rows:
        person = {"staff_id": r["staff_id"], "name": r["name"], "check_in": r["check_in"], "late": int(r["late_minutes"] or 0)}
        if r["on_leave"]:
            leave.append(person)
        elif r["check_in"]:
            present.append(person)
            if person["late"] > 0:
                late.append(person)
        elif r["duty_today"] or not r["has_roster"]:
            missing.append(person)
        else:
            off.append(person)
    late.sort(key=lambda p: -p["late"])
    return {"date": today, "total": len(rows), "present": present, "late": late, "missing": missing,
            "leave": leave, "off": off, "pending": pending, "pending_total": sum(pending.values())}


def _names(people: list[dict], extra=None) -> str:
    shown = [f"• {p['name']} ({p['staff_id']})" + (extra(p) if extra else "") for p in people[:MAX_NAMES]]
    if len(people) > MAX_NAMES:
        shown.append(f"• আরও {len(people) - MAX_NAMES} জন")
    return "\n".join(shown)


def format_summary(s: dict) -> str:
    day = datetime.fromisoformat(s["date"]).strftime("%d %b %Y, %A")
    lines = [f"📋 BURAQ দৈনিক উপস্থিতি সারাংশ", day, "",
             f"✅ উপস্থিত: {len(s['present'])} জন (মোট {s['total']})",
             f"⏰ দেরিতে: {len(s['late'])} জন",
             f"❌ এখনো আসেনি: {len(s['missing'])} জন",
             f"🏖 ছুটিতে: {len(s['leave'])} জন"]
    if s["off"]:
        lines.append(f"💤 আজ duty নেই: {len(s['off'])} জন")
    if s["late"]:
        lines += ["", "⏰ দেরিতে এসেছেন:",
                  _names(s["late"], lambda p: f" {p['late']} মি. · {format_time_12h(p['check_in'])}")]
    if s["missing"]:
        lines += ["", "❌ এখনো Check In করেননি:", _names(s["missing"])]
    if s["leave"]:
        lines += ["", "🏖 ছুটিতে:", _names(s["leave"])]
    p = s["pending"]
    lines += ["", f"📌 অপেক্ষমাণ অনুমোদন: {s['pending_total']}"]
    for label, key in (("Registration", "registrations"), ("Selfie review", "selfies"), ("ছুটির আবেদন", "leave"),
                       ("Attendance correction", "corrections"), ("Overtime", "overtime")):
        if p.get(key):
            lines.append(f"• {label}: {p[key]}")
    return "\n".join(lines)


def template_values(s: dict) -> list[str]:
    return [s["date"], str(len(s["present"])), str(len(s["late"])), str(len(s["missing"])),
            str(len(s["leave"])), str(s["pending_total"])]


async def send_summary(day: date | None = None) -> dict:
    """Send today's summary to every HR number. Returns counts for the page."""
    from app.whatsapp import send_template, send_text
    phones = clean_phones(get_setting(PHONES_KEY))
    if not phones:
        return {"sent": 0, "failed": 0, "reason": "no numbers"}
    summary = build_summary(day or _now().date())
    message = format_summary(summary)
    sent = failed = 0
    for phone in phones:
        result = await send_text(phone, message)
        if not result.get("sent") and TEMPLATE:
            result = await send_template(phone, TEMPLATE, template_values(summary))
        if result.get("sent"):
            sent += 1
        else:
            failed += 1
            logger.warning("Daily HR summary not delivered phone=%s result=%s", phone, result)
    return {"sent": sent, "failed": failed}


def _claim_today(today: str) -> bool:
    """Mark today as sent; True only for the first caller, so a restart or a
    second worker never sends the summary twice."""
    with get_db() as c:
        c.execute("INSERT INTO system_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO NOTHING", (LAST_SENT_KEY, ""))
        result = c.execute("UPDATE system_settings SET value=?,updated_at=CURRENT_TIMESTAMP WHERE key=? AND value<>?",
                           (today, LAST_SENT_KEY, today))
        return (result.result.rowcount or 0) > 0


def due(now: datetime) -> bool:
    if get_setting(ENABLED_KEY) != "1" or not clean_phones(get_setting(PHONES_KEY)):
        return False
    if now.strftime("%H:%M") < clean_time(get_setting(TIME_KEY, DEFAULT_TIME)):
        return False
    return get_setting(LAST_SENT_KEY) != now.date().isoformat()


async def run_summary_cycle(now: datetime | None = None) -> bool:
    now = now or _now()
    if not due(now) or not _claim_today(now.date().isoformat()):
        return False
    await send_summary(now.date())
    return True


async def summary_worker():
    while True:
        try:
            await run_summary_cycle()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Daily HR summary cycle failed")
        await asyncio.sleep(WORKER_INTERVAL_SECONDS)


# ------------------------------------------------------------------ page

@router.get("/daily-summary", response_class=HTMLResponse)
def summary_page(request: Request, notice: str = "", error: str = ""):
    from app.main import layout, require_permission
    require_permission(request, "daily_summary_manage")
    enabled = get_setting(ENABLED_KEY) == "1"
    phones = ", ".join(clean_phones(get_setting(PHONES_KEY)))
    send_time = clean_time(get_setting(TIME_KEY, DEFAULT_TIME))
    last = get_setting(LAST_SENT_KEY) or "never"
    preview = format_summary(build_summary(_now().date()))
    top = ""
    if notice:
        top += f"<div class='notice'>{escape(notice[:200])}</div>"
    if error:
        top += f"<div class='notice' style='background:#fee2e2;color:#991b1b'>{escape(error[:200])}</div>"
    body = f"""{top}<div class='hero'><div><div class='eyebrow'>WhatsApp • every day</div><h2>Daily HR Summary</h2>
    <div class='sub'>One message each morning: who is in, late, not in yet, on leave, and pending approvals.</div>
    <div class='sub'>Last sent: {escape(last)}</div></div>
    <div class='actions'><form method='post' action='/daily-summary/send'><button class='btn secondary'>Send now</button></form></div></div>
    <div class='two'><div class='card'><h3>Settings</h3><form method='post' action='/daily-summary'>
    <label><input type='checkbox' name='enabled' value='1'{' checked' if enabled else ''}> Send automatically every day</label>
    <label>HR WhatsApp numbers</label><input name='phones' value='{escape(phones)}' placeholder='8801XXXXXXXXX, 8801YYYYYYYYY'>
    <div class='sub'>Country code included, separated by commas.</div>
    <label>Send time</label><input type='time' name='send_time' value='{escape(send_time)}' required>
    <button class='btn'>Save</button></form>
    <p class='sub'>WhatsApp delivers free text only if that number messaged the business number in the last 24 hours.
    Otherwise the approved template <b>{escape(TEMPLATE)}</b> is used with 6 values: date, present, late, not in yet, on leave, pending.</p></div>
    <div class='card'><h3>Today's message</h3><pre style='white-space:pre-wrap;font-family:inherit'>{escape(preview)}</pre></div></div>"""
    return layout("Daily HR Summary", body, request, "daily-summary")


@router.post("/daily-summary")
def save_summary_settings(request: Request, phones: str = Form(""), send_time: str = Form(DEFAULT_TIME), enabled: str = Form("")):
    from app.main import audit, require_permission
    require_permission(request, "daily_summary_manage")
    cleaned = clean_phones(phones)
    if enabled == "1" and not cleaned:
        return RedirectResponse("/daily-summary?error=" + quote_plus("Add at least one HR number to turn it on."), 303)
    set_setting(PHONES_KEY, ",".join(cleaned))
    set_setting(TIME_KEY, clean_time(send_time))
    set_setting(ENABLED_KEY, "1" if enabled == "1" else "0")
    audit(request, "daily_summary_settings", "setting", ENABLED_KEY,
          f"{'On' if enabled == '1' else 'Off'} at {clean_time(send_time)} to {len(cleaned)} numbers")
    return RedirectResponse("/daily-summary?notice=Saved", 303)


@router.post("/daily-summary/send")
async def send_summary_now(request: Request):
    from app.main import audit, require_permission
    require_permission(request, "daily_summary_manage")
    result = await send_summary()
    if result.get("reason") == "no numbers":
        return RedirectResponse("/daily-summary?error=" + quote_plus("Add an HR number first."), 303)
    audit(request, "daily_summary_send", "setting", ENABLED_KEY, f"Sent {result['sent']}, failed {result['failed']}")
    message = f"Sent to {result['sent']} number(s)" + (f", {result['failed']} failed (see logs)" if result["failed"] else "")
    return RedirectResponse("/daily-summary?notice=" + quote_plus(message), 303)
