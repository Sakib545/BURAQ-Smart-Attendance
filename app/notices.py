"""Notice board: HR writes once, staff get it on WhatsApp and on their page.

A notice goes to every active, WhatsApp-registered employee, or to one
department. It also shows at the top of each employee's own page (/me) until
HR takes it down.

WhatsApp delivers free text only to people who messaged the business number
in the last 24 hours. For everyone else the approved template
WHATSAPP_NOTICE_TEMPLATE (default ``staff_notice``) is used with two values:
the employee's name and the notice title, so they know to open their page.
"""
import asyncio
import logging
import os
import time
from datetime import datetime
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

TEMPLATE = os.getenv("WHATSAPP_NOTICE_TEMPLATE", "staff_notice")
PORTAL_LIMIT = 5
SEND_PAUSE_SECONDS = 0.2


def apply_notice_migrations(engine, sqlite: bool) -> None:
    pk = "INTEGER PRIMARY KEY AUTOINCREMENT" if sqlite else "BIGSERIAL PRIMARY KEY"
    big = "INTEGER" if sqlite else "BIGINT"
    with engine.begin() as conn:
        conn.execute(text(f"""CREATE TABLE IF NOT EXISTS notices(
            id {pk}, title TEXT NOT NULL, body TEXT NOT NULL, department TEXT NOT NULL DEFAULT '',
            via_whatsapp INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active',
            recipients INTEGER NOT NULL DEFAULT 0, sent INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,
            created_by TEXT NOT NULL DEFAULT '', created_at {big} NOT NULL, removed_at {big})"""))


def recipients(c, department: str = "") -> list[dict]:
    sql = ("SELECT id,name,whatsapp_phone,phone FROM employees WHERE is_active AND registration_status='approved' "
           "AND COALESCE(whatsapp_phone,phone,'')<>''")
    params: tuple = ()
    if department:
        sql += " AND department=?"
        params = (department,)
    return [dict(r) for r in c.execute(sql + " ORDER BY id", params).fetchall()]


def whatsapp_text(title: str, body: str) -> str:
    return f"📢 BURAQ নোটিশ\n\n*{title}*\n\n{body}"


async def deliver(notice_id: int) -> dict:
    from app.whatsapp import send_template, send_text
    with get_db() as c:
        notice = c.execute("SELECT * FROM notices WHERE id=?", (notice_id,)).fetchone()
        if not notice:
            return {"sent": 0, "failed": 0}
        people = recipients(c, notice["department"])
    message = whatsapp_text(notice["title"], notice["body"])
    sent = failed = 0
    for person in people:
        phone = person["whatsapp_phone"] or person["phone"]
        result = await send_text(phone, message)
        if not result.get("sent") and TEMPLATE:
            result = await send_template(phone, TEMPLATE, [person["name"], notice["title"]])
        if result.get("sent"):
            sent += 1
        else:
            failed += 1
            logger.warning("Notice %s not delivered employee=%s result=%s", notice_id, person["id"], result)
        await asyncio.sleep(SEND_PAUSE_SECONDS)
    with get_db() as c:
        c.execute("UPDATE notices SET recipients=?,sent=?,failed=? WHERE id=?", (len(people), sent, failed, notice_id))
    return {"sent": sent, "failed": failed}


def _when(stamp) -> str:
    return datetime.fromtimestamp(int(stamp), ZoneInfo(settings.timezone)).strftime("%d %b %Y, %I:%M %p")


@router.get("/notices", response_class=HTMLResponse)
def notices_page(request: Request, notice: str = "", error: str = ""):
    from app.main import layout, require_permission
    require_permission(request, "notices_manage")
    with get_db() as c:
        rows = c.execute("SELECT * FROM notices ORDER BY id DESC LIMIT 50").fetchall()
        departments = [r["department"] for r in c.execute(
            "SELECT DISTINCT department FROM employees WHERE is_active AND COALESCE(department,'')<>'' ORDER BY department").fetchall()]
        everyone = len(recipients(c))
    top = ""
    if notice:
        top += f"<div class='notice'>{escape(notice[:200])}</div>"
    if error:
        top += f"<div class='notice' style='background:#fee2e2;color:#991b1b'>{escape(error[:200])}</div>"
    options = "<option value=''>Everyone</option>" + "".join(
        f"<option value='{escape(d)}'>{escape(d)}</option>" for d in departments)
    form = f"""<div class='card'><h3>New notice</h3><form method='post' action='/notices'>
    <label>Title</label><input name='title' maxlength='120' required placeholder='যেমন: শুক্রবার অফিস বন্ধ'>
    <label>Message</label><textarea name='body' maxlength='1500' required rows='5'></textarea>
    <label>Send to</label><select name='department'>{options}</select>
    <label><input type='checkbox' name='via_whatsapp' value='1' checked> Also send on WhatsApp</label>
    <button class='btn'>Publish</button></form>
    <p class='sub'>{everyone} registered staff can receive WhatsApp. Staff who have not messaged in 24 hours get the
    <b>{escape(TEMPLATE)}</b> template (name + title) instead of the full text. The notice also shows on each person's own page.</p></div>"""

    def row_html(r) -> str:
        audience = escape(r["department"]) if r["department"] else "Everyone"
        if not r["via_whatsapp"]:
            delivery = "Page only"
        elif r["recipients"] or r["sent"] or r["failed"]:
            delivery = f"{r['sent']} sent" + (f", {r['failed']} failed" if r["failed"] else "")
        else:
            delivery = "Sending…"
        state = ("<span class='status ok'>Showing</span><form method='post' action='/notices/"
                 f"{r['id']}/remove'><button class='btn secondary'>Take down</button></form>"
                 if r["status"] == "active" else "<span class='status bad'>Taken down</span>")
        return (f"<tr><td>{_when(r['created_at'])}<div class='sub'>{escape(r['created_by'])}</div></td>"
                f"<td><b>{escape(r['title'])}</b><div class='sub' style='white-space:pre-wrap'>{escape(r['body'])}</div></td>"
                f"<td>{audience}</td><td>{delivery}</td><td>{state}</td></tr>")

    table = "".join(row_html(r) for r in rows) or "<tr><td colspan='5'>No notices yet.</td></tr>"
    body = f"""{top}<div class='hero'><div><div class='eyebrow'>Staff communication</div><h2>Notice Board</h2>
    <div class='sub'>Write once. Staff get it on WhatsApp and see it on their own page.</div></div></div>
    {form}<div class='section-gap'></div><div class='card' style='overflow:auto'><table><thead><tr><th>When</th><th>Notice</th>
    <th>To</th><th>WhatsApp</th><th></th></tr></thead><tbody>{table}</tbody></table></div>"""
    return layout("Notice Board", body, request, "notices")


@router.post("/notices")
def create_notice(request: Request, background_tasks: BackgroundTasks, title: str = Form(...), body: str = Form(...),
                  department: str = Form(""), via_whatsapp: str = Form("")):
    from app.main import audit, require_permission
    require_permission(request, "notices_manage")
    title, body, department = title.strip()[:120], body.strip()[:1500], department.strip()
    if not title or not body:
        return RedirectResponse("/notices?error=" + quote_plus("Write a title and a message."), 303)
    send = via_whatsapp == "1"
    actor = str(request.session.get("user_name") or "HR")
    with get_db() as c:
        notice_id = int(c.execute(
            "INSERT INTO notices(title,body,department,via_whatsapp,created_by,created_at) VALUES(?,?,?,?,?,?) RETURNING id",
            (title, body, department, 1 if send else 0, actor, int(time.time()))).fetchone()["id"])
        audit(request, "notice_create", "notice", str(notice_id), f"{title} to {department or 'everyone'}", db=c)
    if send:
        background_tasks.add_task(deliver, notice_id)
    return RedirectResponse("/notices?notice=" + quote_plus("Published." + (" WhatsApp is sending now." if send else "")), 303)


@router.post("/notices/{notice_id}/remove")
def remove_notice(request: Request, notice_id: int):
    from app.main import audit, require_permission
    require_permission(request, "notices_manage")
    with get_db() as c:
        c.execute("UPDATE notices SET status='removed',removed_at=? WHERE id=?", (int(time.time()), notice_id))
        audit(request, "notice_remove", "notice", str(notice_id), "Notice taken down", db=c)
    return RedirectResponse("/notices?notice=" + quote_plus("Taken down from staff pages."), 303)


def portal_block(c, employee) -> str:
    rows = c.execute("SELECT title,body,created_at FROM notices WHERE status='active' AND (department='' OR department=?) "
                     "ORDER BY id DESC LIMIT ?", (employee["department"] or "", PORTAL_LIMIT)).fetchall()
    if not rows:
        return ""
    items = "".join(
        f"<div><div class='main'><b>📢 {escape(r['title'])}</b><div class='sub' style='white-space:pre-wrap'>{escape(r['body'])}</div>"
        f"<div class='small'>{_when(r['created_at'])}</div></div></div>" for r in rows)
    return f"<section id='notices'><h2>নোটিশ</h2><div class='sheet rows'>{items}</div></section>"
