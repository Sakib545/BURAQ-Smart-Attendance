"""Browsing-time tracker fed by the BURAQ Chrome extension.

Deliberately narrow, because this is monitoring of real people:

* only the website *name* (domain) and seconds are stored — never a full URL,
  page title, search text or page content;
* time is accepted only while the employee has an open check-in. Off duty the
  server refuses the data, whatever the extension sends;
* a PC is tied to one employee by that employee's personal install link (or a
  one-time code) issued by an Admin, so staff cannot pick someone else's
  Staff ID. The link is signed and an Admin can cancel it at any time.
"""
import hashlib
import io
import json
import logging
import os
import re
import secrets
import threading
import time
import zipfile
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import text

from app.config import settings
from app.database import get_db
from app.location_links import _secret

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

EXTENSION_DIR = Path(__file__).resolve().parent.parent / "extension"
EXTENSION_FILES = ("manifest.json", "config.js", "background.js", "popup.html", "popup.js",
                   "icons/icon16.png", "icons/icon48.png", "icons/icon128.png")


def store_url() -> str:
    """Chrome Web Store listing, once the extension is published there. With it
    set, install pages offer one-click "Add to Chrome" instead of a download."""
    value = os.getenv("BROWSING_EXTENSION_STORE_URL", "").strip()
    return value if value.startswith("https://chromewebstore.google.com/") or value.startswith("https://chrome.google.com/webstore/") else ""

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
        f"""CREATE TABLE IF NOT EXISTS browsing_invites(
            employee_id {big} PRIMARY KEY REFERENCES employees(id),
            version INTEGER NOT NULL DEFAULT 1)""",
        f"""CREATE TABLE IF NOT EXISTS browsing_presence(
            id {pk}, employee_id {big} NOT NULL REFERENCES employees(id),
            work_date TEXT NOT NULL, online_seconds {big} NOT NULL DEFAULT 0,
            outside_seconds {big} NOT NULL DEFAULT 0, idle_seconds {big} NOT NULL DEFAULT 0,
            UNIQUE(employee_id, work_date))""",
    ]
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))
    from sqlalchemy import inspect
    if "idle_seconds" not in {col["name"] for col in inspect(engine).get_columns("browsing_presence")}:
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE browsing_presence ADD COLUMN idle_seconds {big} NOT NULL DEFAULT 0"))


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
        "SELECT d.id,d.employee_id,d.last_report_at,d.last_seen_at,e.name,e.staff_id FROM browsing_devices d "
        "JOIN employees e ON e.id=d.employee_id WHERE d.token_hash=? AND d.revoked_at IS NULL AND e.is_active",
        (_hash(token),),
    ).fetchone()


def _invite_signer() -> URLSafeSerializer:
    return URLSafeSerializer(_secret(), salt="buraq-browsing-invite")


def invite_token(db, employee_id: int) -> str:
    """Personal install token. Stable until an Admin cancels the link."""
    row = db.execute("SELECT version FROM browsing_invites WHERE employee_id=?", (employee_id,)).fetchone()
    if not row:
        db.execute("INSERT INTO browsing_invites(employee_id,version) VALUES(?,1)", (employee_id,))
    return _invite_signer().dumps([int(employee_id), int(row["version"]) if row else 1])


def invite_employee(db, token: str):
    """Active employee a still-valid install token belongs to, else None."""
    try:
        employee_id, version = _invite_signer().loads(str(token or ""))
        employee_id, version = int(employee_id), int(version)
    except (BadSignature, TypeError, ValueError):
        return None
    return db.execute(
        "SELECT e.id,e.name,e.staff_id FROM employees e JOIN browsing_invites i ON i.employee_id=e.id "
        "WHERE e.id=? AND i.version=? AND e.is_active",
        (employee_id, version),
    ).fetchone()


OUTSIDE_KEY = "~outside"            # reserved "domain" the extension uses for time away from tracked Chrome
IDLE_KEY = "~idle"                  # ... and for time with no mouse or keyboard use
IDLE_SETTING = "browsing_idle_minutes"
IDLE_DEFAULT_MINUTES, IDLE_MIN_MINUTES, IDLE_MAX_MINUTES = 6, 2, 10


def idle_minutes() -> int:
    """The rule: this many minutes without a click or key press counts as idle."""
    from app.runtime import get_setting
    try:
        return min(IDLE_MAX_MINUTES, max(IDLE_MIN_MINUTES, int(get_setting(IDLE_SETTING, str(IDLE_DEFAULT_MINUTES)))))
    except ValueError:
        return IDLE_DEFAULT_MINUTES
HEARTBEAT_GAP_SECONDS = 150         # the extension checks in every minute; a longer gap is "no signal"


def record_presence(db, device, work_date: str, now: int, outside_seconds: int = 0, idle_seconds: int = 0) -> None:
    """Count how long the tracker was actually reachable during duty.

    Duty time the tracker cannot account for is the signal that someone worked
    in another Chrome profile or browser, or closed the tracked one."""
    last = device["last_seen_at"]
    gap = now - int(last) if last else HEARTBEAT_GAP_SECONDS + 1
    online = max(0, gap) if gap <= HEARTBEAT_GAP_SECONDS else 60
    db.execute(
        "INSERT INTO browsing_presence(employee_id,work_date,online_seconds,outside_seconds,idle_seconds) VALUES(?,?,?,?,?) "
        "ON CONFLICT(employee_id,work_date) DO UPDATE SET "
        "online_seconds=browsing_presence.online_seconds+excluded.online_seconds,"
        "outside_seconds=browsing_presence.outside_seconds+excluded.outside_seconds,"
        "idle_seconds=browsing_presence.idle_seconds+excluded.idle_seconds",
        (device["employee_id"], work_date, online, max(0, int(outside_seconds)), max(0, int(idle_seconds))),
    )


def duty_seconds(db, employee_id: int, work_date: str) -> int:
    """Length of that day's duty so far: check-in to check-out, or to now if still open."""
    row = db.execute("SELECT check_in,check_out FROM attendance WHERE employee_id=? AND work_date=?",
                     (employee_id, work_date)).fetchone()
    if not row or not row["check_in"]:
        return 0
    zone = ZoneInfo(settings.timezone)

    def parse(value):
        moment = datetime.fromisoformat(str(value))
        return moment if moment.tzinfo else moment.replace(tzinfo=zone)

    try:
        start = parse(row["check_in"])
        limit = start + timedelta(hours=OPEN_CHECKIN_MAX_HOURS)
        end = min(parse(row["check_out"]), limit) if row["check_out"] else min(_now_local(), limit)
    except ValueError:
        return 0
    return max(0, int((end - start).total_seconds()))


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
    invite = str(data.get("invite") or "")
    staff_id = str(data.get("staff_id") or "").strip()[:40]
    with get_db() as c:
        if staff_id and not invite and not code:
            # Self-service: the employee types their own Staff ID in the
            # extension. Nothing proves it is theirs, so these PCs are labelled
            # in the dashboard for an Admin to glance over.
            found = c.execute(
                "SELECT id,name,staff_id FROM employees WHERE UPPER(staff_id)=UPPER(?) AND is_active", (staff_id,)
            ).fetchone()
            if not found:
                return _json({"ok": False, "message": "এই Staff ID পাওয়া যায়নি। আবার দেখে লিখুন।"}, 400)
            row = {"employee_id": found["id"], "name": found["name"], "staff_id": found["staff_id"]}
            label = (label or "PC") + " (Staff ID)"
        elif invite:
            invited = invite_employee(c, invite)
            if not invited:
                return _json({"ok": False, "message": "This install link was cancelled. Ask Admin for a new link."}, 400)
            row = {"employee_id": invited["id"], "name": invited["name"], "staff_id": invited["staff_id"]}
        else:
            row = c.execute(
                "SELECT p.id,p.employee_id,e.name,e.staff_id FROM browsing_pair_codes p JOIN employees e ON e.id=p.employee_id "
                "WHERE p.code_hash=? AND p.used_at IS NULL AND p.expires_at>=? AND e.is_active",
                (_hash(code), now),
            ).fetchone() if code else None
            if not row:
                return _json({"ok": False, "message": "Code is wrong or expired."}, 400)
            c.execute("UPDATE browsing_pair_codes SET used_at=? WHERE id=?", (now, row["id"]))
        token = secrets.token_urlsafe(32)
        c.execute(
            "INSERT INTO browsing_devices(employee_id,token_hash,label,created_at,last_seen_at) VALUES(?,?,?,?,?)",
            (row["employee_id"], _hash(token), label, now, now),
        )
        tracking = bool(duty_work_date(c, int(row["employee_id"])))
    logger.info("Browsing device paired employee_id=%s", row["employee_id"])
    return _json({"ok": True, "token": token, "employee": row["name"], "staff_id": row["staff_id"], "tracking": tracking,
                  "idle_seconds": idle_minutes() * 60})


@router.get("/api/browsing/status")
def browsing_status(request: Request):
    with get_db() as c:
        device = _device(c, request)
        if not device:
            return _json({"ok": False, "message": "Device is not paired."}, 401)
        now = int(time.time())
        work_date = duty_work_date(c, int(device["employee_id"]))
        if work_date:
            record_presence(c, device, work_date, now)
        c.execute("UPDATE browsing_devices SET last_seen_at=? WHERE id=?", (now, device["id"]))
        tracking = bool(work_date)
    return _json({"ok": True, "tracking": tracking, "employee": device["name"], "staff_id": device["staff_id"],
                  "idle_seconds": idle_minutes() * 60})


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
        reserved = entry.get("domain") in (OUTSIDE_KEY, IDLE_KEY)
        domain = entry.get("domain") if reserved else clean_domain(entry.get("domain"))
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
            return _json({"ok": True, "tracking": False, "saved": 0, "idle_seconds": idle_minutes() * 60})
        claimed = sum(totals.values())
        scale = min(1.0, allowed / claimed) if claimed else 0
        record_presence(c, device, work_date, now, int(totals.pop(OUTSIDE_KEY, 0) * scale),
                        int(totals.pop(IDLE_KEY, 0) * scale))
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
    return _json({"ok": True, "tracking": True, "saved": saved, "idle_seconds": idle_minutes() * 60})


# ---------------------------------------------------------- public install page

def _public_base(request: Request) -> str:
    return settings.public_base_url or str(request.base_url).rstrip("/")


def _extension_zip(base: str, invite: str = "") -> Response:
    """The extension with this server's address (and a personal link) built in."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in EXTENSION_FILES:
            path = EXTENSION_DIR / name
            if not path.is_file():
                raise HTTPException(404, "Extension files are not installed on this server")
            if name == "config.js":
                content = "self.BURAQ_CONFIG = " + json.dumps({"server": base, "invite": invite}) + ";\n"
            elif name.endswith(".png"):
                content = path.read_bytes()
            else:
                content = path.read_text(encoding="utf-8").replace("https://smart-attendance.pro", base)
            archive.writestr(f"buraq-tracker/{name}", content)
    return Response(buffer.getvalue(), media_type="application/zip", headers={
        "Content-Disposition": "attachment; filename=buraq-tracker.zip", "Cache-Control": "no-store"})


PRIVACY_NOTE = ("<div class='sub' style='margin-top:14px'>এই extension শুধু duty চলাকালীন (Check In থেকে Check Out) "
                "website-এর নাম ও সময় record করে। পুরো link, page-এর লেখা, password বা duty-র বাইরের browsing record হয় না।</div>")
INSTALL_STEPS = """
      <li>উপরের button থেকে <b>buraq-tracker.zip</b> download করুন, তারপর file-টিতে right-click করে <b>Extract / Unzip</b> করুন।</li>
      <li>Chrome-এর address bar-এ লিখুন <b>chrome://extensions</b> এবং Enter চাপুন।</li>
      <li>উপরে ডান পাশে <b>Developer mode</b> চালু করুন।</li>
      <li><b>Load unpacked</b> চাপুন এবং unzip করা <b>buraq-tracker</b> folder-টি select করুন।</li>"""


@router.get("/tracker/extension.zip")
def tracker_extension_zip(request: Request):
    return _extension_zip(_public_base(request))


@router.get("/tracker/i/{token}/extension.zip")
def tracker_personal_zip(request: Request, token: str):
    with get_db() as c:
        if not invite_employee(c, token):
            raise HTTPException(404, "This install link was cancelled")
    return _extension_zip(_public_base(request), token)


@router.get("/tracker/i/{token}/status")
def tracker_personal_status(token: str):
    """Lets the install page show "connected" once the extension has paired."""
    with get_db() as c:
        employee = invite_employee(c, token)
        if not employee:
            return JSONResponse({"connected": False}, status_code=404, headers={"Cache-Control": "no-store"})
        recent = c.execute(
            "SELECT 1 FROM browsing_devices WHERE employee_id=? AND revoked_at IS NULL AND created_at>=? LIMIT 1",
            (employee["id"], int(time.time()) - 600),
        ).fetchone()
    return JSONResponse({"connected": bool(recent)}, headers={"Cache-Control": "no-store"})


@router.get("/tracker/privacy", response_class=HTMLResponse)
def tracker_privacy_page():
    from app.main import layout
    body = """<div class='login' style='max-width:640px'><div class='card'>
    <div class='title'>BURAQ Duty Browsing Tracker — Privacy Policy</div>
    <p>This Chrome extension is used by BURAQ on office computers together with BURAQ Smart Attendance.</p>
    <h3>What is collected</h3>
    <ul style='line-height:1.8;padding-left:20px'>
      <li>The name of the website in the active tab (for example <b>facebook.com</b>) and how many seconds it was active.</li>
      <li>This is recorded only while the employee is on duty — between Check In and Check Out.</li>
    </ul>
    <h3>What is never collected</h3>
    <ul style='line-height:1.8;padding-left:20px'>
      <li>Full page addresses, page titles, page content, searches, form entries, passwords or keystrokes.</li>
      <li>Any browsing while off duty, and any browsing in Incognito windows.</li>
    </ul>
    <h3>How it is used</h3>
    <p>The data is sent only to the BURAQ Smart Attendance server and is visible to authorised BURAQ HR/Admin staff.
    It is not sold, shared with third parties, or used for advertising.</p>
    <h3>Removal</h3>
    <p>An Admin can disconnect a computer at any time. Removing the extension from Chrome stops all collection.</p>
    <h3>Contact</h3>
    <p>Questions: contact the BURAQ HR/Admin office.</p>
    </div></div>"""
    return layout("Privacy Policy", body)


@router.get("/tracker/i/{token}", response_class=HTMLResponse)
def tracker_personal_page(request: Request, token: str):
    """One employee's own install link: the download connects itself, no code to type."""
    from app.main import layout
    with get_db() as c:
        employee = invite_employee(c, token)
    if not employee:
        body = ("<div class='login'><div class='card'><div class='title'>BURAQ Duty Tracker</div>"
                "<div class='notice' style='background:#fee2e2;color:#991b1b'>এই link-টি আর কাজ করছে না। "
                "Admin/HR-এর কাছ থেকে নতুন link নিন।</div></div></div>")
        return HTMLResponse(layout("BURAQ Duty Tracker", body).body, status_code=404)
    store = store_url()
    if store:
        install = f"""<p class='sub'>নিচের button চাপুন, তারপর <b>Add to Chrome</b> দিন। আর কিছু করতে হবে না — এই page খোলা রাখুন, নিজে connect হবে।</p>
    <a class='btn' target='_blank' rel='noopener' href='{escape(store)}'>➕ Add to Chrome</a>"""
    else:
        install = f"""<p class='sub'>অফিসের PC-তে Chrome extension install করুন। কোনো code লাগবে না — install হলেই নিজে connect হবে।</p>
    <a class='btn' href='/tracker/i/{escape(token)}/extension.zip'>⬇ Extension download করুন</a>
    <ol style='line-height:1.9;padding-left:20px;margin-top:18px'>{INSTALL_STEPS}
      <li>শেষ। এই page খোলা রাখুন — connect হলে নিচে দেখাবে।</li>
    </ol>"""
    body = f"""<div class='login' style='max-width:560px'><div class='card'>
    <div class='title'>BURAQ Duty Tracker</div>
    <div class='notice'>এই link শুধু <b>{escape(employee['name'])}</b> ({escape(employee['staff_id'])})-এর জন্য। অন্য কাউকে দেবেন না।</div>
    {install}
    <div id='tracker-status' class='notice' style='margin-top:16px'>⏳ Extension-এর অপেক্ষায়…</div>
    {PRIVACY_NOTE}<div class='sub'><a href='/tracker/privacy'>Privacy policy</a></div>
    </div></div>
    <script>
    (function () {{
      var box = document.getElementById('tracker-status');
      function check() {{
        fetch('/tracker/i/{escape(token)}/status', {{cache: 'no-store'}}).then(function (r) {{ return r.json(); }}).then(function (d) {{
          if (d.connected) {{
            box.textContent = 'Connected — এই PC connect হয়েছে। এখন page বন্ধ করতে পারেন।';
            box.style.background = '#dcfce7'; box.style.color = '#166534';
          }} else {{ setTimeout(check, 3000); }}
        }}).catch(function () {{ setTimeout(check, 5000); }});
      }}
      check();
    }})();
    </script>"""
    return layout("BURAQ Duty Tracker", body)


@router.get("/tracker", response_class=HTMLResponse)
def tracker_install_page(request: Request, code: str = ""):
    """Shareable page for all staff: add the extension, then type your Staff ID."""
    from app.main import layout
    code = re.sub(r"[^A-Z0-9-]", "", code.upper())[:9]
    code_box = (
        f"<div class='notice'>আপনার code: <b style='letter-spacing:.15em;font-size:18px'>{escape(code)}</b>"
        "<div class='sub'>১৫ মিনিট পর্যন্ত কাজ করবে, একবারই ব্যবহার করা যাবে।</div></div>"
    ) if code else ""
    connect_step = ("<li>Chrome-এর উপরে puzzle (🧩) icon থেকে <b>BURAQ Duty Browsing Tracker</b> খুলুন, "
                    "<b>নিজের Staff ID</b> লিখে <b>Connect</b> চাপুন।</li>")
    store = store_url()
    if store:
        install = f"""<p class='sub'>অফিসের PC-র Chrome-এ এই page খুলুন। সময় লাগবে ১ মিনিট।</p>
    <a class='btn' target='_blank' rel='noopener' href='{escape(store)}'>➕ Add to Chrome</a>
    <ol style='line-height:1.9;padding-left:20px;margin-top:18px'>
      <li>উপরের button চাপুন, তারপর <b>Add to Chrome</b> → <b>Add extension</b> দিন।</li>
      {connect_step}
    </ol>"""
    else:
        install = f"""<p class='sub'>অফিসের PC-তে Chrome extension install করার নিয়ম। সময় লাগবে ২ মিনিট।</p>
    <a class='btn' href='/tracker/extension.zip'>⬇ Extension download করুন</a>
    <ol style='line-height:1.9;padding-left:20px;margin-top:18px'>{INSTALL_STEPS}
      {connect_step}
    </ol>"""
    body = f"""<div class='login' style='max-width:560px'><div class='card'>
    <div class='title'>BURAQ Duty Tracker</div>
    {install}{code_box}{PRIVACY_NOTE}<div class='sub'><a href='/tracker/privacy'>Privacy policy</a></div>
    </div></div>"""
    return layout("BURAQ Duty Tracker", body)


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
    base = _public_base(request)
    share_link = base + "/tracker"
    with get_db() as c:
        employees = c.execute("SELECT id,staff_id,name FROM employees WHERE is_active ORDER BY staff_id").fetchall()
        usage = c.execute(
            "SELECT u.employee_id,e.name,e.staff_id,u.domain,u.seconds FROM browsing_usage u "
            "JOIN employees e ON e.id=u.employee_id WHERE u.work_date=? ORDER BY u.seconds DESC",
            (date,),
        ).fetchall()
        links = {e["id"]: invite_token(c, e["id"]) for e in employees} if manage else {}
        categories = {r["domain"]: r["category"] for r in c.execute("SELECT domain,category FROM browsing_site_categories").fetchall()}
        presence = {r["employee_id"]: r for r in c.execute(
            "SELECT employee_id,online_seconds,outside_seconds,idle_seconds FROM browsing_presence WHERE work_date=?", (date,)).fetchall()}
        # Everyone with a connected PC who was on duty belongs in the list, even
        # with nothing recorded — a silent tracker is exactly what to notice.
        tracked = c.execute(
            "SELECT DISTINCT e.id,e.name,e.staff_id FROM employees e JOIN browsing_devices d ON d.employee_id=e.id "
            "JOIN attendance a ON a.employee_id=e.id AND a.work_date=? WHERE d.revoked_at IS NULL AND a.check_in IS NOT NULL",
            (date,)).fetchall()
        duty = {r["id"]: duty_seconds(c, r["id"], date) for r in tracked}
        devices = c.execute(
            "SELECT d.id,d.label,d.last_seen_at,d.created_at,e.name,e.staff_id FROM browsing_devices d "
            "JOIN employees e ON e.id=d.employee_id WHERE d.revoked_at IS NULL ORDER BY e.staff_id,d.id"
        ).fetchall()
    people: dict[int, dict] = {}
    for row in usage:
        person = people.setdefault(row["employee_id"], {"name": row["name"], "staff_id": row["staff_id"], "total": 0, "top": [], "work": 0, "sorted": 0})
        person["total"] += int(row["seconds"])
        if row["domain"] in categories:
            person["sorted"] += int(row["seconds"])
            if categories[row["domain"]] == "work":
                person["work"] += int(row["seconds"])
        if len(person["top"]) < 3:
            person["top"].append(f"{escape(row['domain'])} <span class='sub'>{fmt_duration(row['seconds'])}</span>")
    for row in tracked:
        people.setdefault(row["id"], {"name": row["name"], "staff_id": row["staff_id"], "total": 0, "top": [], "work": 0, "sorted": 0})
    ranked = sorted(people.items(), key=lambda item: item[1]["total"], reverse=True)

    def coverage(eid) -> str:
        """Duty time the tracker could not see. A hint to look, not proof of anything."""
        seen = presence.get(eid)
        on_duty = duty.get(eid, 0)
        if not on_duty:
            return "<td><span class='sub'>—</span></td>" * 3
        outside = int(seen["outside_seconds"]) if seen else 0
        idle = int(seen["idle_seconds"]) if seen else 0
        silent = max(0, on_duty - (int(seen["online_seconds"]) if seen else 0))
        def cell(seconds):
            share = seconds * 100 / on_duty
            tone = "#b91c1c" if share >= 50 else "#b45309" if share >= 25 else ""
            style = f" style='color:{tone}'" if tone else ""
            return f"<td><b{style}>{fmt_duration(seconds)}</b> <span class='sub'>{round(share)}%</span></td>"
        return cell(idle) + cell(outside) + cell(silent)

    def work_share(p) -> str:
        # Only websites that have a category count, so unsorted time is not
        # silently treated as "not work".
        if not p["sorted"]:
            return "<span class='sub'>not sorted yet</span>"
        return f"<b>{round(p['work'] * 100 / p['sorted'])}%</b> <span class='sub'>work</span>"

    rows = "".join(
        f"<tr><td><a href='/browsing/{eid}?date={date}'><b>{escape(p['name'])}</b></a><div class='sub'>{escape(p['staff_id'])}</div></td>"
        f"<td><b>{fmt_duration(p['total'])}</b></td><td>{work_share(p)}</td>{coverage(eid)}<td>{' &nbsp;•&nbsp; '.join(p['top'])}</td></tr>"
        for eid, p in ranked
    ) or "<tr><td colspan='7'>No browsing recorded for this date.</td></tr>"
    per_employee: dict[str, int] = {}
    for d in devices:
        per_employee[d["staff_id"]] = per_employee.get(d["staff_id"], 0) + 1
    device_rows = "".join(
        f"<tr><td><b>{escape(d['name'])}</b><div class='sub'>{escape(d['staff_id'])}</div></td><td>{escape(d['label'] or 'PC')}"
        + ("<div class='sub' style='color:#b45309'>This employee has more than one PC — check</div>" if per_employee[d["staff_id"]] > 1 else "")
        + "</td>"
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
            "and is not shown again.</div>"
            f"<label>Or send this link to the employee (install steps + this code)</label>"
            f"<input readonly onclick='this.select()' value='{escape(new_code['link'])}'></div><div class='section-gap'></div>"
        )
    pair_form = ""
    if manage:
        options = "".join(f"<option value='{e['id']}'>{escape(e['staff_id'])} — {escape(e['name'])}</option>" for e in employees)
        link_rows = "".join(
            f"<tr><td><b>{escape(e['name'])}</b><div class='sub'>{escape(e['staff_id'])}</div></td>"
            f"<td><input readonly onclick='this.select()' value='{escape(base + '/tracker/i/' + links[e['id']])}'></td>"
            f"<td><form method='post' action='/browsing/invites/{e['id']}/reset'><button class='btn secondary' "
            "title='Cancel the old link and make a new one'>New link</button></form></td></tr>"
            for e in employees
        ) or "<tr><td colspan='3'>No active employees.</td></tr>"
        pair_form = (
            "<div class='card' style='overflow:auto'><h3>Install links</h3><div class='sub'>Send each employee their own link. "
            "They install the extension from it and it connects by itself — no code needed. If a link reaches the wrong "
            "person, press New link to cancel it.</div>"
            f"<table><thead><tr><th>Employee</th><th>Personal link</th><th></th></tr></thead><tbody>{link_rows}</tbody></table>"
            "<details style='margin-top:14px'><summary>Use a one-time code instead</summary>"
            f"<div class='sub'>General install page: {escape(share_link)}</div>"
            "<form method='post' action='/browsing/pair-code'>"
            f"<input type='hidden' name='date' value='{date}'><label>Employee</label><select name='employee_id' required>{options}</select>"
            "<button class='btn'>Create code</button></form></details></div>"
        )
    idle_form = ""
    if manage:
        options_idle = "".join(f"<option value='{m}'{' selected' if m == idle_minutes() else ''}>{m} minutes</option>"
                               for m in range(IDLE_MIN_MINUTES, IDLE_MAX_MINUTES + 1))
        idle_form = ("<div class='section-gap'></div><div class='card'><h3>Idle rule</h3><div class='sub'>If nobody clicks or types for this long, "
                     "the time counts as Idle instead of work. PCs pick up a change within a minute.</div>"
                     f"<form method='post' action='/browsing/idle-rule' class='actions'><select name='minutes'>{options_idle}</select>"
                     "<button class='btn secondary'>Save</button></form></div>")
    body = f"""{notice}<div class='hero'><div><div class='eyebrow'>Duty hours only</div><h2>Browsing Time</h2>
    <div class='sub'>Website names and time while an employee is checked in. Full links, page titles and off-duty browsing are never recorded.</div></div>
    <form method='get' class='actions'><input type='date' name='date' value='{date}'><button class='btn secondary'>Open</button></form></div>
    <div class='card' style='overflow:auto'><table><thead><tr><th>Employee</th><th>On websites</th><th>Work share</th><th>Idle</th><th>Outside tracker</th><th>No signal</th><th>Top websites</th></tr></thead><tbody>{rows}</tbody></table>
    <div class='sub' style='margin-top:10px'><b>Idle</b>: no click or key press for {idle_minutes()} minutes or more — the whole untouched stretch counts, even with a tab open.
    Time on a website counts only when the mouse or keyboard was used.<br><b>Outside tracker</b>: the PC was in use but not in the tracked Chrome — another Chrome profile, another browser or another program.
    <b>No signal</b>: on duty but the extension was silent — tracked Chrome closed, extension removed, or PC off. Percent is of duty time.
    Neither says what the person was doing; work in Excel or another program counts too.</div></div>
    {idle_form}<div class='section-gap'></div><div class='two'>{pair_form}<div class='card' style='overflow:auto'><h3>Connected PCs</h3>
    <table><thead><tr><th>Employee</th><th>PC</th><th>Last seen</th><th></th></tr></thead><tbody>{device_rows}</tbody></table></div></div>"""
    return layout("Browsing Time", body, request, "browsing")


@router.get("/browsing", response_class=HTMLResponse)
def browsing_page(request: Request, date: str = "", error: str = ""):
    from app.main import require_permission
    require_permission(request, "browsing_view")
    return _page(request, _valid_date(date), error=error)


@router.get("/browsing/{employee_id}", response_class=HTMLResponse)
def browsing_employee_page(request: Request, employee_id: int, date: str = ""):
    from app.main import require_permission, has_permission, layout
    from app.ai_insights import CATEGORIES
    require_permission(request, "browsing_view")
    manage = has_permission(request, "browsing_manage")
    date = _valid_date(date)
    with get_db() as c:
        categories = {r["domain"]: (r["category"], r["source"]) for r in c.execute(
            "SELECT domain,category,source FROM browsing_site_categories").fetchall()}
        employee = c.execute("SELECT name,staff_id FROM employees WHERE id=?", (employee_id,)).fetchone()
        if not employee:
            raise HTTPException(404, "Employee not found")
        usage = c.execute(
            "SELECT domain,seconds FROM browsing_usage WHERE employee_id=? AND work_date=? ORDER BY seconds DESC",
            (employee_id, date),
        ).fetchall()
    total = sum(int(r["seconds"]) for r in usage)
    top = int(usage[0]["seconds"]) if usage else 1
    back = f"/browsing/{employee_id}?date={date}"

    def category_cell(domain: str) -> str:
        current, source = categories.get(domain, ("", ""))
        if not manage:
            return escape(CATEGORIES.get(current, "Not sorted"))
        options = "".join(
            f"<option value='{key}'{' selected' if key == current else ''}>{escape(label)}</option>"
            for key, label in CATEGORIES.items())
        hint = "" if current else "<option value='' selected disabled>Not sorted</option>"
        note = " <span class='sub'>set by you</span>" if source == "manual" else ""
        return (f"<form method='post' action='/browsing/categories' style='display:flex;gap:6px;align-items:center'>"
                f"<input type='hidden' name='domain' value='{escape(domain)}'><input type='hidden' name='back' value='{back}'>"
                f"<select name='category' onchange='this.form.submit()'>{hint}{options}</select>{note}</form>")

    rows = "".join(
        f"<tr><td><b>{escape(r['domain'])}</b></td><td>{category_cell(r['domain'])}</td><td>{fmt_duration(r['seconds'])}</td>"
        f"<td style='width:35%'><div style='background:var(--line,#e5e7eb);border-radius:6px;height:8px'>"
        f"<div style='width:{max(2, round(int(r['seconds']) * 100 / top))}%;height:8px;border-radius:6px;background:var(--accent,#2563eb)'></div></div></td></tr>"
        for r in usage
    ) or "<tr><td colspan='4'>No browsing recorded for this date.</td></tr>"
    body = f"""<div class='hero'><div><div class='eyebrow'>Browsing Time • {escape(date)}</div><h2>{escape(employee['name'])}</h2>
    <div class='sub'>{escape(employee['staff_id'])} • Total {fmt_duration(total)} across {len(usage)} websites</div></div>
    <form method='get' class='actions'><input type='date' name='date' value='{date}'><button class='btn secondary'>Open</button>
    <a class='btn secondary' href='/browsing?date={date}'>Back</a></form></div>
    <div class='card' style='overflow:auto'><table><thead><tr><th>Website</th><th>Type</th><th>Time</th><th></th></tr></thead><tbody>{rows}</tbody></table></div>"""
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
    return _page(request, _valid_date(date), new_code={
        "name": employee["name"], "code": f"{code[:4]}-{code[4:]}",
        "link": f"{_public_base(request)}/tracker?code={code[:4]}-{code[4:]}"})


@router.post("/browsing/devices/{device_id}/revoke")
def browsing_revoke_device(request: Request, device_id: int):
    from app.main import require_permission, audit
    require_permission(request, "browsing_manage")
    with get_db() as c:
        c.execute("UPDATE browsing_devices SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (int(time.time()), device_id))
        audit(request, "browsing_device_revoke", "browsing_device", str(device_id), "Browsing tracker PC disconnected", db=c)
    return RedirectResponse("/browsing", 303)


@router.post("/browsing/invites/{employee_id}/reset")
def browsing_reset_invite(request: Request, employee_id: int):
    """Cancel an employee's install link. PCs already connected keep working."""
    from app.main import require_permission, audit
    require_permission(request, "browsing_manage")
    with get_db() as c:
        invite_token(c, employee_id)
        c.execute("UPDATE browsing_invites SET version=version+1 WHERE employee_id=?", (employee_id,))
        audit(request, "browsing_invite_reset", "employee", str(employee_id), "Browsing tracker install link replaced", db=c)
    return RedirectResponse("/browsing", 303)


@router.post("/browsing/idle-rule")
def browsing_set_idle_rule(request: Request, minutes: int = Form(...)):
    from app.main import require_permission, audit
    from app.runtime import set_setting
    require_permission(request, "browsing_manage")
    minutes = min(IDLE_MAX_MINUTES, max(IDLE_MIN_MINUTES, int(minutes)))
    set_setting(IDLE_SETTING, str(minutes))
    audit(request, "browsing_idle_rule", "setting", IDLE_SETTING, f"Idle after {minutes} minutes without input")
    return RedirectResponse("/browsing", 303)
