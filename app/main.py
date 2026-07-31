import csv
import asyncio
import hashlib
import hmac
import io
import json
import logging
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from html import escape

from fastapi import FastAPI, BackgroundTasks, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.database import database_kind, database_ok, database_warning, get_db, init_db
from app.runtime import configured, get_setting, set_setting, import_environment_defaults, get_stored_setting, restore_stored_setting
from app.employee_seed import import_employees
from app.whatsapp import handle, send_approval_flow, send_text
from app.reminders import reminder_worker

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logger = logging.getLogger(__name__)
app = FastAPI(title=settings.app_name, version="9.10.0", docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET", secrets.token_urlsafe(32)), https_only=settings.environment == "production", same_site="lax")

@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("Unhandled request error request_id=%s method=%s path=%s", request_id, request.method, request.url.path)
        return JSONResponse({"detail": "Internal server error", "request_id": request_id}, status_code=500)
    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Response-Time-ms"] = str(duration_ms)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    logger.info("request_id=%s method=%s path=%s status=%s duration_ms=%s", request_id, request.method, request.url.path, response.status_code, duration_ms)
    return response

CSS = """
<style>
:root{--bg:#f4f7f6;--panel:#ffffff;--panel2:#f8faf9;--ink:#15211e;--muted:#697873;--brand:#087f5b;--brand2:#066747;--line:#dfe8e4;--ok:#15803d;--warn:#b45309;--bad:#b91c1c;--shadow:0 12px 34px rgba(22,59,49,.09)}
[data-theme="dark"]{--bg:#0f1715;--panel:#17201d;--panel2:#1c2824;--ink:#eef7f3;--muted:#a4b5af;--brand:#20a97a;--brand2:#37bd8f;--line:#2b3b36;--shadow:none}
*{box-sizing:border-box}html{color-scheme:light}html[data-theme="dark"]{color-scheme:dark}body{margin:0;background:var(--bg);font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif;color:var(--ink)}
a{color:inherit}.shell{min-height:100vh;display:grid;grid-template-columns:250px 1fr}.sidebar{background:#0d3b2e;color:#fff;padding:24px 18px;position:sticky;top:0;height:100vh}.logo{font-size:22px;font-weight:900;line-height:1.2;margin-bottom:6px}.logo:before{content:'◉';color:#59d4a9;margin-right:8px}.side-sub{font-size:12px;color:#b8d4ca;margin-bottom:28px}.side-nav{display:grid;gap:7px}.side-nav a{padding:11px 13px;border-radius:11px;text-decoration:none;color:#d8ebe4;font-weight:650}.side-nav a:hover,.side-nav a.active{background:rgba(255,255,255,.13);color:#fff}.main{min-width:0}.topbar{height:70px;background:var(--panel);border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:0 28px;position:sticky;top:0;z-index:5}.page{padding:26px;max-width:1400px;margin:auto}.title{font-size:27px;font-weight:850;letter-spacing:-.5px}.sub{color:var(--muted);font-size:14px}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:15px}.two{display:grid;grid-template-columns:1fr 1fr;gap:16px}.card{background:var(--panel);border:1px solid var(--line);border-radius:17px;padding:20px;box-shadow:var(--shadow)}.metric{font-size:31px;font-weight:850;margin-top:7px}.status{display:inline-flex;align-items:center;gap:6px;padding:7px 11px;border-radius:999px;font-size:13px;font-weight:750}.status:before{content:'●';font-size:10px}.ok{background:#dcfce7;color:var(--ok)}.warn{background:#fef3c7;color:var(--warn)}.bad{background:#fee2e2;color:var(--bad)}.actions{display:flex;gap:9px;flex-wrap:wrap}.btn{border:0;border-radius:11px;padding:10px 14px;background:var(--brand);color:#fff;font-weight:750;cursor:pointer;text-decoration:none;display:inline-block}.btn:hover{background:var(--brand2)}.btn.secondary{background:var(--panel2);border:1px solid var(--line);color:var(--ink)}.btn.danger{background:#fee2e2;color:var(--bad)}input,select,textarea{width:100%;padding:11px 12px;border:1px solid var(--line);border-radius:10px;margin:6px 0 14px;background:var(--panel);color:var(--ink)}label{font-size:14px;font-weight:700}table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:11px;border-bottom:1px solid var(--line)}th{color:var(--muted)}h2{margin:0 0 14px}h3{margin:0 0 10px}.notice{padding:13px 15px;border-radius:12px;background:#ecfeff;color:#155e75;margin-bottom:16px}.code{font-family:ui-monospace,monospace;background:#111827;color:#f9fafb;padding:12px;border-radius:10px;overflow:auto}.login{max-width:450px;margin:7vh auto;padding:18px}.login .card{padding:30px}.masked{font-family:ui-monospace,monospace;letter-spacing:.5px;background:var(--panel2);padding:11px;border-radius:10px;border:1px solid var(--line)}.section-gap{height:16px}.mobile-menu{display:none}.health-list{display:grid;gap:10px}.health-row{display:flex;justify-content:space-between;align-items:center;padding:11px 0;border-bottom:1px solid var(--line)}

.hero{display:flex;justify-content:space-between;gap:20px;align-items:center;padding:24px;background:linear-gradient(135deg,var(--panel),var(--panel2));border:1px solid var(--line);border-radius:20px;box-shadow:var(--shadow);margin-bottom:16px}.hero h2{font-size:25px;margin-bottom:6px}.eyebrow{text-transform:uppercase;letter-spacing:1.4px;font-size:11px;font-weight:850;color:var(--brand)}.metric-card{position:relative;overflow:hidden}.metric-card:after{content:'';position:absolute;right:-25px;top:-25px;width:85px;height:85px;border-radius:50%;background:rgba(8,127,91,.08)}.metric-label{font-size:13px;color:var(--muted);font-weight:700}.metric-foot{margin-top:10px;font-size:12px;color:var(--muted)}.kpi-icon{width:38px;height:38px;display:grid;place-items:center;border-radius:11px;background:var(--panel2);border:1px solid var(--line);font-size:18px}.card-head{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:14px}.timeline{display:grid;gap:4px}.timeline-item{display:grid;grid-template-columns:42px 1fr auto;gap:12px;align-items:center;padding:11px 0;border-bottom:1px solid var(--line)}.avatar{width:38px;height:38px;border-radius:12px;display:grid;place-items:center;background:var(--panel2);font-weight:850;color:var(--brand);border:1px solid var(--line)}.pill{display:inline-flex;padding:5px 9px;border-radius:999px;font-size:11px;font-weight:800;background:var(--panel2);border:1px solid var(--line)}.chart{height:210px;display:flex;gap:10px;align-items:flex-end;padding:18px 8px 4px}.bar-wrap{flex:1;min-width:0;text-align:center}.bar{min-height:5px;border-radius:8px 8px 3px 3px;background:linear-gradient(180deg,var(--brand),var(--brand2));position:relative}.bar-value{position:absolute;top:-22px;left:50%;transform:translateX(-50%);font-size:11px;font-weight:800}.bar-label{font-size:11px;color:var(--muted);margin-top:8px}.progress{height:8px;background:var(--panel2);border-radius:999px;overflow:hidden;border:1px solid var(--line)}.progress span{display:block;height:100%;background:var(--brand);border-radius:999px}.quick-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}.quick-link{padding:14px;border:1px solid var(--line);border-radius:13px;text-decoration:none;background:var(--panel2);font-weight:750}.quick-link:hover{border-color:var(--brand);transform:translateY(-1px)}.health-dot{width:9px;height:9px;border-radius:50%;display:inline-block;background:var(--ok);box-shadow:0 0 0 4px rgba(21,128,61,.12)}
.profile-hero{display:grid;grid-template-columns:110px 1fr auto;gap:20px;align-items:center}.profile-photo{width:104px;height:104px;border-radius:24px;object-fit:cover;background:var(--panel2);border:1px solid var(--line);display:grid;place-items:center;font-size:35px;font-weight:900;color:var(--brand)}.facts{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.fact{padding:13px;background:var(--panel2);border:1px solid var(--line);border-radius:12px}.fact b{display:block;margin-top:4px}.calendar{display:grid;grid-template-columns:repeat(7,1fr);gap:7px}.cal-day{min-height:76px;border:1px solid var(--line);border-radius:11px;padding:8px;background:var(--panel2)}.cal-day.empty{opacity:.35}.cal-day.present{border-left:4px solid #15803d}.cal-day.late{border-left:4px solid #b45309}.cal-day.leave{border-left:4px solid #2563eb}.cal-day.absent{border-left:4px solid #b91c1c}.searchbar{display:grid;grid-template-columns:2fr repeat(3,1fr) auto;gap:10px;align-items:end}.checkbox{width:auto;margin:0}.table-actions{display:flex;gap:6px;flex-wrap:wrap}.tag{display:inline-flex;padding:4px 8px;border-radius:999px;background:var(--panel2);border:1px solid var(--line);font-size:11px;font-weight:750}
.tabs{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0}.tab{padding:9px 13px;border:1px solid var(--line);border-radius:10px;text-decoration:none;background:var(--panel)}.summary-strip{display:grid;grid-template-columns:repeat(5,1fr);gap:12px}.rating{font-size:22px;font-weight:850}.stars{color:#b7791f;letter-spacing:2px}
.payroll-panel{background:linear-gradient(135deg,#0d3b2e,#087f5b);color:#fff;border:0}.payroll-panel .sub{color:#c8e6dc}.payroll-amount{font-size:34px;font-weight:900;letter-spacing:-1px}.money-card{position:relative;overflow:hidden}.money-card:after{content:'৳';position:absolute;right:14px;top:4px;font-size:64px;font-weight:900;color:rgba(8,127,91,.07)}.salary-breakdown{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.salary-part{padding:14px;border-radius:13px;background:var(--panel2);border:1px solid var(--line)}.salary-part b{display:block;font-size:18px;margin-top:5px}.confidential{display:inline-flex;gap:7px;align-items:center;padding:6px 10px;border-radius:999px;background:rgba(255,255,255,.14);font-size:11px;font-weight:800}
.sidebar{display:flex;flex-direction:column;overflow-y:auto}.side-nav{flex:0 0 auto}.side-account{margin-top:auto;padding:12px;border-radius:12px;background:rgba(255,255,255,.08);flex:0 0 auto}.side-account .side-sub{margin:3px 0 0}
@media(max-width:900px){.summary-strip{grid-template-columns:1fr 1fr}.shell{grid-template-columns:1fr}.sidebar{display:none}.grid{grid-template-columns:1fr 1fr}.two{grid-template-columns:1fr}.mobile-menu{display:block}.page{padding:16px}.topbar{padding:0 16px}}
@media(max-width:700px){.profile-hero{grid-template-columns:1fr}.facts{grid-template-columns:1fr 1fr}.salary-breakdown{grid-template-columns:1fr 1fr}.searchbar{grid-template-columns:1fr}.calendar{gap:4px}.cal-day{min-height:58px;padding:5px}}
@media(max-width:540px){.grid{grid-template-columns:1fr}.topbar{height:auto;padding:13px 16px;gap:10px}.title{font-size:22px}}
</style>
"""

def layout(title: str, body: str, request: Request | None = None, active: str = ""):
    if request is not None and logged_in(request):
        role = request.session.get("role", "super_admin")
        nav = [
            ("dashboard","Dashboard","/dashboard","dashboard_view"),
            ("employees","Employees","/employees","employees_view"),
            ("performance","Performance","/performance","performance_view"),
            ("pending","Approvals","/pending","approvals_view"),
            ("duplicates","Duplicate Analysis","/duplicates","approvals_view"),
            ("reports","Reports","/reports","reports_view"),
            ("payroll","Payroll","/payroll","payroll_view"),
            ("operations","HR Operations","/hr-operations","leave_view"),
            ("duty","Duty Scheduler","/duty-schedules","duty_view"),
            ("hr","User Accounts","/hr-accounts","user_accounts_view"),
            ("audit","Activity Logs","/audit-logs","audit_view"),
            ("settings","Settings","/settings","settings_view"),
        ]
        nav = [item for item in nav if has_permission(request, item[3])]
        links = "".join(f"<a class='{"active" if active==k else ""}' href='{u}'>{label}</a>" for k,label,u,_ in nav)
        user_name = escape(str(request.session.get("user_name", "Admin")))
        role_label = escape(role.replace("_", " ").title())
        body = f"<div class='shell'><aside class='sidebar'><div class='logo'>BURAQ Smart Attendance</div><div class='side-sub'>Enterprise Workforce Control Center</div><nav class='side-nav'>{links}{"<a href='/export/attendance.csv'>Export Attendance</a>" if has_permission(request, 'reports_export') else ''}<a href='/logout'>Logout</a></nav><div class='side-account'><b>{user_name}</b><div class='side-sub'>{role_label}</div></div></aside><main class='main'><header class='topbar'><div><div class='title'>{escape(title)}</div><div class='sub'>Face AI • GPS • WhatsApp • HR Control</div></div><button id='themeToggle' class='btn secondary' type='button'>◐ Theme</button></header><div class='page'>{body}</div></main></div>"
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
    "performance_view": ("Performance: View", "Performance review দেখবে"),
    "performance_manage": ("Performance: Create", "Performance review তৈরি করবে"),
    "face_reset": ("Face AI: Reset", "Employee face profile reset করবে"),
    "approvals_view": ("Approvals: View", "Pending registration দেখবে"),
    "approvals_manage": ("Approvals: Approve/Reject", "Registration approve/reject করবে"),
    "reports_view": ("Reports: View", "Attendance report দেখবে"),
    "reports_export": ("Reports: Export", "CSV/PDF/Excel export করবে"),
    "payroll_view": ("Payroll: View", "Private salary records দেখবে"),
    "payroll_manage": ("Payroll: Manage", "Salary, overtime, bonus ও payment status পরিবর্তন করবে"),
    "payroll_export": ("Payroll: Export", "Private payslip ও monthly Excel/PDF export করবে"),
    "duty_view": ("Duty: View", "Employee duty roster ও reminder status দেখবে"),
    "duty_manage": ("Duty: Manage", "Duty schedule তৈরি ও পরিবর্তন করবে"),
    "leave_view": ("Leave: View", "Leave এবং correction request দেখবে"),
    "leave_manage": ("Leave: Approve/Reject", "Leave ও correction approve/reject করবে"),
    "attendance_edit": ("Attendance: Correct", "Attendance correction request করবে"),
    "shift_manage": ("Shift Management", "Shift manage করবে"),
    "department_manage": ("Department Management", "Department manage করবে"),
    "audit_view": ("Audit Log: View", "Activity log দেখবে"),
    "settings_view": ("Settings: View", "সাধারণ settings page দেখবে"),
    "whatsapp_settings": ("WhatsApp Settings", "Token, Phone ID ও Webhook দেখবে/পরিবর্তন করবে"),
    "user_accounts_view": ("User Accounts: View", "Admin/HR account list দেখবে"),
    "user_accounts_manage": ("User Accounts: Manage", "Admin/HR account create, disable বা delete করবে"),
}
DEFAULT_ROLE_PERMISSIONS = {
    "admin": {"dashboard_view","employees_view","employees_add","employees_edit","performance_view","performance_manage","face_reset","approvals_view","approvals_manage","reports_view","reports_export","payroll_view","payroll_manage","payroll_export","duty_view","duty_manage","leave_view","leave_manage","attendance_edit","shift_manage","department_manage","audit_view"},
    "hr_manager": {"dashboard_view","employees_view","employees_add","employees_edit","performance_view","performance_manage","face_reset","approvals_view","approvals_manage","reports_view","reports_export","payroll_view","payroll_manage","payroll_export","duty_view","duty_manage","leave_view","leave_manage","attendance_edit","shift_manage","department_manage","audit_view"},
    "hr_executive": {"dashboard_view","employees_view","employees_add","employees_edit","performance_view","performance_manage","approvals_view","approvals_manage","reports_view","reports_export","leave_view","leave_manage","attendance_edit"},
    "hr_officer": {"dashboard_view","employees_view","performance_view","reports_view","leave_view"},
    "viewer": {"dashboard_view","reports_view"},
}

def logged_in(request: Request): return bool(request.session.get("admin") or request.session.get("hr_id"))
def require_login(request: Request):
    if not logged_in(request): raise HTTPException(401, "Login required")

def current_permissions(request: Request):
    require_login(request)
    cached = getattr(request.state, "permission_cache", None)
    if cached is not None:
        return cached
    if request.session.get("role") == "super_admin" and request.session.get("admin"):
        allowed = set(PERMISSION_CATALOG) | {"*"}
        request.state.permission_cache = allowed
        return allowed
    account_id = request.session.get("hr_id")
    if not account_id:
        return set()
    with get_db() as c:
        rows = c.execute("SELECT permission FROM account_permissions WHERE account_id=?", (account_id,)).fetchall()
        raw = {r["permission"] for r in rows}
        if "__configured__" in raw:
            allowed = {p for p in raw if p in PERMISSION_CATALOG}
            request.state.permission_cache = allowed
            return allowed
        explicit = {p for p in raw if p in PERMISSION_CATALOG}
        if explicit:
            request.state.permission_cache = explicit
            return explicit
        role = request.session.get("role", "viewer")
        allowed = set(DEFAULT_ROLE_PERMISSIONS.get(role, set()))
        request.state.permission_cache = allowed
        return allowed

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
    issues = settings.production_issues()
    if issues:
        raise RuntimeError("Production configuration invalid: " + "; ".join(issues))
    init_db()
    import_environment_defaults()
    if not get_setting("admin_email"):
        set_setting("admin_email", os.getenv("SUPER_ADMIN_EMAIL", "admin@buraq.com").strip().lower())
    if not get_setting("admin_name"):
        set_setting("admin_name", os.getenv("SUPER_ADMIN_NAME", "Super Admin").strip())
    imported = import_employees()
    logger.info("BURAQ v9.9 started database=%s employees_synced=%s", database_kind(), imported)

@app.on_event("startup")
async def start_reminders():
    app.state.reminder_task=asyncio.create_task(reminder_worker())

@app.on_event("shutdown")
async def stop_reminders():
    task=getattr(app.state,"reminder_task",None)
    if task:
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass

@app.get("/health")
def health():
    return {"status": "ok", "service": settings.app_name, "version": "9.10.0"}


@app.get("/ready")
def ready():
    db_ok = database_ok()
    configured_ok = configured()
    payload = {
        "status": "ready" if db_ok else "not_ready",
        "database": database_kind(),
        "database_ok": db_ok,
        "whatsapp_configured": configured_ok,
        "version": "9.10.0",
    }
    return JSONResponse(payload, status_code=200 if db_ok else 503)

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
    now = datetime.now(ZoneInfo(settings.timezone))
    today = now.date().isoformat()
    week_days = [(now.date() - timedelta(days=i)) for i in range(6, -1, -1)]
    with get_db() as c:
        workforce = c.execute("SELECT COUNT(*) employees,SUM(CASE WHEN registration_status='approved' THEN 1 ELSE 0 END) registered FROM employees").fetchone()
        employees = workforce["employees"]; registered = int(workforce["registered"] or 0)
        pending_registration = c.execute("SELECT COUNT(*) c FROM pending_registrations WHERE status='pending'").fetchone()["c"]
        daily = c.execute("""SELECT SUM(CASE WHEN check_in IS NOT NULL THEN 1 ELSE 0 END) present,
            SUM(CASE WHEN check_out IS NOT NULL THEN 1 ELSE 0 END) checked_out,
            SUM(CASE WHEN late_minutes>0 THEN 1 ELSE 0 END) late,
            COALESCE(SUM(overtime_minutes),0) overtime FROM attendance WHERE work_date=?""",(today,)).fetchone()
        present=int(daily["present"] or 0); checked_out=int(daily["checked_out"] or 0); late=int(daily["late"] or 0); overtime=int(daily["overtime"] or 0)
        on_leave = c.execute("SELECT COUNT(DISTINCT employee_id) c FROM leave_requests WHERE status='approved' AND start_date<=? AND end_date>=?", (today,today)).fetchone()["c"]
        pending_leave = c.execute("SELECT COUNT(*) c FROM leave_requests WHERE status='pending'").fetchone()["c"]
        pending_correction = c.execute("SELECT COUNT(*) c FROM attendance_corrections WHERE status='pending'").fetchone()["c"]
        recent = c.execute("SELECT a.work_date,a.check_in,a.check_out,a.late_minutes,a.overtime_minutes,e.staff_id,e.name,e.department FROM attendance a JOIN employees e ON e.id=a.employee_id ORDER BY COALESCE(a.check_out,a.check_in,a.created_at) DESC LIMIT 10").fetchall()
        week_counts=c.execute("SELECT work_date,COUNT(*) c FROM attendance WHERE work_date>=? AND work_date<=? AND check_in IS NOT NULL GROUP BY work_date",(week_days[0].isoformat(),week_days[-1].isoformat())).fetchall()
        by_day={r['work_date']:r['c'] for r in week_counts}; weekly=[(day,by_day.get(day.isoformat(),0)) for day in week_days]
    absent = max(employees - present - on_leave, 0)
    attendance_rate = round((present / employees * 100), 1) if employees else 0
    cfg, db = configured(), database_ok()
    warning = database_warning()
    can_whatsapp = has_permission(request, "whatsapp_settings")
    can_export = has_permission(request, "reports_export")
    can_approvals = has_permission(request, "approvals_view")
    can_operations = has_permission(request, "leave_view")
    max_week = max([v for _,v in weekly] + [1])
    chart = ''.join(f"<div class='bar-wrap'><div class='bar' style='height:{max(8, int(v/max_week*150))}px'><span class='bar-value'>{v}</span></div><div class='bar-label'>{d.strftime('%a')}</div></div>" for d,v in weekly)
    timeline = ''
    for r in recent:
        event_time = (r['check_out'] or r['check_in'] or '')
        event_label = 'Checked out' if r['check_out'] else 'Checked in'
        initials = ''.join(x[0] for x in str(r['name']).split()[:2]).upper() or 'E'
        badge = 'Late' if r['late_minutes'] else ('OT' if r['overtime_minutes'] else 'On time')
        timeline += f"<div class='timeline-item'><div class='avatar'>{escape(initials)}</div><div><b>{escape(r['name'])}</b><div class='sub'>{escape(r['staff_id'])} • {event_label} • {escape((event_time[11:16] if len(event_time)>15 else event_time) or '-')}</div></div><span class='pill'>{badge}</span></div>"
    if not timeline: timeline = "<div class='notice'>আজ এখনো কোনো attendance activity নেই।</div>"
    quick=[]
    if has_permission(request,'employees_view'): quick.append("<a class='quick-link' href='/employees'>👥 Employee Directory</a>")
    if can_approvals: quick.append(f"<a class='quick-link' href='/pending'>✅ Registration Approvals <span class='pill'>{pending_registration}</span></a>")
    if can_operations: quick.append(f"<a class='quick-link' href='/hr-operations'>🗂 HR Operations <span class='pill'>{pending_leave+pending_correction}</span></a>")
    if has_permission(request,'reports_view'): quick.append("<a class='quick-link' href='/reports'>📊 Open Reports</a>")
    if has_permission(request,'user_accounts_view'): quick.append("<a class='quick-link' href='/hr-accounts'>🔐 User & Permissions</a>")
    if has_permission(request,'settings_view'): quick.append("<a class='quick-link' href='/settings'>⚙️ System Settings</a>")
    system_note = f"<div class='notice' style='background:#fef3c7;color:#92400e'>{escape(warning)}</div>" if warning else ''
    body=f"""
    <section class='hero'><div><div class='eyebrow'>BURAQ Control Center</div><h2>Good {('morning' if now.hour<12 else 'afternoon' if now.hour<17 else 'evening')}, {escape(str(request.session.get('user_name','Admin')))}</h2><div class='sub'>Live workforce overview for {now.strftime('%A, %d %B %Y')}</div></div><div><span class='status {'ok' if db else 'bad'}'>{'All systems operational' if db else 'System attention required'}</span></div></section>
    <div class='grid'>
      <div class='card metric-card'><div class='card-head'><span class='metric-label'>Present Today</span><span class='kpi-icon'>✓</span></div><div class='metric'>{present}</div><div class='metric-foot'>{attendance_rate}% of workforce</div></div>
      <div class='card metric-card'><div class='card-head'><span class='metric-label'>Late Today</span><span class='kpi-icon'>◷</span></div><div class='metric'>{late}</div><div class='metric-foot'>Needs HR visibility</div></div>
      <div class='card metric-card'><div class='card-head'><span class='metric-label'>Absent</span><span class='kpi-icon'>!</span></div><div class='metric'>{absent}</div><div class='metric-foot'>{on_leave} employee(s) on leave</div></div>
      <div class='card metric-card'><div class='card-head'><span class='metric-label'>Overtime</span><span class='kpi-icon'>↗</span></div><div class='metric'>{overtime}m</div><div class='metric-foot'>{checked_out} check-outs completed</div></div>
    </div>
    <div class='section-gap'></div>
    <div class='two'>
      <div class='card'><div class='card-head'><div><h3>7-Day Attendance Trend</h3><div class='sub'>Daily checked-in employees</div></div>{"<a class='btn secondary' href='/reports'>View report</a>" if has_permission(request,'reports_view') else ''}</div><div class='chart'>{chart}</div></div>
      <div class='card'><div class='card-head'><div><h3>Workforce Readiness</h3><div class='sub'>Registration and attendance coverage</div></div><span class='pill'>{employees} employees</span></div><p class='sub'>WhatsApp registered</p><div class='progress'><span style='width:{(registered/employees*100 if employees else 0):.1f}%'></span></div><p><b>{registered}</b> of {employees}</p><p class='sub'>Today attendance</p><div class='progress'><span style='width:{attendance_rate}%'></span></div><p><b>{present}</b> checked in • <b>{checked_out}</b> checked out</p></div>
    </div>
    <div class='section-gap'></div>
    <div class='two'>
      <div class='card'><div class='card-head'><div><h3>Live Attendance Timeline</h3><div class='sub'>Most recent employee activity</div></div>{"<a class='btn secondary' href='/export/attendance.csv'>Export</a>" if can_export else ''}</div><div class='timeline'>{timeline}</div></div>
      <div style='display:grid;gap:16px'>
        <div class='card'><div class='card-head'><h3>Pending Work</h3><span class='pill'>{pending_registration+pending_leave+pending_correction}</span></div><div class='health-list'><div class='health-row'><span>Registration approvals</span><b>{pending_registration}</b></div><div class='health-row'><span>Leave requests</span><b>{pending_leave}</b></div><div class='health-row'><span>Attendance corrections</span><b>{pending_correction}</b></div></div></div>
        <div class='card'><h3>Quick Actions</h3><div class='quick-grid'>{''.join(quick)}</div></div>
      </div>
    </div>
    <div class='section-gap'></div>
    <div class='card'><div class='card-head'><div><h3>System Health</h3><div class='sub'>Production services and integrations</div></div><span class='status {'ok' if db and cfg else 'warn'}'>{'Healthy' if db and cfg else 'Attention'}</span></div>{system_note}<div class='grid'><div><span class='health-dot'></span> <b>Database</b><div class='sub'>{escape(database_kind())}</div></div><div><span class='health-dot' style='background:{'#15803d' if cfg else '#b45309'}'></span> <b>WhatsApp</b><div class='sub'>{'Connected' if cfg else 'Setup needed'}</div></div><div><span class='health-dot'></span> <b>Webhook</b><div class='sub'>Active</div></div><div><span class='health-dot'></span> <b>Face AI</b><div class='sub'>Ready</div></div></div></div>
    """
    return layout("Control Center", body, request, "dashboard")

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
def employees_page(request: Request, q: str = "", department: str = "", shift: str = "", status: str = ""):
    require_permission(request, "employees_view")
    clauses=["1=1"]; params=[]
    if q.strip():
        clauses.append("(LOWER(e.staff_id) LIKE ? OR LOWER(e.name) LIKE ? OR LOWER(COALESCE(e.phone,'')) LIKE ? OR LOWER(COALESCE(e.designation,'')) LIKE ?)")
        term=f"%{q.strip().lower()}%"; params += [term,term,term,term]
    if department: clauses.append("e.department=?"); params.append(department)
    if shift: clauses.append("e.shift=?"); params.append(shift)
    if status in {"active","inactive"}: clauses.append("e.is_active=?"); params.append(1 if status=="active" else 0)
    with get_db() as c:
        rows=c.execute("SELECT e.*,(SELECT COUNT(*) FROM face_samples f WHERE f.employee_id=e.id) face_count,(SELECT MAX(work_date) FROM attendance a WHERE a.employee_id=e.id) last_attendance FROM employees e WHERE "+" AND ".join(clauses)+" ORDER BY e.staff_id", tuple(params)).fetchall()
        deps=c.execute("SELECT DISTINCT department FROM employees WHERE COALESCE(department,'')<>'' ORDER BY department").fetchall()
    can_add=has_permission(request,"employees_add"); can_edit=has_permission(request,"employees_edit"); can_reset=has_permission(request,"face_reset")
    tr=[]
    for r in rows:
        initials=''.join(x[:1] for x in (r['name'] or '?').split()[:2]).upper()
        reg='ok' if r['registration_status']=='approved' else 'warn'; face='ok' if r['face_count']>=3 else 'bad'
        actions=f"<a class='btn secondary' href='/employees/{r['id']}'>Profile</a>"
        if has_permission(request,'duty_view'): actions += f"<a class='btn secondary' href='/employees/{r['id']}/duty'>Duty</a>"
        if can_reset: actions += f"<form method='post' action='/employees/{r['id']}/reset-face' style='display:inline'><button class='btn danger'>Reset Face</button></form>"
        tr.append(f"<tr><td><input class='checkbox' type='checkbox' name='employee_ids' value='{r['id']}'></td><td><div style='display:flex;gap:9px;align-items:center'><span class='avatar'>{escape(initials)}</span><span><b>{escape(r['name'])}</b><br><span class='sub'>{escape(r['staff_id'])}</span></span></div></td><td>{escape(r['designation'] or '—')}</td><td>{escape(r['department'] or '—')}</td><td>{escape(r['shift'])}</td><td><span class='status {reg}'>{escape(r['registration_status'])}</span><br><span class='status {face}' style='margin-top:5px'>{r['face_count']}/3 Face</span></td><td>{escape(r['last_attendance'] or 'Never')}</td><td><div class='table-actions'>{actions}</div></td></tr>")
    depopts=''.join(f"<option {'selected' if department==d['department'] else ''}>{escape(d['department'])}</option>" for d in deps)
    add=''
    if can_add:
        add="""<details class='card'><summary style='cursor:pointer;font-weight:850'>＋ Add Employee</summary><form method='post' style='margin-top:16px'><div class='two'><div><label>Staff ID</label><input name='staff_id' required><label>Name</label><input name='name' required><label>Phone</label><input name='phone'></div><div><label>Department</label><input name='department'><label>Designation</label><input name='designation'><label>Shift</label><select name='shift'><option value='morning'>Morning</option><option value='evening'>Evening</option></select></div></div><button class='btn'>Add Employee</button></form></details><div class='section-gap'></div>"""
    bulk=''
    if can_edit:
        bulk="""<div class='card' style='margin-bottom:15px'><b>Bulk Actions</b><div class='actions' style='margin-top:10px'><select name='bulk_action' style='width:auto;margin:0'><option value=''>Choose action</option><option value='activate'>Activate</option><option value='deactivate'>Deactivate</option><option value='shift'>Change shift</option><option value='department'>Change department</option></select><input name='bulk_value' placeholder='Shift/department value' style='width:220px;margin:0'><button class='btn'>Apply to selected</button></div></div>"""
    body=f"""<div class='hero'><div><div class='eyebrow'>Employee Center</div><h2>Workforce Directory</h2><div class='sub'>360° profiles, advanced search and bulk workforce operations.</div></div><span class='pill'>{len(rows)} results</span></div>{add}<div class='card' style='margin-bottom:15px'><form method='get' class='searchbar'><div><label>Global search</label><input name='q' value='{escape(q)}' placeholder='Name, Staff ID, phone or designation'></div><div><label>Department</label><select name='department'><option value=''>All</option>{depopts}</select></div><div><label>Shift</label><select name='shift'><option value=''>All</option><option {'selected' if shift=='morning' else ''} value='morning'>Morning</option><option {'selected' if shift=='evening' else ''} value='evening'>Evening</option></select></div><div><label>Status</label><select name='status'><option value=''>All</option><option {'selected' if status=='active' else ''} value='active'>Active</option><option {'selected' if status=='inactive' else ''} value='inactive'>Inactive</option></select></div><button class='btn'>Search</button></form></div><form method='post' action='/employees/bulk'>{bulk}<div class='card'><div style='overflow:auto'><table><thead><tr><th></th><th>Employee</th><th>Designation</th><th>Department</th><th>Shift</th><th>Readiness</th><th>Last attendance</th><th>Action</th></tr></thead><tbody>{''.join(tr) or '<tr><td colspan=8>No employees found</td></tr>'}</tbody></table></div></div></form>"""
    return layout("Employee Center", body, request, "employees")

@app.post("/employees")
def add_employee(request: Request, staff_id: str = Form(...), name: str = Form(...), phone: str = Form(""), department: str = Form(""), designation: str = Form(""), shift: str = Form("morning")):
    require_permission(request, "employees_add")
    try:
        with get_db() as c:
            c.execute("INSERT INTO employees(staff_id,name,phone,department,designation,shift) VALUES(?,?,?,?,?,?)", (staff_id.strip(),name.strip(),phone.strip() or None,department.strip() or None,designation.strip() or None,shift))
            audit(request,"employee_created","employee",staff_id,db=c)
    except Exception as exc: logger.warning("Employee add failed: %s",exc)
    return RedirectResponse("/employees",303)

@app.post("/employees/bulk")
async def employee_bulk(request: Request):
    require_permission(request,"employees_edit")
    form=await request.form(); ids=[int(x) for x in form.getlist('employee_ids') if str(x).isdigit()]
    action=str(form.get('bulk_action','')); value=str(form.get('bulk_value','')).strip()
    if not ids or action not in {'activate','deactivate','shift','department'}: return RedirectResponse('/employees',303)
    marks=','.join('?' for _ in ids)
    with get_db() as c:
        if action in {'activate','deactivate'}: c.execute(f"UPDATE employees SET is_active=?,updated_at=CURRENT_TIMESTAMP WHERE id IN ({marks})", tuple([1 if action=='activate' else 0]+ids))
        elif action=='shift' and value: c.execute(f"UPDATE employees SET shift=?,updated_at=CURRENT_TIMESTAMP WHERE id IN ({marks})", tuple([value]+ids))
        elif action=='department' and value: c.execute(f"UPDATE employees SET department=?,updated_at=CURRENT_TIMESTAMP WHERE id IN ({marks})", tuple([value]+ids))
        audit(request,"employee_bulk_update","employee",','.join(map(str,ids)),f"{action}:{value}",db=c)
    return RedirectResponse('/employees',303)

@app.get("/employees/{employee_id}", response_class=HTMLResponse)
def employee_profile(request: Request, employee_id: int, month: str = ""):
    require_permission(request,"employees_view")
    can_payroll_view=has_permission(request,"payroll_view")
    can_payroll_manage=has_permission(request,"payroll_manage")
    can_payroll_export=has_permission(request,"payroll_export")
    now=datetime.now(ZoneInfo(settings.timezone)); month=month or now.strftime('%Y-%m')
    try: first=datetime.strptime(month+'-01','%Y-%m-%d')
    except ValueError: first=datetime(now.year,now.month,1); month=first.strftime('%Y-%m')
    next_month=(first.replace(day=28)+timedelta(days=4)).replace(day=1); last=next_month-timedelta(days=1)
    with get_db() as c:
        e=c.execute("SELECT e.*,(SELECT COUNT(*) FROM face_samples WHERE employee_id=e.id) face_count FROM employees e WHERE e.id=?",(employee_id,)).fetchone()
        if not e: raise HTTPException(404,'Employee not found')
        attendance=c.execute("SELECT * FROM attendance WHERE employee_id=? AND work_date>=? AND work_date<=? ORDER BY work_date",(employee_id,first.strftime('%Y-%m-%d'),last.strftime('%Y-%m-%d'))).fetchall()
        leaves=c.execute("SELECT * FROM leave_requests WHERE employee_id=? AND status='approved' AND start_date<=? AND end_date>=?",(employee_id,last.strftime('%Y-%m-%d'),first.strftime('%Y-%m-%d'))).fetchall()
        notes=c.execute("SELECT * FROM employee_notes WHERE employee_id=? ORDER BY id DESC LIMIT 50",(employee_id,)).fetchall()
        recent=c.execute("SELECT * FROM attendance WHERE employee_id=? ORDER BY work_date DESC LIMIT 20",(employee_id,)).fetchall()
        reviews=c.execute("SELECT * FROM performance_reviews WHERE employee_id=? ORDER BY reviewed_at DESC,id DESC LIMIT 20",(employee_id,)).fetchall()
        perf=c.execute("SELECT overall_rating,review_period,reviewed_by,reviewed_at FROM performance_reviews WHERE employee_id=? ORDER BY reviewed_at DESC,id DESC LIMIT 1",(employee_id,)).fetchone()
        month_stats=c.execute("SELECT COUNT(*) total,SUM(CASE WHEN status='present' THEN 1 ELSE 0 END) present,SUM(CASE WHEN late_minutes>0 THEN 1 ELSE 0 END) late,SUM(COALESCE(overtime_minutes,0)) overtime FROM attendance WHERE employee_id=? AND work_date>=? AND work_date<=?",(employee_id,first.strftime('%Y-%m-%d'),last.strftime('%Y-%m-%d'))).fetchone()
        payroll=c.execute("SELECT * FROM payroll_records WHERE employee_id=? ORDER BY salary_month DESC LIMIT 24",(employee_id,)).fetchall() if can_payroll_view else []
    amap={a['work_date']:a for a in attendance}; leave_dates=set()
    for l in leaves:
        d=datetime.strptime(l['start_date'],'%Y-%m-%d'); end=datetime.strptime(l['end_date'],'%Y-%m-%d')
        while d<=end: leave_dates.add(d.strftime('%Y-%m-%d')); d+=timedelta(days=1)
    cells=['<div class="sub"><b>'+x+'</b></div>' for x in ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']]
    cells += ['<div class="cal-day empty"></div>']*first.weekday()
    today=now.strftime('%Y-%m-%d')
    for day in range(1,last.day+1):
        ds=f'{month}-{day:02d}'; a=amap.get(ds); cls=''; detail=''
        if ds in leave_dates: cls='leave'; detail='Leave'
        elif a: cls='late' if (a['late_minutes'] or 0)>0 else 'present'; detail=(a['check_in'] or '')[-5:]
        elif ds<today and datetime.strptime(ds,'%Y-%m-%d').weekday()<5: cls='absent'; detail='Absent'
        cells.append(f"<div class='cal-day {cls}'><b>{day}</b><div class='sub' style='margin-top:9px'>{escape(detail)}</div></div>")
    timeline=''.join(f"<div class='timeline-item'><span class='avatar'>{escape(str(a['work_date'])[-2:])}</span><div><b>{escape(a['work_date'])}</b><div class='sub'>In {escape((a['check_in'] or '—')[-8:-3])} • Out {escape((a['check_out'] or '—')[-8:-3])}</div></div><span class='pill'>{a['late_minutes'] or 0}m late • {a['overtime_minutes'] or 0}m OT</span></div>" for a in recent) or '<div class="sub">No attendance history</div>'
    notehtml=''.join(f"<div style='padding:12px 0;border-bottom:1px solid var(--line)'><span class='tag'>{escape(n['note_type'])}</span> <b>{escape(n['created_by'] or 'HR')}</b><div style='margin-top:6px'>{escape(n['note'])}</div><div class='sub'>{escape(str(n['created_at']))}</div></div>" for n in notes) or '<div class="sub">No HR notes</div>'
    edit=''
    if has_permission(request,'employees_edit'):
        edit=f"""<div class='card'><h3>Edit profile</h3><form method='post' action='/employees/{employee_id}/profile'><div class='two'><div><label>Name</label><input name='name' value='{escape(e['name'])}'><label>Designation</label><input name='designation' value='{escape(e['designation'] or '')}'><label>Department</label><input name='department' value='{escape(e['department'] or '')}'><label>Shift</label><select name='shift'><option {'selected' if e['shift']=='morning' else ''} value='morning'>Morning</option><option {'selected' if e['shift']=='evening' else ''} value='evening'>Evening</option></select></div><div><label>Reporting manager</label><input name='reporting_manager' value='{escape(e['reporting_manager'] or '')}'><label>Office</label><input name='office_name' value='{escape(e['office_name'] or '')}'><label>Join date</label><input type='date' name='join_date' value='{escape(e['join_date'] or '')}'><label>Phone</label><input name='phone' value='{escape(e['phone'] or '')}'></div></div><h3>Emergency contact</h3><div class='grid'><input name='emergency_name' placeholder='Name' value='{escape(e['emergency_name'] or '')}'><input name='emergency_relation' placeholder='Relation' value='{escape(e['emergency_relation'] or '')}'><input name='emergency_phone' placeholder='Phone' value='{escape(e['emergency_phone'] or '')}'><select name='is_active'><option value='1' {'selected' if e['is_active'] else ''}>Active</option><option value='0' {'selected' if not e['is_active'] else ''}>Inactive</option></select></div><button class='btn'>Save profile</button></form></div>"""
    noteform=f"<form method='post' action='/employees/{employee_id}/notes'><div class='two'><select name='note_type'><option>general</option><option>performance</option><option>warning</option><option>promotion</option></select><input name='note' required placeholder='Write a private HR note'></div><button class='btn'>Add note</button></form>" if has_permission(request,'employees_edit') else ''
    reviewhtml=''.join(f"<div style='padding:14px 0;border-bottom:1px solid var(--line)'><div class='card-head'><b>{escape(r['review_period'])}</b><span class='pill'>{(float(r['overall_rating']) if r['overall_rating'] is not None else 0):.1f}/5</span></div><div class='sub'>Reviewed by {escape(r['reviewed_by'] or 'HR')} • {escape(str(r['reviewed_at']))}</div><div style='margin-top:8px'>{escape(r['comments'] or 'No comments')}</div>{f"<div class='sub' style='margin-top:6px'>Goals: {escape(r['goals'])}</div>" if r['goals'] else ''}</div>" for r in reviews) or '<div class="sub">No performance reviews yet</div>'
    reviewform=''
    if has_permission(request,'performance_manage'):
        reviewform=f"""<form method='post' action='/employees/{employee_id}/performance'><div class='two'><div><label>Review period</label><input type='month' name='review_period' value='{now.strftime('%Y-%m')}' required><label>Attendance</label><select name='attendance_rating'>{''.join(f'<option>{x}</option>' for x in range(1,6))}</select><label>Discipline</label><select name='discipline_rating'>{''.join(f'<option>{x}</option>' for x in range(1,6))}</select><label>Work quality</label><select name='work_quality_rating'>{''.join(f'<option>{x}</option>' for x in range(1,6))}</select></div><div><label>Teamwork</label><select name='teamwork_rating'>{''.join(f'<option>{x}</option>' for x in range(1,6))}</select><label>Communication</label><select name='communication_rating'>{''.join(f'<option>{x}</option>' for x in range(1,6))}</select><label>Responsibility</label><select name='responsibility_rating'>{''.join(f'<option>{x}</option>' for x in range(1,6))}</select><label>Comments</label><textarea name='comments' rows='3'></textarea><label>Goals</label><textarea name='goals' rows='2'></textarea></div></div><button class='btn'>Save Review</button></form>"""
    latest_rating=float(perf['overall_rating']) if perf else 0
    attendance_total=int(month_stats['total'] or 0); attendance_present=int(month_stats['present'] or 0); attendance_rate=round((attendance_present/attendance_total*100),1) if attendance_total else 0
    initials=''.join(x[:1] for x in e['name'].split()[:2]).upper()
    payroll_section=''; payroll_tab=''
    if can_payroll_view:
        payroll_tab="<a class='tab' href='#payroll'>Payroll</a>"
        current_payroll=next((p for p in payroll if p['salary_month']==month),None)
        fixed=float(current_payroll['fixed_salary']) if current_payroll else 0; hours=float(current_payroll['overtime_hours']) if current_payroll else 0; rate=float(current_payroll['overtime_rate']) if current_payroll else 0; bonus=float(current_payroll['bonus']) if current_payroll else 0; deduction=float(current_payroll['deduction']) if current_payroll else 0
        payroll_form=''
        if can_payroll_manage:
            payroll_form=f"""<div class='card'><div class='card-head'><div><h3>{'Update' if current_payroll else 'Create'} Salary</h3><div class='sub'>HR/Admin input for {escape(month)}</div></div><span class='tag'>Private</span></div><form method='post' action='/payroll'><input type='hidden' name='employee_id' value='{employee_id}'><input type='hidden' name='profile_employee_id' value='{employee_id}'><div class='two'><div><label>Salary Month</label><input type='month' name='salary_month' value='{escape(month)}' required></div><div><label>Fixed Salary</label><input type='number' min='0' step='0.01' name='fixed_salary' value='{fixed:.2f}' required></div></div><div class='two'><div><label>Overtime Hours</label><input type='number' min='0' step='0.01' name='overtime_hours' value='{hours:.2f}'></div><div><label>Rate Per Hour</label><input type='number' min='0' step='0.01' name='overtime_rate' value='{rate:.2f}'></div></div><div class='two'><div><label>Bonus</label><input type='number' min='0' step='0.01' name='bonus' value='{bonus:.2f}'></div><div><label>Deduction</label><input type='number' min='0' step='0.01' name='deduction' value='{deduction:.2f}'></div></div><label>Private Note</label><textarea name='note'>{escape(current_payroll['note'] or '') if current_payroll else ''}</textarea><button class='btn'>Calculate & Save Salary</button></form></div>"""
        history=[]
        for p in payroll:
            actions=f"<a class='btn secondary' href='/payroll/{p['id']}/payslip.pdf'>PDF</a>" if can_payroll_export else ''
            if can_payroll_manage:
                new_status='unpaid' if p['payment_status']=='paid' else 'paid'; actions+=f" <form method='post' action='/payroll/{p['id']}/status' style='display:inline'><input type='hidden' name='month' value='{escape(month)}'><input type='hidden' name='return_employee_id' value='{employee_id}'><input type='hidden' name='status' value='{new_status}'><button class='btn {'secondary' if new_status=='unpaid' else ''}'>{'Undo Paid' if new_status=='unpaid' else 'Mark Paid'}</button></form>"
            history.append(f"<tr><td><b>{escape(p['salary_month'])}</b></td><td>{_money(p['fixed_salary'])}</td><td>{_money(p['overtime_amount'])}</td><td>{_money(p['bonus'])}</td><td>{_money(p['deduction'])}</td><td><b>{_money(p['net_salary'])}</b></td><td><span class='status {'ok' if p['payment_status']=='paid' else 'warn'}'>{escape(p['payment_status'])}</span></td><td>{actions}</td></tr>")
        if current_payroll:
            net=float(current_payroll['net_salary']); summary=f"""<div class='card payroll-panel'><div class='card-head'><div><span class='confidential'>🔒 HR CONFIDENTIAL</span><h2 style='margin-top:12px'>{escape(month)} Payroll</h2></div><span class='status {'ok' if current_payroll['payment_status']=='paid' else 'warn'}'>{escape(current_payroll['payment_status'])}</span></div><div class='payroll-amount'>৳{_money(net)}</div><div class='sub'>Net salary</div><div class='salary-breakdown' style='margin-top:18px'><div><div class='sub'>Fixed</div><b>৳{_money(current_payroll['fixed_salary'])}</b></div><div><div class='sub'>Overtime</div><b>৳{_money(current_payroll['overtime_amount'])}</b></div><div><div class='sub'>Bonus</div><b>৳{_money(current_payroll['bonus'])}</b></div><div><div class='sub'>Deduction</div><b>৳{_money(current_payroll['deduction'])}</b></div></div></div>"""
        else: summary=f"<div class='card payroll-panel'><span class='confidential'>🔒 HR CONFIDENTIAL</span><h2 style='margin-top:14px'>No salary for {escape(month)}</h2><div class='sub'>Create this employee's monthly salary using the form.</div></div>"
        payroll_section=f"""<div id='payroll' class='section-gap'></div><div class='hero'><div><div class='eyebrow'>Confidential Compensation</div><h2>Employee Payroll</h2><div class='sub'>Only authorized HR/Admin can view or change this information.</div></div><a class='btn secondary' href='/payroll?month={escape(month)}'>Monthly Payroll</a></div><div class='two'>{summary}{payroll_form}</div><div class='section-gap'></div><div class='card' style='overflow:auto'><div class='card-head'><div><h3>Salary History</h3><div class='sub'>Latest 24 months</div></div></div><table><thead><tr><th>Month</th><th>Fixed</th><th>OT</th><th>Bonus</th><th>Deduction</th><th>Net</th><th>Status</th><th>Action</th></tr></thead><tbody>{''.join(history) or '<tr><td colspan=8>No salary history.</td></tr>'}</tbody></table></div>"""
    body=f"""<div class='card profile-hero'><div class='profile-photo'>{escape(initials)}</div><div><div class='eyebrow'>Employee 360°</div><h2>{escape(e['name'])}</h2><div class='sub'>{escape(e['staff_id'])} • {escape(e['designation'] or 'No designation')} • {escape(e['department'] or 'No department')}</div><div class='actions' style='margin-top:10px'><span class='status {'ok' if e['is_active'] else 'bad'}'>{'Active' if e['is_active'] else 'Inactive'}</span><span class='status {'ok' if e['registration_status']=='approved' else 'warn'}'>WhatsApp {escape(e['registration_status'])}</span><span class='status {'ok' if e['face_count']>=3 else 'warn'}'>Face {e['face_count']}/3</span></div></div><a class='btn secondary' href='/employees'>Back</a></div><div class='section-gap'></div><div class='summary-strip'><div class='card'><div class='sub'>Attendance</div><div class='metric'>{attendance_rate}%</div></div><div class='card'><div class='sub'>Present</div><div class='metric'>{attendance_present}</div></div><div class='card'><div class='sub'>Late</div><div class='metric'>{int(month_stats['late'] or 0)}</div></div><div class='card'><div class='sub'>Overtime</div><div class='metric'>{round(int(month_stats['overtime'] or 0)/60,1)}h</div></div><div class='card'><div class='sub'>Performance</div><div class='rating'>{latest_rating:.1f}/5</div><div class='stars'>{'★'*round(latest_rating)}{'☆'*(5-round(latest_rating))}</div></div></div><div class='tabs'><a class='tab' href='#profile'>Profile</a><a class='tab' href='#attendance'>Attendance</a><a class='tab' href='#leave'>Leave</a><a class='tab' href='#performance'>Performance</a>{payroll_tab}<a class='tab' href='#activity'>Activity</a></div><div id='profile' class='facts'><div class='fact'><span class='sub'>Shift</span><b>{escape(e['shift'])}</b></div><div class='fact'><span class='sub'>Manager</span><b>{escape(e['reporting_manager'] or '—')}</b></div><div class='fact'><span class='sub'>Office</span><b>{escape(e['office_name'] or 'BURAQ Office')}</b></div><div class='fact'><span class='sub'>Join date</span><b>{escape(e['join_date'] or '—')}</b></div><div class='fact'><span class='sub'>WhatsApp</span><b>{escape(e['whatsapp_phone'] or 'Not registered')}</b></div><div class='fact'><span class='sub'>Emergency</span><b>{escape(e['emergency_name'] or '—')} {escape(e['emergency_phone'] or '')}</b></div></div><div class='section-gap'></div><div class='two'><div class='card'><div class='card-head'><div><h3>Attendance Calendar</h3><div class='sub'>{escape(month)}</div></div><form method='get'><input type='month' name='month' value='{escape(month)}' style='margin:0' onchange='this.form.submit()'></form></div><div class='calendar'>{''.join(cells)}</div></div><div class='card'><h3>Recent timeline</h3><div class='timeline'>{timeline}</div></div></div><div class='section-gap'></div><div id='performance' class='card'><div class='card-head'><div><h3>Performance Review</h3><div class='sub'>Simple 1–5 ratings with comments and goals</div></div><span class='pill'>{latest_rating:.1f}/5 latest</span></div>{reviewform}<div class='section-gap'></div>{reviewhtml}</div>{payroll_section}<div class='section-gap'></div><div class='two'>{edit}<div id='activity' class='card'><h3>HR Notes</h3>{noteform}<div class='section-gap'></div>{notehtml}</div></div>"""
    return layout(e['name'],body,request,'employees')

@app.get("/employees/{employee_id}/duty", response_class=HTMLResponse)
def employee_duty_page(request: Request, employee_id: int, saved: str=""):
    require_permission(request,'duty_view'); can_manage=has_permission(request,'duty_manage')
    today=datetime.now(ZoneInfo(settings.timezone)).date().isoformat(); days=['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']
    with get_db() as c:
        e=c.execute("SELECT * FROM employees WHERE id=?",(employee_id,)).fetchone()
        weekly=c.execute("SELECT * FROM duty_schedules WHERE employee_id=? ORDER BY weekday",(employee_id,)).fetchall()
        custom=c.execute("SELECT * FROM custom_duties WHERE employee_id=? AND duty_date>=? ORDER BY duty_date",(employee_id,today)).fetchall()
    if not e: raise HTTPException(404,'Employee not found')
    forms=''
    if can_manage:
        day_options=''.join(f"<option value='{i}'>{d}</option>" for i,d in enumerate(days))
        forms=f"""<div class='two'><div class='card'><div class='eyebrow'>Repeating Schedule</div><h2>Regular Duty by Shift</h2><form method='post' action='/employees/{employee_id}/duty/regular'><label>Weekday</label><select name='weekday'>{day_options}</select><label>Shift preset</label><select name='preset'><option value='morning'>Morning (08:00-16:00)</option><option value='evening'>Evening (16:00-22:00)</option><option value='night'>Night (22:00-06:00)</option><option value='custom'>Custom selectable time</option></select><div class='two'><div><label>Custom start (optional)</label><input type='time' name='start_time'></div><div><label>Custom end (optional)</label><input type='time' name='end_time'></div></div><label>Office</label><input name='office_name' value='{escape(e['office_name'] or 'BURAQ Office')}'><button class='btn'>Assign Regular Duty</button></form></div><div class='card money-card'><div class='eyebrow'>One Specific Date</div><h2>Custom Duty</h2><form method='post' action='/employees/{employee_id}/duty/custom'><label>Date</label><input type='date' name='duty_date' required><div class='two'><div><label>Start</label><input type='time' name='start_time' required></div><div><label>End</label><input type='time' name='end_time' required></div></div><label>Office</label><input name='office_name' value='{escape(e['office_name'] or 'BURAQ Office')}'><label>Note</label><input name='note' placeholder='Special duty reason'><button class='btn'>Assign Custom Duty</button></form></div></div><div class='section-gap'></div><div class='two'><div class='card'><div class='eyebrow'>Quick Assignment</div><h2>Assign Friday Duty</h2><form method='post' action='/employees/{employee_id}/duty/friday'><div class='two'><div><label>Start</label><input type='time' name='start_time' required></div><div><label>End</label><input type='time' name='end_time' required></div></div><label>Office</label><input name='office_name' value='{escape(e['office_name'] or 'BURAQ Office')}'><button class='btn'>Assign Every Friday</button></form></div><div class='card payroll-panel'><div class='eyebrow' style='color:#8ff0cb'>Overnight Assignment</div><h2>Assign Night Duty</h2><form method='post' action='/employees/{employee_id}/duty/night'><label>Starting date</label><input type='date' name='duty_date' required><div class='two'><div><label>Night start</label><input type='time' name='start_time' value='22:00' required></div><div><label>Next-day end</label><input type='time' name='end_time' value='06:00' required></div></div><label>Repeat</label><select name='repeat'><option value='once'>One-time night duty</option><option value='weekly'>Repeat every week on this weekday</option></select><label>Office</label><input name='office_name' value='{escape(e['office_name'] or 'BURAQ Office')}'><button class='btn'>Assign Night Duty</button></form></div></div>"""
    weekly_rows=''.join(f"<tr><td>{days[int(r['weekday'])]}</td><td>{escape(r['start_time'])} - {escape(r['end_time'])}{' (+1 day)' if r['end_time']<=r['start_time'] else ''}</td><td>{escape(r['office_name'] or 'BURAQ Office')}</td><td>{f'''<form method='post' action='/employees/{employee_id}/duty/weekly/{r['id']}/delete'><button class='btn danger'>Delete</button></form>''' if can_manage else ''}</td></tr>" for r in weekly) or '<tr><td colspan=4>No regular duty.</td></tr>'
    custom_rows=''.join(f"<tr><td>{escape(r['duty_date'])}</td><td>{escape(r['start_time'])} - {escape(r['end_time'])}{' (+1 day)' if r['end_time']<=r['start_time'] else ''}</td><td>{escape(r['office_name'] or 'BURAQ Office')}<div class='sub'>{escape(r['note'] or '')}</div></td><td>{f'''<form method='post' action='/employees/{employee_id}/duty/custom/{r['id']}/delete'><button class='btn danger'>Delete</button></form>''' if can_manage else ''}</td></tr>" for r in custom) or '<tr><td colspan=4>No upcoming custom duty.</td></tr>'
    notice="<div class='notice'>Duty assignment saved.</div>" if saved else ''
    body=f"""{notice}<div class='card profile-hero'><div class='profile-photo'>{escape(''.join(x[:1] for x in e['name'].split()[:2]).upper())}</div><div><div class='eyebrow'>Employee Duty Control</div><h2>{escape(e['name'])}</h2><div class='sub'>{escape(e['staff_id'])} • Current shift: {escape(e['shift'])}</div></div><div class='actions'><a class='btn secondary' href='/employees/{employee_id}'>Profile</a><a class='btn secondary' href='/employees'>Employees</a></div></div><div class='section-gap'></div>{forms}<div class='section-gap'></div><div class='two'><div class='card' style='overflow:auto'><h2>Regular Weekly Duty</h2><table><thead><tr><th>Day</th><th>Time</th><th>Office</th><th></th></tr></thead><tbody>{weekly_rows}</tbody></table></div><div class='card' style='overflow:auto'><h2>Upcoming Custom Duty</h2><table><thead><tr><th>Date</th><th>Time</th><th>Office</th><th></th></tr></thead><tbody>{custom_rows}</tbody></table></div></div>"""
    return layout(f"{e['name']} Duty",body,request,'employees')

def _duty_times(preset: str, start_time: str, end_time: str):
    presets={'morning':('08:00','16:00'),'evening':('16:00','22:00'),'night':('22:00','06:00')}
    if start_time and end_time: return start_time,end_time
    if preset in presets: return presets[preset]
    raise HTTPException(400,'Select start and end time')

@app.post("/employees/{employee_id}/duty/regular")
def assign_regular_duty(request: Request, employee_id: int, weekday: int=Form(...), preset: str=Form('morning'), start_time: str=Form(''), end_time: str=Form(''), office_name: str=Form('BURAQ Office')):
    require_permission(request,'duty_manage'); start_time,end_time=_duty_times(preset,start_time,end_time)
    if weekday not in range(7): raise HTTPException(400,'Invalid weekday')
    with get_db() as c: c.execute("INSERT INTO duty_schedules(employee_id,weekday,start_time,end_time,office_name,created_by) VALUES(?,?,?,?,?,?) ON CONFLICT(employee_id,weekday) DO UPDATE SET start_time=excluded.start_time,end_time=excluded.end_time,office_name=excluded.office_name,is_active=excluded.is_active,updated_at=CURRENT_TIMESTAMP",(employee_id,weekday,start_time,end_time,office_name.strip() or 'BURAQ Office',str(request.session.get('hr_id') or 'super_admin')))
    return RedirectResponse(f'/employees/{employee_id}/duty?saved=1',303)

@app.post("/employees/{employee_id}/duty/custom")
def assign_employee_custom_duty(request: Request, employee_id: int, duty_date: str=Form(...), start_time: str=Form(...), end_time: str=Form(...), office_name: str=Form('BURAQ Office'), note: str=Form('')):
    require_permission(request,'duty_manage')
    try: datetime.strptime(duty_date,'%Y-%m-%d')
    except ValueError: raise HTTPException(400,'Invalid date')
    with get_db() as c: c.execute("INSERT INTO custom_duties(employee_id,duty_date,start_time,end_time,office_name,note,created_by) VALUES(?,?,?,?,?,?,?) ON CONFLICT(employee_id,duty_date) DO UPDATE SET start_time=excluded.start_time,end_time=excluded.end_time,office_name=excluded.office_name,note=excluded.note,is_active=excluded.is_active,updated_at=CURRENT_TIMESTAMP",(employee_id,duty_date,start_time,end_time,office_name.strip() or 'BURAQ Office',note.strip() or None,str(request.session.get('hr_id') or 'super_admin')))
    return RedirectResponse(f'/employees/{employee_id}/duty?saved=1',303)

@app.post("/employees/{employee_id}/duty/friday")
def assign_friday_duty(request: Request, employee_id: int, start_time: str=Form(...), end_time: str=Form(...), office_name: str=Form('BURAQ Office')):
    return assign_regular_duty(request,employee_id,4,'custom',start_time,end_time,office_name)

@app.post("/employees/{employee_id}/duty/night")
def assign_night_duty(request: Request, employee_id: int, duty_date: str=Form(...), start_time: str=Form(...), end_time: str=Form(...), repeat: str=Form('once'), office_name: str=Form('BURAQ Office')):
    require_permission(request,'duty_manage')
    try: day=datetime.strptime(duty_date,'%Y-%m-%d')
    except ValueError: raise HTTPException(400,'Invalid date')
    if repeat=='weekly': return assign_regular_duty(request,employee_id,day.weekday(),'custom',start_time,end_time,office_name)
    return assign_employee_custom_duty(request,employee_id,duty_date,start_time,end_time,office_name,'Night duty')

@app.post("/employees/{employee_id}/duty/weekly/{schedule_id}/delete")
def delete_employee_weekly_duty(request: Request, employee_id: int, schedule_id: int):
    require_permission(request,'duty_manage')
    with get_db() as c: c.execute("DELETE FROM duty_schedules WHERE id=? AND employee_id=?",(schedule_id,employee_id))
    return RedirectResponse(f'/employees/{employee_id}/duty',303)

@app.post("/employees/{employee_id}/duty/custom/{duty_id}/delete")
def delete_employee_custom_duty(request: Request, employee_id: int, duty_id: int):
    require_permission(request,'duty_manage')
    with get_db() as c: c.execute("DELETE FROM custom_duties WHERE id=? AND employee_id=?",(duty_id,employee_id))
    return RedirectResponse(f'/employees/{employee_id}/duty',303)


@app.post("/employees/{employee_id}/performance")
def add_performance_review(request: Request, employee_id: int, review_period: str=Form(...), attendance_rating: int=Form(...), discipline_rating: int=Form(...), work_quality_rating: int=Form(...), teamwork_rating: int=Form(...), communication_rating: int=Form(...), responsibility_rating: int=Form(...), comments: str=Form(""), goals: str=Form("")):
    require_permission(request,"performance_manage")
    ratings=[attendance_rating,discipline_rating,work_quality_rating,teamwork_rating,communication_rating,responsibility_rating]
    if any(x<1 or x>5 for x in ratings): raise HTTPException(400,"Ratings must be 1 to 5")
    overall=round(sum(ratings)/len(ratings),2)
    with get_db() as c:
        c.execute("INSERT INTO performance_reviews(employee_id,review_period,attendance_rating,discipline_rating,work_quality_rating,teamwork_rating,communication_rating,responsibility_rating,overall_rating,comments,goals,reviewed_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(employee_id,review_period,*ratings,overall,comments.strip() or None,goals.strip() or None,request.session.get('user_name','Admin')))
        audit(request,'performance_review_created','employee',str(employee_id),f'{review_period}: {overall}/5',db=c)
    return RedirectResponse(f'/employees/{employee_id}#performance',303)

@app.get("/performance", response_class=HTMLResponse)
def performance_page(request: Request):
    require_permission(request,"performance_view")
    with get_db() as c:
        rows=c.execute("SELECT e.id,e.staff_id,e.name,e.department,e.designation,p.overall_rating,p.review_period,p.reviewed_at FROM employees e LEFT JOIN performance_reviews p ON p.id=(SELECT p2.id FROM performance_reviews p2 WHERE p2.employee_id=e.id ORDER BY p2.reviewed_at DESC,p2.id DESC LIMIT 1) WHERE e.is_active ORDER BY e.name").fetchall()
    trs=''.join(f"<tr><td><b>{escape(r['name'])}</b><div class='sub'>{escape(r['staff_id'])}</div></td><td>{escape(r['department'] or '—')}</td><td>{escape(r['designation'] or '—')}</td><td>{(float(r['overall_rating']) if r['overall_rating'] is not None else 0):.1f}/5"+f"<div class='sub'>{escape(r['review_period'] or '')}</div>"+f"</td><td><a class='btn secondary' href='/employees/{r['id']}#performance'>{'Review' if r['overall_rating'] is None else 'Open'}</a></td></tr>" for r in rows) or "<tr><td colspan='5'>No employees</td></tr>"
    body=f"""<div class='hero'><div><div class='eyebrow'>People Development</div><h2>Performance Reviews</h2><div class='sub'>Simple, consistent employee reviews without unnecessary complexity.</div></div><span class='pill'>{len(rows)} employees</span></div><div class='card'><table><tr><th>Employee</th><th>Department</th><th>Designation</th><th>Latest rating</th><th></th></tr>{trs}</table></div>"""
    return layout('Performance',body,request,'performance')

@app.post("/employees/{employee_id}/profile")
def update_employee_profile(request: Request, employee_id: int, name: str=Form(...), phone: str=Form(""), designation: str=Form(""), department: str=Form(""), shift: str=Form("morning"), reporting_manager: str=Form(""), office_name: str=Form(""), join_date: str=Form(""), emergency_name: str=Form(""), emergency_relation: str=Form(""), emergency_phone: str=Form(""), is_active: int=Form(1)):
    require_permission(request,'employees_edit')
    with get_db() as c:
        c.execute("UPDATE employees SET name=?,phone=?,designation=?,department=?,shift=?,reporting_manager=?,office_name=?,join_date=?,emergency_name=?,emergency_relation=?,emergency_phone=?,is_active=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(name.strip(),phone.strip() or None,designation.strip() or None,department.strip() or None,shift,reporting_manager.strip() or None,office_name.strip() or None,join_date or None,emergency_name.strip() or None,emergency_relation.strip() or None,emergency_phone.strip() or None,1 if is_active else 0,employee_id))
        audit(request,'employee_profile_updated','employee',str(employee_id),db=c)
    return RedirectResponse(f'/employees/{employee_id}',303)

@app.post("/employees/{employee_id}/notes")
def add_employee_note(request: Request, employee_id: int, note_type: str=Form('general'), note: str=Form(...)):
    require_permission(request,'employees_edit')
    with get_db() as c:
        c.execute("INSERT INTO employee_notes(employee_id,note_type,note,created_by) VALUES(?,?,?,?)",(employee_id,note_type[:30],note.strip(),request.session.get('user_name','Admin')))
        audit(request,'employee_note_added','employee',str(employee_id),note_type,db=c)
    return RedirectResponse(f'/employees/{employee_id}',303)

@app.post("/employees/{employee_id}/reset-face")
def reset_employee_face(request: Request, employee_id: int):
    require_permission(request, "face_reset")
    with get_db() as c:
        employee=c.execute("SELECT whatsapp_phone,phone FROM employees WHERE id=?",(employee_id,)).fetchone()
        c.execute("DELETE FROM face_samples WHERE employee_id=?",(employee_id,)); c.execute("DELETE FROM face_profiles WHERE employee_id=?",(employee_id,))
        if employee:
            phone=employee["whatsapp_phone"] or employee["phone"]
            if phone: c.execute("INSERT INTO conversation_states(phone,state) VALUES(?,?) ON CONFLICT(phone) DO UPDATE SET state=excluded.state,updated_at=CURRENT_TIMESTAMP",(phone,"awaiting_face_registration"))
        audit(request,'face_reset','employee',str(employee_id),db=c)
    return RedirectResponse(f"/employees/{employee_id}",303)

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
    require_permission(request,"approvals_manage")
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


def _attendance_report_rows(start_date: str, end_date: str, status: str = "", department: str = ""):
    clauses = ["a.work_date>=?", "a.work_date<=?"]
    params = [start_date, end_date]
    if status:
        clauses.append("a.status=?"); params.append(status)
    if department:
        clauses.append("e.department=?"); params.append(department)
    sql = "SELECT a.*,e.staff_id,e.name,e.department,e.shift FROM attendance a JOIN employees e ON e.id=a.employee_id WHERE " + " AND ".join(clauses) + " ORDER BY a.work_date DESC,e.staff_id"
    with get_db() as c:
        return c.execute(sql, params).fetchall()

def _payroll_rows(month: str):
    with get_db() as c:
        return c.execute("""SELECT p.*,e.staff_id,e.name,e.department,e.designation
            FROM payroll_records p JOIN employees e ON e.id=p.employee_id
            WHERE p.salary_month=? ORDER BY e.staff_id""",(month,)).fetchall()

def _money(value):
    return f"{float(value or 0):,.2f}"

@app.get("/payroll", response_class=HTMLResponse)
def payroll_page(request: Request, month: str="", saved: str="", error: str=""):
    require_permission(request,"payroll_view")
    current=datetime.now(ZoneInfo(settings.timezone)).strftime("%Y-%m")
    month=month or current
    if not re.fullmatch(r"\d{4}-\d{2}",month): raise HTTPException(400,"Invalid salary month")
    rows=_payroll_rows(month); can_manage=has_permission(request,"payroll_manage"); can_export=has_permission(request,"payroll_export")
    with get_db() as c: employees=c.execute("SELECT id,staff_id,name FROM employees WHERE is_active ORDER BY staff_id").fetchall()
    employee_options=''.join(f"<option value='{e['id']}'>{escape(e['staff_id'])} - {escape(e['name'])}</option>" for e in employees)
    notices="<div class='notice'>Payroll saved successfully.</div>" if saved else ("<div class='notice' style='background:#fee2e2;color:#991b1b'>Payroll could not be saved.</div>" if error else "")
    form=""
    if can_manage:
        form=f"""<div class='card'><h2>Add or Update Salary</h2><p class='sub'>Saving the same employee and month updates the existing record.</p><form method='post' action='/payroll'><input type='hidden' name='return_month' value='{month}'><label>Employee</label><select name='employee_id' required>{employee_options}</select><label>Salary Month</label><input type='month' name='salary_month' value='{month}' required><div class='two'><div><label>Fixed Salary</label><input type='number' min='0' step='0.01' name='fixed_salary' required></div><div><label>Bonus</label><input type='number' min='0' step='0.01' name='bonus' value='0'></div></div><div class='two'><div><label>Overtime Hours</label><input type='number' min='0' step='0.01' name='overtime_hours' value='0'></div><div><label>Overtime Rate / Hour</label><input type='number' min='0' step='0.01' name='overtime_rate' value='0'></div></div><label>Deduction</label><input type='number' min='0' step='0.01' name='deduction' value='0'><label>Private HR Note</label><textarea name='note'></textarea><button class='btn'>Calculate & Save</button></form></div>"""
    table=[]
    for r in rows:
        controls=""
        if can_manage:
            next_status="paid" if r['payment_status']!='paid' else "unpaid"
            controls+=f"<form method='post' action='/payroll/{r['id']}/status' style='display:inline'><input type='hidden' name='month' value='{month}'><input type='hidden' name='status' value='{next_status}'><button class='btn {'secondary' if next_status=='unpaid' else ''}'>{'Mark Unpaid' if next_status=='unpaid' else 'Mark Paid'}</button></form> "
        if can_export: controls+=f"<a class='btn secondary' href='/payroll/{r['id']}/payslip.pdf'>Payslip</a>"
        state='ok' if r['payment_status']=='paid' else 'warn'
        table.append(f"<tr><td><b>{escape(r['staff_id'])}</b><br><span class='sub'>{escape(r['name'])}</span></td><td>{_money(r['fixed_salary'])}</td><td>{r['overtime_hours']:.2f} × {_money(r['overtime_rate'])}<br><b>{_money(r['overtime_amount'])}</b></td><td>{_money(r['bonus'])}</td><td>{_money(r['deduction'])}</td><td><b>{_money(r['net_salary'])}</b></td><td><span class='status {state}'>{escape(r['payment_status'])}</span></td><td>{controls}</td></tr>")
    gross=sum(float(r['net_salary']) for r in rows); paid=sum(float(r['net_salary']) for r in rows if r['payment_status']=='paid')
    export_buttons=f"<a class='btn secondary' href='/payroll/export.xlsx?month={month}'>Excel</a><a class='btn secondary' href='/payroll/export.pdf?month={month}'>PDF</a>" if can_export else ""
    body=f"""{notices}<div class='hero'><div><div class='eyebrow'>Private HR Module</div><h2>Salary & Payroll</h2><div class='sub'>Employees cannot access this page or its exports.</div></div><div class='actions'>{export_buttons}</div></div><div class='card' style='margin-bottom:15px'><form method='get' class='actions'><div style='max-width:220px'><label>Salary Month</label><input type='month' name='month' value='{month}'></div><button class='btn'>Open Month</button></form></div><div class='grid'><div class='card'><div class='sub'>Employees</div><div class='metric'>{len(rows)}</div></div><div class='card'><div class='sub'>Net Payroll</div><div class='metric'>৳{_money(gross)}</div></div><div class='card'><div class='sub'>Paid</div><div class='metric'>৳{_money(paid)}</div></div><div class='card'><div class='sub'>Unpaid</div><div class='metric'>৳{_money(gross-paid)}</div></div></div><div class='section-gap'></div><div class='two'>{form}<div class='card'><h2>Calculation</h2><div class='code'>Overtime = Hours × Rate\nNet Salary = Fixed + Overtime + Bonus - Deduction</div><p class='sub'>All inputs are entered manually by authorized HR/Admin. Attendance remains separate and employee-visible salary is disabled.</p></div></div><div class='section-gap'></div><div class='card' style='overflow:auto'><h2>{escape(month)} Salary Sheet</h2><table><thead><tr><th>Employee</th><th>Fixed</th><th>Overtime</th><th>Bonus</th><th>Deduction</th><th>Net</th><th>Status</th><th>Action</th></tr></thead><tbody>{''.join(table) or '<tr><td colspan=8>No salary records for this month.</td></tr>'}</tbody></table></div>"""
    return layout("Private Payroll",body,request,"payroll")

@app.post("/payroll")
def save_payroll(request: Request, employee_id: int=Form(...), salary_month: str=Form(...), fixed_salary: float=Form(...), overtime_hours: float=Form(0), overtime_rate: float=Form(0), bonus: float=Form(0), deduction: float=Form(0), note: str=Form(""), return_month: str=Form(""), profile_employee_id: int=Form(0)):
    require_permission(request,"payroll_manage")
    values=(fixed_salary,overtime_hours,overtime_rate,bonus,deduction)
    if not re.fullmatch(r"\d{4}-\d{2}",salary_month) or any(v<0 for v in values): return RedirectResponse(f"/payroll?month={return_month or salary_month}&error=1",303)
    overtime=round(overtime_hours*overtime_rate,2); net=round(fixed_salary+overtime+bonus-deduction,2)
    actor=str(request.session.get('hr_id') or 'super_admin')
    with get_db() as c:
        c.execute("""INSERT INTO payroll_records(employee_id,salary_month,fixed_salary,overtime_hours,overtime_rate,overtime_amount,bonus,deduction,net_salary,note,created_by,updated_by)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(employee_id,salary_month) DO UPDATE SET fixed_salary=excluded.fixed_salary,overtime_hours=excluded.overtime_hours,overtime_rate=excluded.overtime_rate,overtime_amount=excluded.overtime_amount,bonus=excluded.bonus,deduction=excluded.deduction,net_salary=excluded.net_salary,note=excluded.note,updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP""",
            (employee_id,salary_month,fixed_salary,overtime_hours,overtime_rate,overtime,bonus,deduction,net,note.strip(),actor,actor))
    audit(request,"save","payroll",f"{employee_id}:{salary_month}",f"Net salary: {net:.2f}")
    if profile_employee_id==employee_id: return RedirectResponse(f"/employees/{employee_id}?month={salary_month}#payroll",303)
    return RedirectResponse(f"/payroll?month={salary_month}&saved=1",303)

@app.post("/payroll/{payroll_id}/status")
def payroll_status(request: Request, payroll_id: int, status: str=Form(...), month: str=Form(...), return_employee_id: int=Form(0)):
    require_permission(request,"payroll_manage")
    if status not in {"paid","unpaid"}: raise HTTPException(400,"Invalid payment status")
    paid="CURRENT_TIMESTAMP" if status=="paid" else "NULL"
    with get_db() as c: c.execute(f"UPDATE payroll_records SET payment_status=?,paid_at={paid},updated_at=CURRENT_TIMESTAMP WHERE id=?",(status,payroll_id))
    audit(request,"payment_status","payroll",str(payroll_id),status)
    if return_employee_id: return RedirectResponse(f"/employees/{return_employee_id}?month={month}#payroll",303)
    return RedirectResponse(f"/payroll?month={month}",303)

@app.get("/payroll/export.xlsx")
def payroll_xlsx(request: Request, month: str):
    require_permission(request,"payroll_export")
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    rows=_payroll_rows(month); wb=Workbook(); ws=wb.active; ws.title="Payroll"
    ws.append(["BURAQ PRIVATE PAYROLL",month]); ws.merge_cells("A1:L1"); ws["A1"].font=Font(bold=True,size=16,color="FFFFFF"); ws["A1"].fill=PatternFill("solid",fgColor="0D3B2E"); ws["A1"].alignment=Alignment(horizontal="center")
    headers=["Staff ID","Employee","Department","Fixed Salary","OT Hours","OT Rate","OT Amount","Bonus","Deduction","Net Salary","Status","Note"]; ws.append(headers)
    for c in ws[2]: c.font=Font(bold=True,color="FFFFFF"); c.fill=PatternFill("solid",fgColor="087F5B")
    for r in rows: ws.append([r['staff_id'],r['name'],r['department'] or "",r['fixed_salary'],r['overtime_hours'],r['overtime_rate'],r['overtime_amount'],r['bonus'],r['deduction'],r['net_salary'],r['payment_status'],r['note'] or ""])
    total_row=ws.max_row+1; ws.cell(total_row,9,"TOTAL"); ws.cell(total_row,10,f"=SUM(J3:J{total_row-1})"); ws.cell(total_row,9).font=ws.cell(total_row,10).font=Font(bold=True)
    for row in ws.iter_rows(min_row=3,min_col=4,max_col=10):
        for cell in row: cell.number_format='#,##0.00'
    ws.freeze_panes="A3"; ws.auto_filter.ref=f"A2:L{max(2,ws.max_row)}"
    for col in ws.columns:
        letter=col[0].column_letter; ws.column_dimensions[letter].width=min(max(len(str(x.value or "")) for x in col)+2,32)
    out=io.BytesIO(); wb.save(out); out.seek(0)
    return StreamingResponse(out,media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",headers={"Content-Disposition":f"attachment; filename=BURAQ-Payroll-{month}.xlsx"})

def _pdf_font():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    path="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    if os.path.exists(path):
        try: pdfmetrics.registerFont(TTFont("BuraqUnicode",path))
        except Exception: pass
        return "BuraqUnicode"
    return "Helvetica"

@app.get("/payroll/export.pdf")
def payroll_pdf(request: Request, month: str):
    require_permission(request,"payroll_export")
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    rows=_payroll_rows(month); out=io.BytesIO(); font=_pdf_font(); styles=getSampleStyleSheet(); styles['Title'].fontName=font; styles['Normal'].fontName=font
    data=[["Staff ID","Employee","Fixed","OT","Bonus","Deduction","Net","Status"]]+[[str(r['staff_id']),str(r['name']),_money(r['fixed_salary']),_money(r['overtime_amount']),_money(r['bonus']),_money(r['deduction']),_money(r['net_salary']),str(r['payment_status']).title()] for r in rows]
    data.append(["","TOTAL","","","","",_money(sum(float(r['net_salary']) for r in rows)),""])
    doc=SimpleDocTemplate(out,pagesize=landscape(A4),leftMargin=24,rightMargin=24,topMargin=24,bottomMargin=24); table=Table(data,repeatRows=1,colWidths=[65,155,75,70,70,75,80,60])
    table.setStyle(TableStyle([("FONTNAME",(0,0),(-1,-1),font),("BACKGROUND",(0,0),(-1,0),colors.HexColor("#087F5B")),("TEXTCOLOR",(0,0),(-1,0),colors.white),("FONTNAME",(0,0),(-1,0),font),("FONTNAME",(0,-1),(-1,-1),font),("FONTNAME",(0,-1),(-1,-1),font),("GRID",(0,0),(-1,-1),.35,colors.HexColor("#B7C8C2")),("ROWBACKGROUNDS",(0,1),(-1,-2),[colors.white,colors.HexColor("#F4F7F6")]),("ALIGN",(2,1),(-2,-1),"RIGHT"),("TOPPADDING",(0,0),(-1,-1),7),("BOTTOMPADDING",(0,0),(-1,-1),7)]))
    doc.build([Paragraph("BURAQ Private Payroll Report",styles['Title']),Paragraph(f"Salary month: {month} | HR/Admin confidential",styles['Normal']),Spacer(1,12),table]); out.seek(0)
    return StreamingResponse(out,media_type="application/pdf",headers={"Content-Disposition":f"attachment; filename=BURAQ-Payroll-{month}.pdf"})

@app.get("/payroll/{payroll_id}/payslip.pdf")
def payroll_payslip(request: Request, payroll_id: int):
    require_permission(request,"payroll_export")
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    with get_db() as c: r=c.execute("SELECT p.*,e.staff_id,e.name,e.department,e.designation FROM payroll_records p JOIN employees e ON e.id=p.employee_id WHERE p.id=?",(payroll_id,)).fetchone()
    if not r: raise HTTPException(404,"Payroll not found")
    out=io.BytesIO(); font=_pdf_font(); styles=getSampleStyleSheet(); styles['Title'].fontName=font; styles['Normal'].fontName=font
    data=[["Salary Item","Amount (BDT)"],["Fixed Salary",_money(r['fixed_salary'])],[f"Overtime ({r['overtime_hours']:.2f} hours x {_money(r['overtime_rate'])})",_money(r['overtime_amount'])],["Bonus",_money(r['bonus'])],["Deduction",f"- {_money(r['deduction'])}"],["NET SALARY",_money(r['net_salary'])]]
    table=Table(data,colWidths=[330,160]); table.setStyle(TableStyle([("FONTNAME",(0,0),(-1,-1),font),("BACKGROUND",(0,0),(-1,0),colors.HexColor("#087F5B")),("TEXTCOLOR",(0,0),(-1,0),colors.white),("GRID",(0,0),(-1,-1),.5,colors.HexColor("#B7C8C2")),("ALIGN",(1,1),(1,-1),"RIGHT"),("BACKGROUND",(0,-1),(-1,-1),colors.HexColor("#DCFCE7")),("TOPPADDING",(0,0),(-1,-1),10),("BOTTOMPADDING",(0,0),(-1,-1),10)]))
    doc=SimpleDocTemplate(out,pagesize=A4,leftMargin=50,rightMargin=50,topMargin=45,bottomMargin=45)
    doc.build([Paragraph("BURAQ Salary Statement",styles['Title']),Paragraph(f"Employee: {escape(str(r['name']))}<br/>Staff ID: {escape(str(r['staff_id']))}<br/>Department: {escape(str(r['department'] or '-'))}<br/>Salary month: {r['salary_month']}<br/>Payment status: {str(r['payment_status']).title()}",styles['Normal']),Spacer(1,18),table,Spacer(1,18),Paragraph("Confidential - generated for HR/Admin use only.",styles['Normal'])]); out.seek(0)
    return StreamingResponse(out,media_type="application/pdf",headers={"Content-Disposition":f"attachment; filename=BURAQ-Payslip-{r['staff_id']}-{r['salary_month']}.pdf"})

@app.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request, start_date: str = "", end_date: str = "", status: str = "", department: str = ""):
    require_permission(request, "reports_view")
    today = datetime.now(ZoneInfo(settings.timezone)).date()
    start_date = start_date or today.replace(day=1).isoformat(); end_date = end_date or today.isoformat()
    rows = _attendance_report_rows(start_date, end_date, status, department)
    with get_db() as c:
        deps = c.execute("SELECT DISTINCT department FROM employees WHERE department IS NOT NULL AND department<>'' ORDER BY department").fetchall()
    dep_options = ''.join(f"<option value='{escape(d['department'])}' {'selected' if department==d['department'] else ''}>{escape(d['department'])}</option>" for d in deps)
    table_rows = ''.join(f"<tr><td>{escape(r['work_date'])}</td><td><b>{escape(r['staff_id'])}</b><div class='sub'>{escape(r['name'])}</div></td><td>{escape(r['department'] or '-')}</td><td>{escape(r['shift'])}</td><td>{escape((r['check_in'] or '-')[11:16] if r['check_in'] else '-')}</td><td>{escape((r['check_out'] or '-')[11:16] if r['check_out'] else '-')}</td><td>{r['late_minutes']}m</td><td>{r['overtime_minutes']}m</td><td>{escape(r['status'])}</td></tr>" for r in rows) or "<tr><td colspan='9'>No records found.</td></tr>"
    q=f"start_date={start_date}&end_date={end_date}&status={status}&department={department}"
    exports=(f"<a class='btn secondary' href='/reports/export.csv?{q}'>CSV</a><a class='btn secondary' href='/reports/export.xlsx?{q}'>Excel</a><a class='btn secondary' href='/reports/export.pdf?{q}'>PDF</a>" if has_permission(request,'reports_export') else '')
    body=f"""<div class='card'><form method='get'><div class='grid'><div><label>From</label><input type='date' name='start_date' value='{start_date}'></div><div><label>To</label><input type='date' name='end_date' value='{end_date}'></div><div><label>Status</label><select name='status'><option value=''>All</option><option value='present' {'selected' if status=='present' else ''}>Present</option><option value='leave' {'selected' if status=='leave' else ''}>Leave</option><option value='absent' {'selected' if status=='absent' else ''}>Absent</option></select></div><div><label>Department</label><select name='department'><option value=''>All</option>{dep_options}</select></div></div><div class='actions'><button class='btn'>Apply</button>{exports}</div></form></div><div class='section-gap'></div><div class='grid'><div class='card'><div class='sub'>Records</div><div class='metric'>{len(rows)}</div></div><div class='card'><div class='sub'>Late Records</div><div class='metric'>{sum(1 for r in rows if r['late_minutes']>0)}</div></div><div class='card'><div class='sub'>Overtime Minutes</div><div class='metric'>{sum(r['overtime_minutes'] for r in rows)}</div></div><div class='card'><div class='sub'>Leave Records</div><div class='metric'>{sum(1 for r in rows if r['status']=='leave')}</div></div></div><div class='section-gap'></div><div class='card'><h2>Attendance Report</h2><div style='overflow:auto'><table><thead><tr><th>Date</th><th>Employee</th><th>Department</th><th>Shift</th><th>In</th><th>Out</th><th>Late</th><th>OT</th><th>Status</th></tr></thead><tbody>{table_rows}</tbody></table></div></div>"""
    return layout("Attendance Reports", body, request, "reports")

@app.get("/reports/export.csv")
def report_csv(request: Request, start_date: str, end_date: str, status: str = "", department: str = ""):
    require_permission(request,"reports_export"); rows=_attendance_report_rows(start_date,end_date,status,department)
    out=io.StringIO(); w=csv.writer(out); w.writerow(["Date","Staff ID","Name","Department","Shift","Check In","Check Out","Late","Early Leave","Overtime","Status"])
    for r in rows: w.writerow([r[k] for k in ["work_date","staff_id","name","department","shift","check_in","check_out","late_minutes","early_leave_minutes","overtime_minutes","status"]])
    return StreamingResponse(io.BytesIO(out.getvalue().encode("utf-8-sig")),media_type="text/csv",headers={"Content-Disposition":f"attachment; filename=BURAQ-{start_date}-to-{end_date}.csv"})

@app.get("/reports/export.xlsx")
def report_xlsx(request: Request, start_date: str, end_date: str, status: str = "", department: str = ""):
    require_permission(request,"reports_export")
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    rows=_attendance_report_rows(start_date,end_date,status,department); wb=Workbook(); ws=wb.active; ws.title="Attendance"
    headers=["Date","Staff ID","Name","Department","Shift","Check In","Check Out","Late","Early Leave","Overtime","Status"]; ws.append(headers)
    for c in ws[1]: c.font=Font(bold=True,color="FFFFFF"); c.fill=PatternFill("solid",fgColor="087F5B")
    for r in rows: ws.append([r[k] for k in ["work_date","staff_id","name","department","shift","check_in","check_out","late_minutes","early_leave_minutes","overtime_minutes","status"]])
    for col in ws.columns: ws.column_dimensions[col[0].column_letter].width=min(max(len(str(x.value or "")) for x in col)+2,30)
    out=io.BytesIO(); wb.save(out); out.seek(0)
    return StreamingResponse(out,media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",headers={"Content-Disposition":f"attachment; filename=BURAQ-{start_date}-to-{end_date}.xlsx"})

@app.get("/reports/export.pdf")
def report_pdf(request: Request, start_date: str, end_date: str, status: str = "", department: str = ""):
    require_permission(request,"reports_export")
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    rows=_attendance_report_rows(start_date,end_date,status,department); out=io.BytesIO()
    data=[["Date","Staff ID","Name","Department","Shift","In","Out","Late","OT","Status"]]+[[str(r[k] or "") for k in ["work_date","staff_id","name","department","shift","check_in","check_out","late_minutes","overtime_minutes","status"]] for r in rows]
    doc=SimpleDocTemplate(out,pagesize=landscape(A4),leftMargin=18,rightMargin=18,topMargin=18,bottomMargin=18); styles=getSampleStyleSheet(); table=Table(data,repeatRows=1)
    table.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#087F5B")),("TEXTCOLOR",(0,0),(-1,0),colors.white),("FONTSIZE",(0,0),(-1,-1),7),("GRID",(0,0),(-1,-1),.3,colors.grey)]))
    doc.build([Paragraph("BURAQ Attendance Report",styles["Title"]),Paragraph(f"{start_date} to {end_date}",styles["Normal"]),Spacer(1,8),table]); out.seek(0)
    return StreamingResponse(out,media_type="application/pdf",headers={"Content-Disposition":f"attachment; filename=BURAQ-{start_date}-to-{end_date}.pdf"})

@app.get("/hr-operations", response_class=HTMLResponse)
def operations_page(request: Request, saved: str = ""):
    require_permission(request,"leave_view")
    with get_db() as c:
        employees=c.execute("SELECT id,staff_id,name FROM employees ORDER BY name").fetchall()
        leaves=c.execute("SELECT l.*,e.staff_id,e.name FROM leave_requests l JOIN employees e ON e.id=l.employee_id ORDER BY l.id DESC LIMIT 200").fetchall()
        corrections=c.execute("SELECT x.*,e.staff_id,e.name FROM attendance_corrections x JOIN employees e ON e.id=x.employee_id ORDER BY x.id DESC LIMIT 200").fetchall()
        shifts=c.execute("SELECT * FROM shifts ORDER BY name").fetchall(); deps=c.execute("SELECT * FROM departments ORDER BY name").fetchall()
    opts=''.join(f"<option value='{e['id']}'>{escape(e['staff_id'])} — {escape(e['name'])}</option>" for e in employees)
    can_decide=has_permission(request,"leave_manage")
    leave_rows=[]
    for r in leaves:
        actions="-"
        if can_decide and r['status']=='pending': actions=f"<div class='actions'><form method='post' action='/leave/{r['id']}/approve'><button class='btn'>Approve</button></form><form method='post' action='/leave/{r['id']}/reject'><button class='btn danger'>Reject</button></form></div>"
        leave_rows.append(f"<tr><td>{escape(r['staff_id'])}<div class='sub'>{escape(r['name'])}</div></td><td>{escape(r['leave_type'])}</td><td>{escape(r['start_date'])} → {escape(r['end_date'])}</td><td>{escape(r['reason'] or '')}</td><td>{escape(r['status'])}</td><td>{actions}</td></tr>")
    correction_rows=[]
    for r in corrections:
        actions="-"
        if can_decide and r['status']=='pending': actions=f"<div class='actions'><form method='post' action='/correction/{r['id']}/approve'><button class='btn'>Apply</button></form><form method='post' action='/correction/{r['id']}/reject'><button class='btn danger'>Reject</button></form></div>"
        correction_rows.append(f"<tr><td>{escape(r['staff_id'])}<div class='sub'>{escape(r['name'])}</div></td><td>{escape(r['work_date'])}</td><td>{escape(r['requested_check_in'] or '-')}</td><td>{escape(r['requested_check_out'] or '-')}</td><td>{escape(r['reason'])}</td><td>{escape(r['status'])}</td><td>{actions}</td></tr>")
    correction_form=""
    if has_permission(request,"attendance_edit"):
        correction_form=f"<div class='card'><h2>Attendance Correction</h2><form method='post' action='/correction'><label>Employee</label><select name='employee_id'>{opts}</select><label>Work Date</label><input type='date' name='work_date' required><label>Check In (HH:MM)</label><input name='check_in'><label>Check Out (HH:MM)</label><input name='check_out'><label>Reason</label><textarea name='reason' required></textarea><button class='btn'>Submit</button></form></div>"
    management=""
    if has_permission(request,"shift_manage") or has_permission(request,"department_manage"):
        shift_form=("<div class='card'><h2>Shifts</h2><form method='post' action='/shifts'><label>Name</label><input name='name' required><label>Start</label><input type='time' name='start_time' required><label>End</label><input type='time' name='end_time' required><button class='btn'>Save Shift</button></form><p class='sub'>"+", ".join(escape(x['name']) for x in shifts)+"</p></div>") if has_permission(request,"shift_manage") else ""
        dep_form=("<div class='card'><h2>Departments</h2><form method='post' action='/departments'><label>Name</label><input name='name' required><button class='btn'>Save Department</button></form><p class='sub'>"+", ".join(escape(x['name']) for x in deps)+"</p></div>") if has_permission(request,"department_manage") else ""
        management=f"<div class='section-gap'></div><div class='two'>{shift_form}{dep_form}</div>"
    body=("<div class='notice'>Saved successfully.</div>" if saved else "")+f"<div class='two'><div class='card'><h2>Leave Request</h2><form method='post' action='/leave'><label>Employee</label><select name='employee_id'>{opts}</select><label>Type</label><select name='leave_type'><option>Casual</option><option>Sick</option><option>Annual</option><option>Unpaid</option></select><label>Start</label><input type='date' name='start_date' required><label>End</label><input type='date' name='end_date' required><label>Reason</label><textarea name='reason'></textarea><button class='btn'>Submit</button></form></div>{correction_form}</div>{management}<div class='section-gap'></div><div class='card'><h2>Leave Requests</h2><div style='overflow:auto'><table><thead><tr><th>Employee</th><th>Type</th><th>Dates</th><th>Reason</th><th>Status</th><th>Action</th></tr></thead><tbody>{''.join(leave_rows) or '<tr><td colspan=6>No requests.</td></tr>'}</tbody></table></div></div><div class='section-gap'></div><div class='card'><h2>Attendance Corrections</h2><div style='overflow:auto'><table><thead><tr><th>Employee</th><th>Date</th><th>In</th><th>Out</th><th>Reason</th><th>Status</th><th>Action</th></tr></thead><tbody>{''.join(correction_rows) or '<tr><td colspan=7>No requests.</td></tr>'}</tbody></table></div></div>"
    return layout("HR Operations",body,request,"operations")

@app.post("/leave")
def create_leave(request: Request, employee_id: int=Form(...), leave_type: str=Form(...), start_date: str=Form(...), end_date: str=Form(...), reason: str=Form("")):
    require_permission(request,"leave_view")
    with get_db() as c: c.execute("INSERT INTO leave_requests(employee_id,leave_type,start_date,end_date,reason,requested_by) VALUES(?,?,?,?,?,?)",(employee_id,leave_type,start_date,end_date,reason,str(request.session.get('hr_id') or 'super_admin')))
    audit(request,"create","leave_request",str(employee_id),f"{start_date} to {end_date}"); return RedirectResponse("/hr-operations?saved=1",303)

@app.post("/leave/{request_id}/{action}")
def decide_leave(request: Request, request_id: int, action: str):
    require_permission(request,"leave_manage"); status="approved" if action=="approve" else "rejected" if action=="reject" else None
    if not status: raise HTTPException(400)
    with get_db() as c:
        row=c.execute("SELECT * FROM leave_requests WHERE id=?",(request_id,)).fetchone(); c.execute("UPDATE leave_requests SET status=?,decided_by=?,decided_at=CURRENT_TIMESTAMP WHERE id=?",(status,str(request.session.get('hr_id') or 'super_admin'),request_id))
        if row and status=="approved":
            d=datetime.fromisoformat(row['start_date']).date(); end=datetime.fromisoformat(row['end_date']).date()
            while d<=end:
                c.execute("INSERT INTO attendance(employee_id,work_date,status,source) VALUES(?,?,?,?) ON CONFLICT(employee_id,work_date) DO UPDATE SET status=excluded.status,source=excluded.source,updated_at=CURRENT_TIMESTAMP",(row['employee_id'],d.isoformat(),'leave','hr')); d+=timedelta(days=1)
    audit(request,status,"leave_request",str(request_id),""); return RedirectResponse("/hr-operations?saved=1",303)

@app.post("/correction")
def create_correction(request: Request, employee_id: int=Form(...), work_date: str=Form(...), check_in: str=Form(""), check_out: str=Form(""), reason: str=Form(...)):
    require_permission(request,"attendance_edit")
    with get_db() as c: c.execute("INSERT INTO attendance_corrections(employee_id,work_date,requested_check_in,requested_check_out,reason,requested_by) VALUES(?,?,?,?,?,?)",(employee_id,work_date,check_in or None,check_out or None,reason,str(request.session.get('hr_id') or 'super_admin')))
    audit(request,"create","attendance_correction",str(employee_id),work_date); return RedirectResponse("/hr-operations?saved=1",303)

@app.post("/correction/{request_id}/{action}")
def decide_correction(request: Request, request_id: int, action: str):
    require_permission(request,"leave_manage"); status="approved" if action=="approve" else "rejected" if action=="reject" else None
    if not status: raise HTTPException(400)
    with get_db() as c:
        row=c.execute("SELECT * FROM attendance_corrections WHERE id=?",(request_id,)).fetchone()
        if row and status=="approved":
            ci=row['requested_check_in']; co=row['requested_check_out']
            if ci and len(ci)<=5: ci=f"{row['work_date']}T{ci}:00"
            if co and len(co)<=5: co=f"{row['work_date']}T{co}:00"
            c.execute("INSERT INTO attendance(employee_id,work_date,check_in,check_out,status,source) VALUES(?,?,?,?,?,?) ON CONFLICT(employee_id,work_date) DO UPDATE SET check_in=COALESCE(excluded.check_in,attendance.check_in),check_out=COALESCE(excluded.check_out,attendance.check_out),source='hr_correction',updated_at=CURRENT_TIMESTAMP",(row['employee_id'],row['work_date'],ci,co,'present','hr_correction'))
        c.execute("UPDATE attendance_corrections SET status=?,decided_by=?,decided_at=CURRENT_TIMESTAMP WHERE id=?",(status,str(request.session.get('hr_id') or 'super_admin'),request_id))
    audit(request,status,"attendance_correction",str(request_id),""); return RedirectResponse("/hr-operations?saved=1",303)

@app.post("/shifts")
def save_shift(request: Request, name: str=Form(...), start_time: str=Form(...), end_time: str=Form(...)):
    require_permission(request,"shift_manage")
    with get_db() as c: c.execute("INSERT INTO shifts(name,start_time,end_time) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET start_time=excluded.start_time,end_time=excluded.end_time",(name.strip(),start_time,end_time))
    audit(request,"save","shift",name,f"{start_time}-{end_time}"); return RedirectResponse("/hr-operations?saved=1",303)

@app.post("/departments")
def save_department(request: Request, name: str=Form(...)):
    require_permission(request,"department_manage")
    with get_db() as c: c.execute("INSERT INTO departments(name) VALUES(?) ON CONFLICT(name) DO NOTHING",(name.strip(),))
    audit(request,"save","department",name,""); return RedirectResponse("/hr-operations?saved=1",303)

@app.get("/duty-schedules", response_class=HTMLResponse)
def duty_schedules_page(request: Request, saved: str=""):
    require_permission(request,"duty_view"); can_manage=has_permission(request,"duty_manage")
    with get_db() as c:
        employees=c.execute("SELECT id,staff_id,name FROM employees WHERE is_active ORDER BY staff_id").fetchall()
        rows=c.execute("SELECT d.*,e.staff_id,e.name FROM duty_schedules d JOIN employees e ON e.id=d.employee_id ORDER BY e.staff_id,d.weekday").fetchall()
        custom=c.execute("SELECT d.*,e.staff_id,e.name FROM custom_duties d JOIN employees e ON e.id=d.employee_id WHERE d.duty_date>=? ORDER BY d.duty_date,e.staff_id",(datetime.now(ZoneInfo(settings.timezone)).date().isoformat(),)).fetchall()
        logs=c.execute("SELECT l.*,e.staff_id,e.name FROM duty_reminder_logs l JOIN employees e ON e.id=l.employee_id ORDER BY l.id DESC LIMIT 50").fetchall()
    days=['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']; options=''.join(f"<option value='{e['id']}'>{escape(e['staff_id'])} - {escape(e['name'])}</option>" for e in employees)
    form=''
    if can_manage:
        day_options=''.join(f"<option value='{i}'>{d}</option>" for i,d in enumerate(days))
        form=f"""<div class='card'><h2>Assign Weekly Duty</h2><p class='sub'>একই employee ও weekday আবার save করলে schedule update হবে।</p><form method='post'><label>Employee</label><select name='employee_id'>{options}</select><label>Weekday</label><select name='weekday'>{day_options}</select><div class='two'><div><label>Start</label><input type='time' name='start_time' required></div><div><label>End</label><input type='time' name='end_time' required></div></div><label>Office</label><input name='office_name' value='BURAQ Office'><button class='btn'>Save Duty</button></form></div>"""
        custom_form=f"""<div class='card money-card'><div class='card-head'><div><div class='eyebrow'>Outside Weekly Roster</div><h2>Assign Custom Duty</h2></div><span class='tag'>One Day</span></div><p class='sub'>নির্দিষ্ট দিনের special duty weekly schedule-কে override করবে।</p><form method='post' action='/custom-duties'><label>Employee</label><select name='employee_id'>{options}</select><div class='two'><div><label>Duty Date</label><input type='date' name='duty_date' required></div><div><label>Office</label><input name='office_name' value='BURAQ Office'></div></div><div class='two'><div><label>Start</label><input type='time' name='start_time' required></div><div><label>End</label><input type='time' name='end_time' required></div></div><label>Note</label><input name='note' placeholder='Special duty reason (optional)'><button class='btn'>Save Custom Duty</button></form></div>"""
    else: custom_form=''
    roster=[]
    for r in rows:
        action=f"<form method='post' action='/duty-schedules/{r['id']}/delete' onsubmit=\"return confirm('Delete this duty?')\"><button class='btn danger'>Delete</button></form>" if can_manage else ''
        roster.append(f"<tr><td><b>{escape(r['staff_id'])}</b><div class='sub'>{escape(r['name'])}</div></td><td>{days[int(r['weekday'])]}</td><td>{escape(r['start_time'])} - {escape(r['end_time'])}</td><td>{escape(r['office_name'] or 'BURAQ Office')}</td><td><span class='status {'ok' if r['is_active'] else 'bad'}'>{'Active' if r['is_active'] else 'Off'}</span></td><td>{action}</td></tr>")
    log_rows=''.join(f"<tr><td>{escape(str(x['created_at']))}</td><td>{escape(x['staff_id'])} - {escape(x['name'])}</td><td>{escape(x['duty_date'])}</td><td>{escape(x['reminder_type'])}</td><td><span class='status ok'>{escape(x['status'])}</span></td></tr>" for x in logs) or '<tr><td colspan=5>No reminders sent yet.</td></tr>'
    custom_rows=[]
    for r in custom:
        action=f"<form method='post' action='/custom-duties/{r['id']}/delete' onsubmit=\"return confirm('Delete this custom duty?')\"><button class='btn danger'>Delete</button></form>" if can_manage else ''
        custom_rows.append(f"<tr><td><b>{escape(r['duty_date'])}</b></td><td>{escape(r['staff_id'])} - {escape(r['name'])}</td><td>{escape(r['start_time'])} - {escape(r['end_time'])}</td><td>{escape(r['office_name'] or 'BURAQ Office')}</td><td>{escape(r['note'] or '—')}</td><td>{action}</td></tr>")
    notice="<div class='notice'>Duty schedule saved.</div>" if saved else ''
    body=f"""{notice}<div class='hero'><div><div class='eyebrow'>Zero-Touch Workforce</div><h2>Duty Scheduler & Reminders</h2><div class='sub'>Weekly roster plus one-day custom duty with automatic reminders.</div></div><div class='actions'><span class='pill'>{len(rows)} weekly</span><span class='pill'>{len(custom)} custom</span></div></div><div class='two'>{form}<div class='card'><h2>Reminder Timing</h2><div class='salary-part'><span class='sub'>Before duty</span><b>30 minutes</b></div><div class='salary-part'><span class='sub'>Late alert</span><b>10 minutes after start</b></div><div class='salary-part'><span class='sub'>Checkout</span><b>10 minutes before end</b></div><p class='sub'>Custom duty থাকলে ওই দিনের weekly duty ও reminder override হবে।</p></div></div><div class='section-gap'></div>{custom_form}<div class='section-gap'></div><div class='card' style='overflow:auto'><h2>Upcoming Custom Duties</h2><table><thead><tr><th>Date</th><th>Employee</th><th>Duty</th><th>Office</th><th>Note</th><th></th></tr></thead><tbody>{''.join(custom_rows) or '<tr><td colspan=6>No upcoming custom duty.</td></tr>'}</tbody></table></div><div class='section-gap'></div><div class='card' style='overflow:auto'><h2>Weekly Roster</h2><table><thead><tr><th>Employee</th><th>Day</th><th>Duty</th><th>Office</th><th>Status</th><th></th></tr></thead><tbody>{''.join(roster) or '<tr><td colspan=6>No duty assigned.</td></tr>'}</tbody></table></div><div class='section-gap'></div><div class='card' style='overflow:auto'><h2>Recent Reminder Log</h2><table><thead><tr><th>Sent</th><th>Employee</th><th>Duty Date</th><th>Type</th><th>Status</th></tr></thead><tbody>{log_rows}</tbody></table></div>"""
    return layout("Duty Scheduler",body,request,"duty")

@app.post("/duty-schedules")
def save_duty_schedule(request: Request, employee_id: int=Form(...), weekday: int=Form(...), start_time: str=Form(...), end_time: str=Form(...), office_name: str=Form("BURAQ Office")):
    require_permission(request,"duty_manage")
    if weekday not in range(7) or not re.fullmatch(r"\d{2}:\d{2}",start_time) or not re.fullmatch(r"\d{2}:\d{2}",end_time): raise HTTPException(400,"Invalid duty schedule")
    actor=str(request.session.get('hr_id') or 'super_admin')
    with get_db() as c:
        c.execute("INSERT INTO duty_schedules(employee_id,weekday,start_time,end_time,office_name,created_by) VALUES(?,?,?,?,?,?) ON CONFLICT(employee_id,weekday) DO UPDATE SET start_time=excluded.start_time,end_time=excluded.end_time,office_name=excluded.office_name,is_active=excluded.is_active,updated_at=CURRENT_TIMESTAMP",(employee_id,weekday,start_time,end_time,office_name.strip() or 'BURAQ Office',actor))
        audit(request,'save','duty_schedule',f'{employee_id}:{weekday}',f'{start_time}-{end_time}',db=c)
    return RedirectResponse('/duty-schedules?saved=1',303)

@app.post("/duty-schedules/{schedule_id}/delete")
def delete_duty_schedule(request: Request, schedule_id: int):
    require_permission(request,"duty_manage")
    with get_db() as c:
        c.execute("DELETE FROM duty_schedules WHERE id=?",(schedule_id,)); audit(request,'delete','duty_schedule',str(schedule_id),db=c)
    return RedirectResponse('/duty-schedules',303)

@app.post("/custom-duties")
def save_custom_duty(request: Request, employee_id: int=Form(...), duty_date: str=Form(...), start_time: str=Form(...), end_time: str=Form(...), office_name: str=Form("BURAQ Office"), note: str=Form("")):
    require_permission(request,"duty_manage")
    try: datetime.strptime(duty_date,'%Y-%m-%d')
    except ValueError: raise HTTPException(400,'Invalid duty date')
    if not re.fullmatch(r"\d{2}:\d{2}",start_time) or not re.fullmatch(r"\d{2}:\d{2}",end_time): raise HTTPException(400,'Invalid duty time')
    actor=str(request.session.get('hr_id') or 'super_admin')
    with get_db() as c:
        c.execute("INSERT INTO custom_duties(employee_id,duty_date,start_time,end_time,office_name,note,created_by) VALUES(?,?,?,?,?,?,?) ON CONFLICT(employee_id,duty_date) DO UPDATE SET start_time=excluded.start_time,end_time=excluded.end_time,office_name=excluded.office_name,note=excluded.note,is_active=excluded.is_active,updated_at=CURRENT_TIMESTAMP",(employee_id,duty_date,start_time,end_time,office_name.strip() or 'BURAQ Office',note.strip() or None,actor))
        c.execute("DELETE FROM duty_reminder_logs WHERE employee_id=? AND duty_date=?",(employee_id,duty_date))
        audit(request,'save','custom_duty',f'{employee_id}:{duty_date}',f'{start_time}-{end_time}',db=c)
    return RedirectResponse('/duty-schedules?saved=1',303)

@app.post("/custom-duties/{duty_id}/delete")
def delete_custom_duty(request: Request, duty_id: int):
    require_permission(request,"duty_manage")
    with get_db() as c: c.execute("DELETE FROM custom_duties WHERE id=?",(duty_id,)); audit(request,'delete','custom_duty',str(duty_id),db=c)
    return RedirectResponse('/duty-schedules',303)

@app.get("/duplicates")
def duplicate_analysis(request: Request, decision: str="", review: str=""):
    require_permission(request, "approvals_view")
    clauses, params = ["1=1"], []
    if decision in {"accept", "pending", "reject"}: clauses.append("f.decision=?"); params.append(decision)
    if review in {"none", "pending", "approved", "rejected"}: clauses.append("f.review_status=?"); params.append(review)
    with get_db() as c:
        rows = c.execute("SELECT f.*,e.staff_id,e.name FROM attendance_fingerprints f JOIN employees e ON e.id=f.employee_id WHERE "+" AND ".join(clauses)+" ORDER BY f.id DESC LIMIT 300", tuple(params)).fetchall()
    items=[]
    can_manage=has_permission(request,"approvals_manage")
    for r in rows:
        state="bad" if r["decision"]=="reject" else "warn" if r["decision"]=="pending" else "ok"
        controls=""
        if can_manage and r["review_status"]=="pending":
            controls=f"<form method='post' action='/duplicates/{r['id']}/approve' style='display:inline'><button class='btn'>Approve</button></form> <form method='post' action='/duplicates/{r['id']}/reject' style='display:inline'><button class='btn danger'>Reject</button></form>"
        items.append(f"<tr><td>#{r['id']}</td><td><b>{escape(r['name'])}</b><br><span class='sub'>{escape(r['staff_id'])}</span></td><td>{escape(r['action'])}</td><td><span class='status {state}'>{escape(r['decision'])}</span><br><span class='sub'>{escape(r['review_status'])}</span></td><td><b>{r['duplicate_score']*100:.1f}%</b></td><td>Hash {r['hash_score']*100:.0f}%<br>Face {r['face_score']*100:.0f}%<br>Pose {r['pose_score']*100:.0f}%<br>Landmark {r['landmark_score']*100:.0f}%</td><td>{'#'+str(r['matched_fingerprint_id']) if r['matched_fingerprint_id'] else '—'}</td><td>{escape(str(r['created_at']))}</td><td>{controls}</td></tr>")
    thresholds=f"Accept &lt; {settings.duplicate_accept_below:.2f} • Pending {settings.duplicate_accept_below:.2f}–{settings.duplicate_reject_at:.2f} • Reject ≥ {settings.duplicate_reject_at:.2f}"
    body=f"""<div class='hero'><div><div class='eyebrow'>v9.5 Security</div><h2>Duplicate Selfie Analysis</h2><div class='sub'>{thresholds}</div></div><span class='pill'>{len(rows)} records</span></div><div class='card' style='margin-bottom:15px'><form method='get' class='actions'><select name='decision' style='max-width:180px'><option value=''>All decisions</option><option value='accept'>Accept</option><option value='pending'>Pending</option><option value='reject'>Reject</option></select><select name='review' style='max-width:180px'><option value=''>All reviews</option><option value='pending'>Needs review</option><option value='approved'>Approved</option><option value='rejected'>Rejected</option></select><button class='btn'>Filter</button></form></div><div class='card' style='overflow:auto'><table><thead><tr><th>ID</th><th>Employee</th><th>Action</th><th>Decision</th><th>Score</th><th>Signals</th><th>Matched</th><th>Time</th><th>Review</th></tr></thead><tbody>{''.join(items) or '<tr><td colspan=9>No duplicate analysis found</td></tr>'}</tbody></table></div>"""
    return layout("Duplicate Analysis", body, request, "duplicates")

@app.post("/duplicates/{fingerprint_id}/{action}")
def review_duplicate(request: Request, fingerprint_id: int, action: str):
    require_permission(request,"approvals_manage")
    if action not in {"approve","reject"}: raise HTTPException(400,"Invalid action")
    status="approved" if action=="approve" else "rejected"
    actor=str(request.session.get("hr_id") or "super_admin")
    with get_db() as c:
        row=c.execute("SELECT id FROM attendance_fingerprints WHERE id=? AND review_status='pending'",(fingerprint_id,)).fetchone()
        if not row: raise HTTPException(404,"Pending fingerprint not found")
        c.execute("UPDATE attendance_fingerprints SET review_status=?,reviewed_by=?,reviewed_at=CURRENT_TIMESTAMP WHERE id=?",(status,actor,fingerprint_id))
    audit(request,action,"attendance_fingerprint",str(fingerprint_id),status)
    return RedirectResponse("/duplicates?review=pending",303)

@app.get("/webhook/whatsapp", response_class=PlainTextResponse)
def verify(hub_mode: str | None = Query(None, alias="hub.mode"), hub_verify_token: str | None = Query(None, alias="hub.verify_token"), hub_challenge: str | None = Query(None, alias="hub.challenge")):
    if hub_mode == "subscribe" and hub_verify_token == get_setting("whatsapp_verify_token"):
        return hub_challenge or ""
    raise HTTPException(403, "Webhook verification failed")

@app.post("/webhook/whatsapp")
async def webhook(request: Request):
    payload=await request.json(); processed=await handle(payload); return {"status":"ok","processed":processed}
