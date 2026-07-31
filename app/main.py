import csv
import hashlib
import hmac
import io
import json
import logging
import os
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo
from html import escape

from fastapi import FastAPI, BackgroundTasks, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.database import database_kind, database_ok, database_warning, get_db, init_db
from app.runtime import configured, get_setting, set_setting, import_environment_defaults, get_stored_setting, restore_stored_setting
from app.employee_seed import import_employees
from app.whatsapp import handle, send_approval_flow, send_text

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logger = logging.getLogger(__name__)
app = FastAPI(title=settings.app_name, version="8.2.0", docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET", secrets.token_urlsafe(32)), https_only=settings.environment == "production", same_site="lax")

CSS = """
<style>
:root{--bg:#f4f7f6;--panel:#ffffff;--panel2:#f8faf9;--ink:#15211e;--muted:#697873;--brand:#087f5b;--brand2:#066747;--line:#dfe8e4;--ok:#15803d;--warn:#b45309;--bad:#b91c1c;--shadow:0 12px 34px rgba(22,59,49,.09)}
[data-theme="dark"]{--bg:#0f1715;--panel:#17201d;--panel2:#1c2824;--ink:#eef7f3;--muted:#a4b5af;--brand:#20a97a;--brand2:#37bd8f;--line:#2b3b36;--shadow:none}
*{box-sizing:border-box}html{color-scheme:light}html[data-theme="dark"]{color-scheme:dark}body{margin:0;background:var(--bg);font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif;color:var(--ink)}
a{color:inherit}.shell{min-height:100vh;display:grid;grid-template-columns:250px 1fr}.sidebar{background:#0d3b2e;color:#fff;padding:24px 18px;position:sticky;top:0;height:100vh}.logo{font-size:22px;font-weight:900;line-height:1.2;margin-bottom:6px}.logo:before{content:'◉';color:#59d4a9;margin-right:8px}.side-sub{font-size:12px;color:#b8d4ca;margin-bottom:28px}.side-nav{display:grid;gap:7px}.side-nav a{padding:11px 13px;border-radius:11px;text-decoration:none;color:#d8ebe4;font-weight:650}.side-nav a:hover,.side-nav a.active{background:rgba(255,255,255,.13);color:#fff}.main{min-width:0}.topbar{height:70px;background:var(--panel);border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:0 28px;position:sticky;top:0;z-index:5}.page{padding:26px;max-width:1400px;margin:auto}.title{font-size:27px;font-weight:850;letter-spacing:-.5px}.sub{color:var(--muted);font-size:14px}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:15px}.two{display:grid;grid-template-columns:1fr 1fr;gap:16px}.card{background:var(--panel);border:1px solid var(--line);border-radius:17px;padding:20px;box-shadow:var(--shadow)}.metric{font-size:31px;font-weight:850;margin-top:7px}.status{display:inline-flex;align-items:center;gap:6px;padding:7px 11px;border-radius:999px;font-size:13px;font-weight:750}.status:before{content:'●';font-size:10px}.ok{background:#dcfce7;color:var(--ok)}.warn{background:#fef3c7;color:var(--warn)}.bad{background:#fee2e2;color:var(--bad)}.actions{display:flex;gap:9px;flex-wrap:wrap}.btn{border:0;border-radius:11px;padding:10px 14px;background:var(--brand);color:#fff;font-weight:750;cursor:pointer;text-decoration:none;display:inline-block}.btn:hover{background:var(--brand2)}.btn.secondary{background:var(--panel2);border:1px solid var(--line);color:var(--ink)}.btn.danger{background:#fee2e2;color:var(--bad)}input,select,textarea{width:100%;padding:11px 12px;border:1px solid var(--line);border-radius:10px;margin:6px 0 14px;background:var(--panel);color:var(--ink)}label{font-size:14px;font-weight:700}table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:11px;border-bottom:1px solid var(--line)}th{color:var(--muted)}h2{margin:0 0 14px}h3{margin:0 0 10px}.notice{padding:13px 15px;border-radius:12px;background:#ecfeff;color:#155e75;margin-bottom:16px}.code{font-family:ui-monospace,monospace;background:#111827;color:#f9fafb;padding:12px;border-radius:10px;overflow:auto}.login{max-width:450px;margin:7vh auto;padding:18px}.login .card{padding:30px}.masked{font-family:ui-monospace,monospace;letter-spacing:.5px;background:var(--panel2);padding:11px;border-radius:10px;border:1px solid var(--line)}.section-gap{height:16px}.mobile-menu{display:none}.health-list{display:grid;gap:10px}.health-row{display:flex;justify-content:space-between;align-items:center;padding:11px 0;border-bottom:1px solid var(--line)}
@media(max-width:900px){.shell{grid-template-columns:1fr}.sidebar{display:none}.grid{grid-template-columns:1fr 1fr}.two{grid-template-columns:1fr}.mobile-menu{display:block}.page{padding:16px}.topbar{padding:0 16px}}
@media(max-width:540px){.grid{grid-template-columns:1fr}.topbar{height:auto;padding:13px 16px;gap:10px}.title{font-size:22px}}
</style>
"""

def layout(title: str, body: str, request: Request | None = None, active: str = ""):
    if request is not None and logged_in(request):
        role = request.session.get("role", "super_admin")
        nav = [
            ("dashboard","Dashboard","/dashboard","dashboard_view"),
            ("employees","Employees","/employees","employees_view"),
            ("pending","Approvals","/pending","approvals_view"),
            ("hr","User Accounts","/hr-accounts","user_accounts_view"),
            ("audit","Activity Logs","/audit-logs","audit_view"),
            ("settings","Settings","/settings","settings_view"),
        ]
        nav = [item for item in nav if has_permission(request, item[3])]
        links = "".join(f"<a class='{"active" if active==k else ""}' href='{u}'>{label}</a>" for k,label,u,_ in nav)
        user_name = escape(str(request.session.get("user_name", "Admin")))
        role_label = escape(role.replace("_", " ").title())
        body = f"<div class='shell'><aside class='sidebar'><div class='logo'>BURAQ Smart Attendance</div><div class='side-sub'>Enterprise Workforce Control Center</div><nav class='side-nav'>{links}{"<a href='/export/attendance.csv'>Export Attendance</a>" if has_permission(request, 'reports_export') else ''}<a href='/logout'>Logout</a></nav><div style='position:absolute;bottom:22px;left:18px;right:18px;padding:12px;border-radius:12px;background:rgba(255,255,255,.08)'><b>{user_name}</b><div class='side-sub' style='margin:3px 0 0'>{role_label}</div></div></aside><main class='main'><header class='topbar'><div><div class='title'>{escape(title)}</div><div class='sub'>Face AI • GPS • WhatsApp • HR Control</div></div><button id='themeToggle' class='btn secondary' type='button'>◐ Theme</button></header><div class='page'>{body}</div></main></div>"
    script = """<script>(function(){const root=document.documentElement;const saved=localStorage.getItem('buraq-theme');if(saved)root.dataset.theme=saved;document.getElementById('themeToggle')?.addEventListener('click',()=>{const next=root.dataset.theme==='dark'?'light':'dark';root.dataset.theme=next;localStorage.setItem('buraq-theme',next);});})();</script>"""
    return HTMLResponse(f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{escape(title)}</title>{CSS}</head><body>{body}{script}</body></html>")

def hash_password(password: str, salt: str | None = None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 180000).hex()
    return f"{salt}${digest}"

def verify_password(password: str, stored: str):
    try:
        salt, _ = stored.split("$", 1)
        return hmac.compare_digest(hash_password(password, salt), stored)
    except Exception:
        return False

PERMISSION_CATALOG = {
    "dashboard_view": ("Dashboard", "Dashboard summary দেখবে"),
    "employees_view": ("Employees: View", "Employee list দেখবে"),
    "employees_add": ("Employees: Add", "নতুন employee যোগ করবে"),
    "employees_edit": ("Employees: Edit", "Employee তথ্য পরিবর্তন করবে"),
    "employees_delete": ("Employees: Delete", "Employee মুছতে পারবে"),
    "face_reset": ("Face AI: Reset", "Employee face profile reset করবে"),
    "approvals_view": ("Approvals: View", "Pending registration দেখবে"),
    "approvals_manage": ("Approvals: Approve/Reject", "Registration approve/reject করবে"),
    "reports_view": ("Reports: View", "Attendance report দেখবে"),
    "reports_export": ("Reports: Export", "CSV/PDF/Excel export করবে"),
    "audit_view": ("Audit Log: View", "Activity log দেখবে"),
    "settings_view": ("Settings: View", "সাধারণ settings page দেখবে"),
    "whatsapp_settings": ("WhatsApp Settings", "Token, Phone ID ও Webhook দেখবে/পরিবর্তন করবে"),
    "user_accounts_view": ("User Accounts: View", "Admin/HR account list দেখবে"),
    "user_accounts_manage": ("User Accounts: Manage", "Admin/HR account create, disable বা delete করবে"),
}
DEFAULT_ROLE_PERMISSIONS = {
    "admin": {"dashboard_view","employees_view","employees_add","employees_edit","face_reset","approvals_view","approvals_manage","reports_view","reports_export","audit_view"},
    "hr_manager": {"dashboard_view","employees_view","employees_add","employees_edit","face_reset","approvals_view","approvals_manage","reports_view","reports_export","audit_view"},
    "hr_executive": {"dashboard_view","employees_view","employees_add","employees_edit","approvals_view","approvals_manage","reports_view","reports_export"},
    "hr_officer": {"dashboard_view","employees_view","reports_view"},
    "viewer": {"dashboard_view","reports_view"},
}

def logged_in(request: Request): return bool(request.session.get("admin") or request.session.get("hr_id"))
def require_login(request: Request):
    if not logged_in(request): raise HTTPException(401, "Login required")

def current_permissions(request: Request):
    require_login(request)
    if request.session.get("role") == "super_admin" and request.session.get("admin"):
        return set(PERMISSION_CATALOG) | {"*"}
    account_id = request.session.get("hr_id")
    if not account_id:
        return set()
    with get_db() as c:
        rows = c.execute("SELECT permission FROM account_permissions WHERE account_id=?", (account_id,)).fetchall()
        raw = {r["permission"] for r in rows}
        if "__configured__" in raw:
            return {p for p in raw if p in PERMISSION_CATALOG}
        explicit = {p for p in raw if p in PERMISSION_CATALOG}
        if explicit:
            return explicit
        role = request.session.get("role", "viewer")
        return set(DEFAULT_ROLE_PERMISSIONS.get(role, set()))

def has_permission(request: Request, permission: str):
    if not logged_in(request):
        return False
    allowed = current_permissions(request)
    return "*" in allowed or permission in allowed

def require_permission(request: Request, permission: str):
    require_login(request)
    if not has_permission(request, permission):
        raise HTTPException(403, "Permission denied")

def require_super_admin(request: Request):
    require_login(request)
    if request.session.get("role") != "super_admin" or not request.session.get("admin"):
        raise HTTPException(403, "Super Admin access required")

def audit(request: Request, action: str, target_type: str = "", target_id: str = "", details: str = "", db=None):
    """Write an audit event without allowing audit logging to break the main action.

    When the caller already has an open write transaction, pass that connection as
    ``db``. Opening a second SQLite write transaction used to wait for 30 seconds
    and then raise ``database is locked`` during HR login.
    """
    actor_type = "admin" if request.session.get("admin") else "hr"
    actor_id = str(request.session.get("hr_id", "admin"))
    actor_name = str(request.session.get("user_name", "Admin"))
    ip = request.client.host if request.client else ""
    params = (actor_type, actor_id, actor_name, action, target_type, target_id, details, ip)
    sql = "INSERT INTO audit_logs(actor_type,actor_id,actor_name,action,target_type,target_id,details,ip_address) VALUES(?,?,?,?,?,?,?,?)"
    try:
        if db is not None:
            db.execute(sql, params)
        else:
            with get_db() as c:
                c.execute(sql, params)
    except Exception:
        logger.exception("Audit logging failed: action=%s target=%s:%s", action, target_type, target_id)

def base_url(request: Request): return str(request.base_url).rstrip("/")

@app.on_event("startup")
def startup():
    init_db()
    import_environment_defaults()
    if not get_setting("admin_email"):
        set_setting("admin_email", os.getenv("SUPER_ADMIN_EMAIL", "admin@buraq.com").strip().lower())
    if not get_setting("admin_name"):
        set_setting("admin_name", os.getenv("SUPER_ADMIN_NAME", "Super Admin").strip())
    imported = import_employees()
    logger.info("Employee master synced: %s", imported)

@app.get("/health")
def health():
    # Railway healthcheck is a liveness check. It must stay 200 while the
    # dashboard explains any optional database/configuration warning.
    return {
        "ok": True,
        "version": "8.1.1",
        "database": database_kind(),
        "database_connected": database_ok(),
        "whatsapp_configured": configured(),
    }

@app.get("/ready")
def ready():
    if not database_ok():
        raise HTTPException(503, "Database unavailable")
    return {"ready": True, "database": database_kind()}

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    if not get_setting("admin_password_hash"):
        return RedirectResponse("/setup", 302)
    if not logged_in(request): return RedirectResponse("/login", 302)
    return RedirectResponse("/dashboard", 302)

@app.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request):
    if get_setting("admin_password_hash"):
        return RedirectResponse("/dashboard" if logged_in(request) else "/login", 302)
    cfg_note = "<div class='notice'>Railway Variables থেকে WhatsApp configuration পাওয়া গেছে। শুধু Admin password তৈরি করুন।</div>" if configured() else "<div class='notice' style='background:#fef3c7;color:#92400e'>WhatsApp credentials পরে Dashboard → Settings থেকে যোগ করতে পারবেন।</div>"
    body=f"<div class='login'><div class='card'><div class='title'>BURAQ Smart Attendance</div><p class='sub'>প্রথমবারের নিরাপদ Admin setup</p>{cfg_note}<form method='post'><label>Super Admin email</label><input type='email' name='email' value='admin@buraq.com' required><label>নতুন Admin password</label><input type='password' name='password' minlength='6' required><label>Confirm password</label><input type='password' name='confirm_password' minlength='6' required><button class='btn' type='submit'>Create Admin & Open Dashboard</button></form><p class='sub'>WhatsApp Token, Phone Number ID এবং Verify Token এই page-এ আর চাইবে না।</p></div></div>"
    return layout("Initial Setup", body)

@app.post("/setup")
def save_setup(request: Request, email: str = Form(...), password: str = Form(...), confirm_password: str = Form(...)):
    if get_setting("admin_password_hash"):
        raise HTTPException(403)
    if password != confirm_password or len(password) < 6:
        raise HTTPException(400, "Passwords do not match or are too short")
    set_setting("admin_email", email.strip().lower())
    set_setting("admin_name", "Super Admin")
    set_setting("admin_password_hash", hash_password(password))
    request.session["admin"] = True
    request.session["role"] = "super_admin"
    request.session["user_name"] = get_setting("admin_name", "Super Admin")
    return RedirectResponse("/dashboard", 303)

@app.get("/login", response_class=HTMLResponse)
def login_page(error: str = ""):
    msg = "<div class='notice' style='background:#fee2e2;color:#991b1b'>Email অথবা Password সঠিক নয়।</div>" if error else ""
    body = f"""<div class='login'><div class='card'><div class='title'>BURAQ Smart Attendance</div><p class='sub'>Super Admin, Admin এবং HR-এর জন্য একটি নিরাপদ login</p>{msg}<form method='post'><label>Email</label><input type='email' name='email' placeholder='name@buraq.com' autocomplete='username' required><label>Password</label><input type='password' name='password' placeholder='Password' autocomplete='current-password' required><button class='btn' type='submit'>Sign In</button></form></div></div>"""
    return layout("Unified Login", body)

@app.post("/login")
def login(request: Request, password: str = Form(...), email: str = Form(...)):
    normalized_email = email.strip().lower()
    admin_email = get_setting("admin_email", "admin@buraq.com").strip().lower()
    admin_hash = get_setting("admin_password_hash")

    if normalized_email == admin_email and verify_password(password, admin_hash):
        request.session.clear()
        request.session["admin"] = True
        request.session["role"] = "super_admin"
        request.session["user_name"] = get_setting("admin_name", "Super Admin")
        audit(request, "login", "user_account", "super_admin", "super_admin login")
        return RedirectResponse("/dashboard", 303)

    with get_db() as c:
        row = c.execute(
            "SELECT * FROM hr_accounts WHERE LOWER(email)=LOWER(?) AND is_active=?",
            (normalized_email, True),
        ).fetchone()
        if row and verify_password(password, row["password_hash"]):
            request.session.clear()
            request.session["hr_id"] = row["id"]
            request.session["role"] = row["role"]
            request.session["user_name"] = row["name"]
            c.execute("UPDATE hr_accounts SET last_login_at=CURRENT_TIMESTAMP WHERE id=?", (row["id"],))
            # Use the same transaction to avoid SQLite's nested-write lock.
            audit(request, "login", "user_account", str(row["id"]), f"{row['role']} login", db=c)
            return RedirectResponse("/dashboard", 303)

    return RedirectResponse("/login?error=1", 303)

@app.get("/logout")
def logout(request: Request):
    request.session.clear(); return RedirectResponse("/login", 302)

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    require_permission(request, "dashboard_view")
    today = datetime.now(ZoneInfo(settings.timezone)).date().isoformat()
    with get_db() as c:
        employees = c.execute("SELECT COUNT(*) c FROM employees").fetchone()["c"]
        registered = c.execute("SELECT COUNT(*) c FROM employees WHERE registration_status='approved'").fetchone()["c"]
        pending = c.execute("SELECT COUNT(*) c FROM pending_registrations WHERE status='pending'").fetchone()["c"]
        present = c.execute("SELECT COUNT(*) c FROM attendance WHERE work_date=? AND check_in IS NOT NULL", (today,)).fetchone()["c"]
        checked_out = c.execute("SELECT COUNT(*) c FROM attendance WHERE work_date=? AND check_out IS NOT NULL", (today,)).fetchone()["c"]
        recent = c.execute("SELECT a.work_date,a.check_in,a.check_out,a.late_minutes,a.overtime_minutes,e.staff_id,e.name FROM attendance a JOIN employees e ON e.id=a.employee_id ORDER BY a.id DESC LIMIT 12").fetchall()
    cfg, db = configured(), database_ok()
    warning = database_warning()
    can_whatsapp = has_permission(request, "whatsapp_settings")
    can_export = has_permission(request, "reports_export")
    webhook = f"{base_url(request)}/webhook/whatsapp" if can_whatsapp else ""
    rows=''.join(f"<tr><td>{escape(str(r['work_date']))}</td><td><b>{escape(r['staff_id'])}</b></td><td>{escape(r['name'])}</td><td>{escape((r['check_in'] or '-')[11:16] if r['check_in'] else '-')}</td><td>{escape((r['check_out'] or '-')[11:16] if r['check_out'] else '-')}</td><td>{r['late_minutes']}m</td><td>{r['overtime_minutes']}m</td></tr>" for r in recent) or "<tr><td colspan='7'>এখনো attendance নেই</td></tr>"
    system_extra = ""
    if can_whatsapp:
        system_extra = f"<div class='sub'>Webhook URL</div><div class='code'>{escape(webhook)}</div>"
    else:
        system_extra = "<div class='notice'>আপনার account-এ WhatsApp Settings permission নেই; credentials ও webhook গোপন রাখা হয়েছে।</div>"
    whatsapp_panel = ""
    if can_whatsapp:
        whatsapp_panel = f"<div class='card'><h3>Quick WhatsApp Test</h3><form method='post' action='/test-message'><label>WhatsApp number</label><input name='phone' placeholder='8801XXXXXXXXX' required><label>Message</label><input name='message' value='BURAQ Attendance connected ✅'><button class='btn'>Send Test Message</button></form><p class='sub'>Pending approvals: <b>{pending}</b></p></div>"
    else:
        whatsapp_panel = f"<div class='card'><h3>Attendance Operations</h3><p class='sub'>Pending approvals</p><div class='metric'>{pending}</div><p class='sub'>Webhook URL, access token, Phone Number ID and Verify Token are hidden for this account.</p></div>"
    body=f"<div class='grid'><div class='card'><div class='sub'>Total Employees</div><div class='metric'>{employees}</div></div><div class='card'><div class='sub'>Registered</div><div class='metric'>{registered}</div></div><div class='card'><div class='sub'>Present Today</div><div class='metric'>{present}</div></div><div class='card'><div class='sub'>Checked Out</div><div class='metric'>{checked_out}</div></div></div><div class='section-gap'></div><div class='two'><div class='card'><h3>System Health</h3><div class='health-list'><div class='health-row'><span>Database</span><span class='status {'ok' if db else 'bad'}'>{escape(database_kind())}</span></div><div class='health-row'><span>WhatsApp</span><span class='status {'ok' if cfg else 'warn'}'>{'Connected' if cfg else 'Setup needed'}</span></div><div class='health-row'><span>Webhook</span><span class='status ok'>Active</span></div><div class='health-row'><span>Face AI</span><span class='status ok'>Ready</span></div></div>{f"<div class='notice' style='background:#fef3c7;color:#92400e'>{escape(warning)}</div>" if warning else ''}{system_extra}</div>{whatsapp_panel}</div><div class='section-gap'></div><div class='card'><div class='actions' style='justify-content:space-between;align-items:center'><h2>Recent Attendance</h2>{"<a class='btn secondary' href='/export/attendance.csv'>Download CSV</a>" if can_export else ''}</div><div style='overflow:auto'><table><thead><tr><th>Date</th><th>Staff ID</th><th>Name</th><th>In</th><th>Out</th><th>Late</th><th>OT</th></tr></thead><tbody>{rows}</tbody></table></div></div>"
    return layout("Dashboard", body, request, "dashboard")

@app.post("/test-message")
async def test_message(request: Request, phone: str = Form(...), message: str = Form(...)):
    require_permission(request, "whatsapp_settings")
    result = await send_text(phone.strip(), message.strip())
    return RedirectResponse("/dashboard" if result.get("sent") else "/settings?error=send", 303)

def mask_secret(value: str, visible: int = 4) -> str:
    if not value: return "Not configured"
    if len(value) <= visible * 2: return "•" * len(value)
    return value[:visible] + "•" * 12 + value[-visible:]

@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: str = "", error: str = ""):
    require_permission(request, "settings_view")
    access=get_setting("whatsapp_access_token"); phone=get_setting("whatsapp_phone_number_id"); verify=get_setting("whatsapp_verify_token")
    notice = "<div class='notice'>Settings সফলভাবে save হয়েছে।</div>" if saved else ("<div class='notice' style='background:#fee2e2;color:#991b1b'>Action ব্যর্থ হয়েছে। তথ্য পরীক্ষা করুন।</div>" if error else "")
    webhook=f"{base_url(request)}/webhook/whatsapp"
    if has_permission(request, "whatsapp_settings"):
        body=f"{notice}<div class='two'><div class='card'><h2>WhatsApp Connection</h2><p><span class='status {'ok' if configured() else 'warn'}'>{'Connected' if configured() else 'Setup needed'}</span></p><div class='sub'>Access Token</div><div class='masked'>{escape(mask_secret(access))}</div><br><div class='sub'>Phone Number ID</div><div class='masked'>{escape(mask_secret(phone))}</div><br><div class='sub'>Verify Token</div><div class='masked'>{escape(mask_secret(verify))}</div><br><details><summary class='btn secondary'>Edit credentials</summary><form method='post' style='margin-top:15px'><label>New Access Token</label><input type='password' name='access_token'><label>New Phone Number ID</label><input name='phone_id'><label>New Verify Token</label><input type='password' name='verify_token'><button class='btn'>Save securely</button></form></details></div><div class='card'><h2>Connection & Webhook</h2><div class='sub'>Callback URL</div><div class='code'>{escape(webhook)}</div><p class='sub'>Sensitive access is controlled by Super Admin permission.</p></div></div>"
    else:
        body=f"{notice}<div class='card'><h2>General Settings</h2><div class='notice'>আপনার account-এ WhatsApp Settings permission নেই। Token, Phone Number ID, Verify Token এবং Webhook URL গোপন রাখা হয়েছে।</div></div>"
    return layout("Settings", body, request, "settings")

@app.post("/settings")
def save_settings(request: Request, access_token: str = Form(""), phone_id: str = Form(""), verify_token: str = Form("")):
    require_permission(request, "whatsapp_settings")
    if access_token.strip(): set_setting("whatsapp_access_token", access_token.strip())
    if phone_id.strip(): set_setting("whatsapp_phone_number_id", phone_id.strip())
    if verify_token.strip(): set_setting("whatsapp_verify_token", verify_token.strip())
    return RedirectResponse("/settings?saved=1", 303)

@app.post("/settings/password")
def change_password(request: Request, current_password: str = Form(...), new_password: str = Form(...)):
    require_super_admin(request)
    if not verify_password(current_password, get_setting("admin_password_hash")) or len(new_password) < 6:
        return RedirectResponse("/settings?error=password", 303)
    set_setting("admin_password_hash", hash_password(new_password))
    return RedirectResponse("/settings?saved=password", 303)

@app.get("/settings/backup")
def backup_settings(request: Request):
    require_super_admin(request)
    payload={"version":1,"encrypted":True,"created_at":datetime.now(ZoneInfo(settings.timezone)).isoformat(),"settings":{key:get_stored_setting(key) for key in ("whatsapp_access_token","whatsapp_phone_number_id","whatsapp_verify_token")},"plain_settings":{"meta_api_version":get_setting("meta_api_version","v23.0")}}
    data=json.dumps(payload,ensure_ascii=False,indent=2).encode("utf-8")
    return StreamingResponse(io.BytesIO(data),media_type="application/json",headers={"Content-Disposition":"attachment; filename=BURAQ-Config-Backup.json"})

@app.post("/settings/restore")
async def restore_settings(request: Request):
    require_super_admin(request)
    form=await request.form(); upload=form.get("backup_file")
    try:
        data=json.loads((await upload.read()).decode("utf-8"))
        values=data.get("settings",{})
        if data.get("encrypted"):
            for key in ("whatsapp_access_token","whatsapp_phone_number_id","whatsapp_verify_token"):
                if values.get(key): restore_stored_setting(key,str(values[key]))
            plain=data.get("plain_settings",{})
            if plain.get("meta_api_version"): set_setting("meta_api_version",str(plain["meta_api_version"]))
        else:
            for key in ("whatsapp_access_token","whatsapp_phone_number_id","whatsapp_verify_token","meta_api_version"):
                if values.get(key): set_setting(key,str(values[key]))
    except Exception:
        return RedirectResponse("/settings?error=restore",303)
    return RedirectResponse("/settings?saved=restore",303)

@app.get("/employees", response_class=HTMLResponse)
def employees_page(request: Request):
    require_permission(request, "employees_view")
    with get_db() as c:
        rows=c.execute("SELECT e.*,(SELECT COUNT(*) FROM face_samples f WHERE f.employee_id=e.id) face_count FROM employees e ORDER BY staff_id").fetchall()
    can_add=has_permission(request,"employees_add")
    can_reset=has_permission(request,"face_reset")
    tr_parts=[]
    for r in rows:
        reset_action = f"<form method='post' action='/employees/{r['id']}/reset-face'><button class='btn danger'>Reset Face</button></form>" if can_reset else "<span class='sub'>View only</span>"
        status_class = 'ok' if r['registration_status']=='approved' else 'warn'
        face_class = 'ok' if r['face_count']>=3 else 'bad'
        tr_parts.append(f"<tr><td><b>{escape(r['staff_id'])}</b></td><td>{escape(r['name'])}</td><td>{escape(r['phone'] or '')}</td><td>{escape(r['department'] or '')}</td><td>{escape(r['shift'])}</td><td><span class='status {status_class}'>{escape(r['registration_status'])}</span></td><td><span class='status {face_class}'>{r['face_count']}/3</span></td><td>{reset_action}</td></tr>")
    trs=''.join(tr_parts) or "<tr><td colspan='8'>কোনো employee নেই</td></tr>"
    add_card=""
    if can_add:
        add_card="""<div class='card'><h2>Add Employee</h2><form method='post'><label>Staff ID</label><input name='staff_id' required><label>Name</label><input name='name' required><label>Phone</label><input name='phone' placeholder='8801XXXXXXXXX'><label>Department</label><input name='department'><label>Shift</label><select name='shift'><option value='morning'>Morning 8AM–4PM</option><option value='evening'>Evening 4PM–10PM</option></select><button class='btn'>Add Employee</button></form></div>"""
    body=f"<div class='two'>{add_card}<div class='card'><h2>Employee List</h2><div style='overflow:auto'><table><thead><tr><th>ID</th><th>Name</th><th>Phone</th><th>Dept.</th><th>Shift</th><th>Status</th><th>Face AI</th><th>Action</th></tr></thead><tbody>{trs}</tbody></table></div></div></div>"
    return layout("Employees", body, request, "employees")

@app.post("/employees")
def add_employee(request: Request, staff_id: str = Form(...), name: str = Form(...), phone: str = Form(""), department: str = Form(""), shift: str = Form("morning")):
    require_permission(request, "employees_add")
    try:
        with get_db() as c: c.execute("INSERT INTO employees(staff_id,name,phone,department,shift) VALUES(?,?,?,?,?)", (staff_id.strip(), name.strip(), phone.strip() or None, department.strip() or None, shift))
    except Exception as exc:
        logger.warning("Employee add failed: %s", exc)
    return RedirectResponse("/employees", 303)

@app.post("/employees/{employee_id}/reset-face")
def reset_employee_face(request: Request, employee_id: int):
    require_permission(request, "face_reset")
    with get_db() as c:
        employee=c.execute("SELECT whatsapp_phone,phone FROM employees WHERE id=?",(employee_id,)).fetchone()
        c.execute("DELETE FROM face_samples WHERE employee_id=?",(employee_id,))
        c.execute("DELETE FROM face_profiles WHERE employee_id=?",(employee_id,))
        if employee:
            phone=employee["whatsapp_phone"] or employee["phone"]
            if phone:
                c.execute("INSERT INTO conversation_states(phone,state) VALUES(?,?) ON CONFLICT(phone) DO UPDATE SET state=excluded.state,updated_at=CURRENT_TIMESTAMP",(phone,"awaiting_face_registration"))
    return RedirectResponse("/employees",303)

@app.get("/pending", response_class=HTMLResponse)
def pending_page(request: Request):
    require_permission(request, "approvals_view")
    with get_db() as c:
        rows=c.execute("SELECT p.id,p.whatsapp_phone,p.created_at,e.staff_id,e.name FROM pending_registrations p JOIN employees e ON e.id=p.employee_id WHERE p.status='pending' ORDER BY p.id DESC").fetchall()
    can_manage=has_permission(request,"approvals_manage")
    parts=[]
    for r in rows:
        approve = f"<form method='post' action='/pending/{r['id']}/approve'><button class='btn'>Approve</button></form>" if can_manage else "<span class='sub'>View only</span>"
        reject = f"<form method='post' action='/pending/{r['id']}/reject'><button class='btn danger'>Reject</button></form>" if can_manage else ""
        parts.append(f"<tr><td>{escape(r['staff_id'])}</td><td>{escape(r['name'])}</td><td>{escape(r['whatsapp_phone'])}</td><td>{approve}</td><td>{reject}</td></tr>")
    trs=''.join(parts) or "<tr><td colspan='5'>কোনো pending registration নেই</td></tr>"
    return layout("Approvals", f"<div class='card'><table><thead><tr><th>Staff ID</th><th>Name</th><th>WhatsApp</th><th></th><th></th></tr></thead><tbody>{trs}</tbody></table></div>", request, "pending")

@app.post("/pending/{pending_id}/approve")
def approve_pending(request: Request, pending_id: int, background_tasks: BackgroundTasks):
    require_login(request)
    notify = None
    with get_db() as c:
        row=c.execute("SELECT p.*,e.name,e.staff_id FROM pending_registrations p JOIN employees e ON e.id=p.employee_id WHERE p.id=? AND p.status='pending'", (pending_id,)).fetchone()
        if row:
            c.execute("UPDATE employees SET whatsapp_phone=?,registration_status='approved',updated_at=CURRENT_TIMESTAMP WHERE id=?", (row['whatsapp_phone'],row['employee_id']))
            c.execute("UPDATE pending_registrations SET status='approved',updated_at=CURRENT_TIMESTAMP WHERE id=?", (pending_id,))
            c.execute("INSERT INTO conversation_states(phone,state) VALUES(?,?) ON CONFLICT(phone) DO UPDATE SET state=excluded.state,updated_at=CURRENT_TIMESTAMP", (row['whatsapp_phone'],'awaiting_face_registration'))
            notify = (row['whatsapp_phone'], row['name'], row['staff_id'])
    if notify:
        background_tasks.add_task(send_approval_flow, *notify)
    return RedirectResponse("/pending",303)

@app.post("/pending/{pending_id}/reject")
def reject_pending(request: Request, pending_id: int):
    require_permission(request, "approvals_manage")
    with get_db() as c: c.execute("UPDATE pending_registrations SET status='rejected',updated_at=CURRENT_TIMESTAMP WHERE id=?", (pending_id,))
    return RedirectResponse("/pending",303)

@app.get("/export/attendance.csv")
def export_attendance(request: Request):
    require_permission(request, "reports_export")
    with get_db() as c: rows=c.execute("SELECT a.work_date,e.staff_id,e.name,e.department,e.shift,a.check_in,a.check_out,a.late_minutes,a.early_leave_minutes,a.overtime_minutes,a.status FROM attendance a JOIN employees e ON e.id=a.employee_id ORDER BY a.work_date DESC,e.staff_id").fetchall()
    output=io.StringIO(); writer=csv.writer(output); writer.writerow(["Date","Staff ID","Name","Department","Shift","Check In","Check Out","Late Minutes","Early Leave Minutes","Overtime Minutes","Status"])
    for r in rows: writer.writerow(list(r.values()))
    data=output.getvalue().encode("utf-8-sig")
    return StreamingResponse(io.BytesIO(data),media_type="text/csv",headers={"Content-Disposition":"attachment; filename=BURAQ-Attendance.csv"})


@app.get("/hr-accounts", response_class=HTMLResponse)
def hr_accounts_page(request: Request, saved: str = "", error: str = ""):
    require_permission(request, "user_accounts_view")
    can_manage = has_permission(request, "user_accounts_manage")
    is_super = request.session.get("role") == "super_admin" and request.session.get("admin")
    with get_db() as c:
        rows = c.execute("SELECT * FROM hr_accounts ORDER BY is_active DESC, name").fetchall()
        permission_rows = c.execute("SELECT account_id,permission FROM account_permissions").fetchall()
    by_account = {}
    for pr in permission_rows:
        by_account.setdefault(pr["account_id"], set()).add(pr["permission"])
    notice = "<div class='notice'>Account or permissions saved successfully.</div>" if saved else ("<div class='notice' style='background:#fee2e2;color:#991b1b'>Action failed. Check account data and permissions.</div>" if error else "")
    trs=[]
    for r in rows:
        perms=by_account.get(r['id']) or DEFAULT_ROLE_PERMISSIONS.get(r['role'], set())
        chips=' '.join(f"<span class='status ok' style='margin:2px'>{escape(PERMISSION_CATALOG[p][0])}</span>" for p in sorted(perms) if p in PERMISSION_CATALOG) or "<span class='sub'>No permissions</span>"
        actions=""
        if can_manage:
            actions=f"<div class='actions'><form method='post' action='/hr-accounts/{r['id']}/toggle'><button class='btn secondary'>{'Disable' if r['is_active'] else 'Enable'}</button></form><form method='post' action='/hr-accounts/{r['id']}/delete' onsubmit=\"return confirm('Delete this account?')\"><button class='btn danger'>Delete</button></form></div>"
        if is_super:
            actions += f"<a class='btn secondary' href='/hr-accounts/{r['id']}/permissions'>Permissions</a>"
        trs.append(f"<tr><td><b>{escape(r['name'])}</b><div class='sub'>{escape(r['email'])}</div></td><td>{escape(r['role'].replace('_',' ').title())}</td><td><span class='status {'ok' if r['is_active'] else 'bad'}'>{'Active' if r['is_active'] else 'Disabled'}</span></td><td><div style='max-width:520px'>{chips}</div></td><td>{actions or '<span class=\'sub\'>View only</span>'}</td></tr>")
    create_card=""
    if can_manage:
        create_card="""<div class='card'><h2>Add Admin / HR Account</h2><form method='post'><label>Full Name</label><input name='name' required><label>Email</label><input type='email' name='email' required><label>Role</label><select name='role'><option value='admin'>Admin</option><option value='hr_manager'>HR Manager</option><option value='hr_executive'>HR Executive</option><option value='hr_officer'>HR Officer</option><option value='viewer'>Viewer</option></select><label>Temporary Password</label><input type='password' name='password' minlength='8' required><button class='btn'>Create Account</button></form></div>"""
    body=f"{notice}<div class='two'>{create_card}<div class='card'><h2>Dynamic Permission Rules</h2><p>Super Admin প্রতিটি account-এর জন্য আলাদা permission নির্বাচন করবে। Permission না থাকলে menu লুকানো থাকবে এবং direct URL-এ 403 হবে।</p><p class='sub'>Permission changes audit log-এ সংরক্ষিত হয় এবং পরের request থেকেই কার্যকর হয়।</p></div></div><div class='section-gap'></div><div class='card'><h2>Admin & HR Accounts</h2><div style='overflow:auto'><table><thead><tr><th>User</th><th>Role</th><th>Status</th><th>Current Permissions</th><th>Action</th></tr></thead><tbody>{''.join(trs) or '<tr><td colspan=\'5\'>No accounts yet.</td></tr>'}</tbody></table></div></div>"
    return layout("User Accounts", body, request, "hr")

@app.post("/hr-accounts")
def add_hr_account(request: Request, name: str = Form(...), email: str = Form(...), role: str = Form(...), password: str = Form(...)):
    require_permission(request, "user_accounts_manage")
    if role not in DEFAULT_ROLE_PERMISSIONS or len(password) < 8:
        return RedirectResponse("/hr-accounts?error=1",303)
    try:
        with get_db() as c:
            cur=c.execute("INSERT INTO hr_accounts(name,email,password_hash,role) VALUES(?,?,?,?)", (name.strip(), email.strip().lower(), hash_password(password), role))
            account_id=getattr(cur, 'lastrowid', None)
            if not account_id:
                row=c.execute("SELECT id FROM hr_accounts WHERE LOWER(email)=LOWER(?)",(email.strip(),)).fetchone(); account_id=row['id']
            c.execute("INSERT INTO account_permissions(account_id,permission) VALUES(?,?) ON CONFLICT(account_id,permission) DO NOTHING",(account_id,"__configured__"))
            for permission in DEFAULT_ROLE_PERMISSIONS.get(role,set()):
                c.execute("INSERT INTO account_permissions(account_id,permission) VALUES(?,?) ON CONFLICT(account_id,permission) DO NOTHING",(account_id,permission))
        audit(request,"create","hr_account",email.strip().lower(),f"Role: {role}")
    except Exception as exc:
        logger.warning("Account create failed: %s", exc)
        return RedirectResponse("/hr-accounts?error=1",303)
    return RedirectResponse("/hr-accounts?saved=1",303)

@app.post("/hr-accounts/{account_id}/toggle")
def toggle_hr_account(request: Request, account_id: int):
    require_permission(request, "user_accounts_manage")
    with get_db() as c:
        row=c.execute("SELECT is_active,email FROM hr_accounts WHERE id=?",(account_id,)).fetchone()
        if row:
            c.execute("UPDATE hr_accounts SET is_active=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (not bool(row['is_active']), account_id))
    audit(request,"toggle","hr_account",str(account_id),row['email'] if row else '')
    return RedirectResponse("/hr-accounts",303)

@app.post("/hr-accounts/{account_id}/delete")
def delete_hr_account(request: Request, account_id: int):
    require_permission(request, "user_accounts_manage")
    with get_db() as c:
        row=c.execute("SELECT email FROM hr_accounts WHERE id=?",(account_id,)).fetchone()
        c.execute("DELETE FROM hr_accounts WHERE id=?",(account_id,))
    audit(request,"delete","hr_account",str(account_id),row['email'] if row else '')
    return RedirectResponse("/hr-accounts",303)

@app.get("/hr-accounts/{account_id}/permissions", response_class=HTMLResponse)
def account_permissions_page(request: Request, account_id: int, saved: str = ""):
    require_super_admin(request)
    with get_db() as c:
        account=c.execute("SELECT * FROM hr_accounts WHERE id=?",(account_id,)).fetchone()
        rows=c.execute("SELECT permission FROM account_permissions WHERE account_id=?",(account_id,)).fetchall()
    if not account: raise HTTPException(404,"Account not found")
    selected={r['permission'] for r in rows} or DEFAULT_ROLE_PERMISSIONS.get(account['role'],set())
    checks=''.join(f"<label style='display:flex;gap:10px;align-items:flex-start;padding:12px;border:1px solid var(--line);border-radius:12px;margin-bottom:9px'><input style='width:auto;margin:3px 0' type='checkbox' name='permissions' value='{escape(key)}' {'checked' if key in selected else ''}><span><b>{escape(label)}</b><div class='sub'>{escape(desc)}</div></span></label>" for key,(label,desc) in PERMISSION_CATALOG.items())
    notice="<div class='notice'>Permissions updated.</div>" if saved else ""
    body=f"{notice}<div class='card' style='max-width:900px'><h2>{escape(account['name'])}</h2><p class='sub'>{escape(account['email'])} • {escape(account['role'].replace('_',' ').title())}</p><form method='post'>{checks}<div class='actions'><button class='btn'>Save Permissions</button><a class='btn secondary' href='/hr-accounts'>Back</a></div></form></div>"
    return layout("Account Permissions",body,request,"hr")

@app.post("/hr-accounts/{account_id}/permissions")
async def save_account_permissions(request: Request, account_id: int):
    require_super_admin(request)
    form=await request.form()
    selected={p for p in form.getlist('permissions') if p in PERMISSION_CATALOG}
    with get_db() as c:
        account=c.execute("SELECT email FROM hr_accounts WHERE id=?",(account_id,)).fetchone()
        if not account: raise HTTPException(404,"Account not found")
        c.execute("DELETE FROM account_permissions WHERE account_id=?",(account_id,))
        c.execute("INSERT INTO account_permissions(account_id,permission) VALUES(?,?)",(account_id,"__configured__"))
        for permission in sorted(selected):
            c.execute("INSERT INTO account_permissions(account_id,permission) VALUES(?,?)",(account_id,permission))
    audit(request,"permissions_update","hr_account",str(account_id),", ".join(sorted(selected)))
    return RedirectResponse(f"/hr-accounts/{account_id}/permissions?saved=1",303)

@app.get("/audit-logs", response_class=HTMLResponse)
def audit_logs_page(request: Request):
    require_permission(request, "audit_view")
    with get_db() as c:
        rows=c.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 300").fetchall()
    trs=''.join(f"<tr><td>{escape(str(r['created_at']))}</td><td><b>{escape(r['actor_name'] or r['actor_type'])}</b><div class='sub'>{escape(r['actor_type'])}</div></td><td>{escape(r['action'])}</td><td>{escape((r['target_type'] or '')+' '+(r['target_id'] or ''))}</td><td>{escape(r['details'] or '')}</td><td>{escape(r['ip_address'] or '')}</td></tr>" for r in rows) or "<tr><td colspan='6'>No activity yet.</td></tr>"
    return layout("Activity Logs",f"<div class='card'><h2>Security & Activity Audit</h2><div style='overflow:auto'><table><thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Target</th><th>Details</th><th>IP</th></tr></thead><tbody>{trs}</tbody></table></div></div>",request,"audit")

@app.get("/webhook/whatsapp", response_class=PlainTextResponse)
def verify(hub_mode: str | None = Query(None, alias="hub.mode"), hub_verify_token: str | None = Query(None, alias="hub.verify_token"), hub_challenge: str | None = Query(None, alias="hub.challenge")):
    if hub_mode == "subscribe" and hub_verify_token == get_setting("whatsapp_verify_token"):
        return hub_challenge or ""
    raise HTTPException(403, "Webhook verification failed")

@app.post("/webhook/whatsapp")
async def webhook(request: Request):
    payload=await request.json(); processed=await handle(payload); return {"status":"ok","processed":processed}
