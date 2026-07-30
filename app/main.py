import csv
import hashlib
import hmac
import io
import logging
import os
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo
from html import escape

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.database import database_kind, database_ok, database_warning, get_db, init_db
from app.runtime import configured, get_setting, set_setting
from app.whatsapp import handle, send_text

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logger = logging.getLogger(__name__)
app = FastAPI(title=settings.app_name, version="4.3.0", docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET", secrets.token_urlsafe(32)), https_only=settings.environment == "production", same_site="lax")

CSS = """
<style>
:root{--bg:#f4f7fb;--card:#fff;--ink:#172033;--muted:#6c7484;--brand:#0f766e;--brand2:#115e59;--ok:#15803d;--warn:#b45309;--bad:#b91c1c;--line:#e5e9f0}
*{box-sizing:border-box}body{margin:0;background:var(--bg);font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif;color:var(--ink)}
.wrap{max-width:1160px;margin:auto;padding:24px}.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:22px}.brand{font-size:24px;font-weight:800}.sub{color:var(--muted);font-size:14px}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:20px;box-shadow:0 8px 30px rgba(30,41,59,.05)}.metric{font-size:30px;font-weight:800;margin-top:8px}.status{display:inline-block;padding:7px 11px;border-radius:999px;font-size:13px;font-weight:700}.ok{background:#dcfce7;color:var(--ok)}.warn{background:#fef3c7;color:var(--warn)}.bad{background:#fee2e2;color:var(--bad)}
.actions{display:flex;gap:10px;flex-wrap:wrap}.btn{border:0;border-radius:11px;padding:11px 15px;background:var(--brand);color:white;font-weight:700;cursor:pointer;text-decoration:none;display:inline-block}.btn:hover{background:var(--brand2)}.btn.secondary{background:#e7f3f1;color:var(--brand2)}.btn.danger{background:#fee2e2;color:var(--bad)}input,select{width:100%;padding:11px 12px;border:1px solid #d7dce5;border-radius:10px;margin:6px 0 14px;background:white}.two{display:grid;grid-template-columns:1fr 1fr;gap:16px}table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:11px;border-bottom:1px solid var(--line)}th{color:var(--muted)}h2{margin:0 0 14px}h3{margin:0 0 10px}.notice{padding:13px 15px;border-radius:12px;background:#ecfeff;color:#155e75;margin-bottom:16px}.code{font-family:ui-monospace,monospace;background:#111827;color:#f9fafb;padding:12px;border-radius:10px;overflow:auto}.login{max-width:430px;margin:8vh auto}.nav{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:18px}@media(max-width:850px){.grid{grid-template-columns:1fr 1fr}.two{grid-template-columns:1fr}}@media(max-width:520px){.grid{grid-template-columns:1fr}.wrap{padding:14px}.top{align-items:flex-start;gap:12px}}
</style>
"""

def layout(title: str, body: str):
    return HTMLResponse(f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{escape(title)}</title>{CSS}</head><body>{body}</body></html>")

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

def logged_in(request: Request): return bool(request.session.get("admin"))
def require_login(request: Request):
    if not logged_in(request): raise HTTPException(401, "Login required")

def base_url(request: Request): return str(request.base_url).rstrip("/")

@app.on_event("startup")
def startup():
    init_db()

@app.get("/health")
def health():
    # Railway healthcheck is a liveness check. It must stay 200 while the
    # dashboard explains any optional database/configuration warning.
    return {
        "ok": True,
        "version": "4.3.0",
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
    if get_setting("admin_password_hash") and not logged_in(request): return RedirectResponse("/login", 302)
    token = get_setting("whatsapp_verify_token") or secrets.token_urlsafe(24)
    body=f"<div class='wrap login'><div class='card'><div class='brand'>BURAQ Smart Attendance</div><p class='sub'>One-time Easy Setup</p><form method='post'><label>Admin password</label><input type='password' name='password' minlength='6' required><label>WhatsApp Access Token</label><input name='access_token' value='{escape(get_setting('whatsapp_access_token'))}' required><label>Phone Number ID</label><input name='phone_id' value='{escape(get_setting('whatsapp_phone_number_id'))}' required><label>Verify Token</label><input name='verify_token' value='{escape(token)}' required><button class='btn' type='submit'>Save & Open Dashboard</button></form><p class='sub'>এই তথ্য একবারই দিতে হবে। পরে Dashboard থেকে পরিবর্তন করা যাবে।</p></div></div>"
    return layout("Easy Setup", body)

@app.post("/setup")
def save_setup(request: Request, password: str = Form(...), access_token: str = Form(...), phone_id: str = Form(...), verify_token: str = Form(...)):
    if get_setting("admin_password_hash") and not logged_in(request): raise HTTPException(403)
    set_setting("admin_password_hash", hash_password(password))
    set_setting("whatsapp_access_token", access_token.strip())
    set_setting("whatsapp_phone_number_id", phone_id.strip())
    set_setting("whatsapp_verify_token", verify_token.strip())
    request.session["admin"] = True
    return RedirectResponse("/dashboard", 303)

@app.get("/login", response_class=HTMLResponse)
def login_page(error: str = ""):
    msg = "<div class='notice' style='background:#fee2e2;color:#991b1b'>Password ভুল হয়েছে।</div>" if error else ""
    return layout("Admin Login", f"<div class='wrap login'><div class='card'><div class='brand'>BURAQ Admin</div><p class='sub'>Professional Attendance Dashboard</p>{msg}<form method='post'><input type='password' name='password' placeholder='Admin password' required><button class='btn' type='submit'>Login</button></form></div></div>")

@app.post("/login")
def login(request: Request, password: str = Form(...)):
    if verify_password(password, get_setting("admin_password_hash")):
        request.session["admin"] = True
        return RedirectResponse("/dashboard", 303)
    return RedirectResponse("/login?error=1", 303)

@app.get("/logout")
def logout(request: Request):
    request.session.clear(); return RedirectResponse("/login", 302)

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    require_login(request)
    today = datetime.now(ZoneInfo(settings.timezone)).date().isoformat()
    with get_db() as c:
        employees = c.execute("SELECT COUNT(*) c FROM employees").fetchone()["c"]
        registered = c.execute("SELECT COUNT(*) c FROM employees WHERE registration_status='approved'").fetchone()["c"]
        pending = c.execute("SELECT COUNT(*) c FROM pending_registrations WHERE status='pending'").fetchone()["c"]
        present = c.execute("SELECT COUNT(*) c FROM attendance WHERE work_date=? AND check_in IS NOT NULL", (today,)).fetchone()["c"]
        recent = c.execute("SELECT a.work_date,a.check_in,a.check_out,a.late_minutes,a.overtime_minutes,e.staff_id,e.name FROM attendance a JOIN employees e ON e.id=a.employee_id ORDER BY a.id DESC LIMIT 12").fetchall()
    cfg = configured(); db = database_ok(); db_kind = database_kind(); warning = database_warning(); webhook=f"{base_url(request)}/webhook/whatsapp"
    rows=''.join(f"<tr><td>{escape(str(r['work_date']))}</td><td>{escape(r['staff_id'])}</td><td>{escape(r['name'])}</td><td>{escape((r['check_in'] or '-')[11:16] if r['check_in'] else '-')}</td><td>{escape((r['check_out'] or '-')[11:16] if r['check_out'] else '-')}</td><td>{r['late_minutes']}m</td><td>{r['overtime_minutes']}m</td></tr>" for r in recent) or "<tr><td colspan='7'>এখনো attendance নেই</td></tr>"
    body=f"<div class='wrap'><div class='top'><div><div class='brand'>BURAQ Smart Attendance</div><div class='sub'>Professional Control Center</div></div><a class='btn secondary' href='/logout'>Logout</a></div><div class='nav'><a class='btn' href='/dashboard'>Dashboard</a><a class='btn secondary' href='/employees'>Employees</a><a class='btn secondary' href='/pending'>Approvals</a><a class='btn secondary' href='/settings'>Settings</a><a class='btn secondary' href='/export/attendance.csv'>Export CSV</a></div><div class='grid'><div class='card'><div class='sub'>Total Employees</div><div class='metric'>{employees}</div></div><div class='card'><div class='sub'>Registered</div><div class='metric'>{registered}</div></div><div class='card'><div class='sub'>Present Today</div><div class='metric'>{present}</div></div><div class='card'><div class='sub'>Pending Approval</div><div class='metric'>{pending}</div></div></div><div style='height:16px'></div><div class='two'><div class='card'><h3>System Status</h3><p><span class='status {'ok' if db else 'bad'}'>Database {'Connected' if db else 'Error'} ({escape(db_kind)})</span></p>{f"<div class='notice' style='background:#fef3c7;color:#92400e'>{escape(warning)}</div>" if warning else ''}<p><span class='status {'ok' if cfg else 'warn'}'>WhatsApp {'Ready' if cfg else 'Setup needed'}</span></p><div class='sub'>Webhook URL</div><div class='code'>{escape(webhook)}</div><p class='sub'>Meta-তে এই URL একবার বসালেই হবে। এরপর Mac বন্ধ থাকলেও Railway-এ চলবে।</p></div><div class='card'><h3>Quick Test</h3><form method='post' action='/test-message'><label>WhatsApp number (country codeসহ)</label><input name='phone' placeholder='8801XXXXXXXXX' required><label>Message</label><input name='message' value='BURAQ Attendance connected ✅'><button class='btn'>Send Test Message</button></form></div></div><div style='height:16px'></div><div class='card'><h2>Recent Attendance</h2><div style='overflow:auto'><table><thead><tr><th>Date</th><th>Staff ID</th><th>Name</th><th>In</th><th>Out</th><th>Late</th><th>OT</th></tr></thead><tbody>{rows}</tbody></table></div></div></div>"
    return layout("BURAQ Dashboard", body)

@app.post("/test-message")
async def test_message(request: Request, phone: str = Form(...), message: str = Form(...)):
    require_login(request)
    result = await send_text(phone.strip(), message.strip())
    return RedirectResponse("/dashboard" if result.get("sent") else "/settings?error=send", 303)

@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, error: str = ""):
    require_login(request)
    notice = "<div class='notice' style='background:#fee2e2;color:#991b1b'>Test message পাঠানো যায়নি। Token ও Phone Number ID পরীক্ষা করুন।</div>" if error else ""
    webhook=f"{base_url(request)}/webhook/whatsapp"
    body=f"<div class='wrap'><div class='top'><div><div class='brand'>Settings</div><div class='sub'>সব গুরুত্বপূর্ণ সেটিং এক জায়গায়</div></div><a class='btn secondary' href='/dashboard'>Dashboard</a></div>{notice}<div class='two'><div class='card'><h2>WhatsApp Connection</h2><form method='post'><label>Access Token</label><input type='password' name='access_token' value='{escape(get_setting('whatsapp_access_token'))}' required><label>Phone Number ID</label><input name='phone_id' value='{escape(get_setting('whatsapp_phone_number_id'))}' required><label>Verify Token</label><input name='verify_token' value='{escape(get_setting('whatsapp_verify_token'))}' required><button class='btn'>Save Settings</button></form></div><div class='card'><h2>Meta Webhook</h2><div class='sub'>Callback URL</div><div class='code'>{escape(webhook)}</div><br><div class='sub'>Verify Token</div><div class='code'>{escape(get_setting('whatsapp_verify_token'))}</div><p class='sub'>Meta Webhooks-এ messages field Subscribe করুন। এই কাজ শুধু একবার।</p></div></div></div>"
    return layout("Settings", body)

@app.post("/settings")
def save_settings(request: Request, access_token: str = Form(...), phone_id: str = Form(...), verify_token: str = Form(...)):
    require_login(request)
    set_setting("whatsapp_access_token", access_token.strip()); set_setting("whatsapp_phone_number_id", phone_id.strip()); set_setting("whatsapp_verify_token", verify_token.strip())
    return RedirectResponse("/dashboard", 303)

@app.get("/employees", response_class=HTMLResponse)
def employees_page(request: Request):
    require_login(request)
    with get_db() as c: rows=c.execute("SELECT * FROM employees ORDER BY staff_id").fetchall()
    trs=''.join(f"<tr><td>{escape(r['staff_id'])}</td><td>{escape(r['name'])}</td><td>{escape(r['phone'] or '')}</td><td>{escape(r['department'] or '')}</td><td>{escape(r['shift'])}</td><td>{escape(r['registration_status'])}</td></tr>" for r in rows) or "<tr><td colspan='6'>কোনো employee নেই</td></tr>"
    body=f"<div class='wrap'><div class='top'><div><div class='brand'>Employees</div><div class='sub'>Add and manage staff</div></div><a class='btn secondary' href='/dashboard'>Dashboard</a></div><div class='two'><div class='card'><h2>Add Employee</h2><form method='post'><label>Staff ID</label><input name='staff_id' required><label>Name</label><input name='name' required><label>Phone</label><input name='phone' placeholder='8801XXXXXXXXX'><label>Department</label><input name='department'><label>Shift</label><select name='shift'><option value='morning'>Morning 8AM–4PM</option><option value='evening'>Evening 4PM–10PM</option></select><button class='btn'>Add Employee</button></form></div><div class='card'><h2>Employee List</h2><div style='overflow:auto'><table><thead><tr><th>ID</th><th>Name</th><th>Phone</th><th>Dept.</th><th>Shift</th><th>Status</th></tr></thead><tbody>{trs}</tbody></table></div></div></div></div>"
    return layout("Employees", body)

@app.post("/employees")
def add_employee(request: Request, staff_id: str = Form(...), name: str = Form(...), phone: str = Form(""), department: str = Form(""), shift: str = Form("morning")):
    require_login(request)
    try:
        with get_db() as c: c.execute("INSERT INTO employees(staff_id,name,phone,department,shift) VALUES(?,?,?,?,?)", (staff_id.strip(), name.strip(), phone.strip() or None, department.strip() or None, shift))
    except Exception as exc:
        logger.warning("Employee add failed: %s", exc)
    return RedirectResponse("/employees", 303)

@app.get("/pending", response_class=HTMLResponse)
def pending_page(request: Request):
    require_login(request)
    with get_db() as c: rows=c.execute("SELECT p.id,p.whatsapp_phone,p.created_at,e.staff_id,e.name FROM pending_registrations p JOIN employees e ON e.id=p.employee_id WHERE p.status='pending' ORDER BY p.id DESC").fetchall()
    trs=''.join(f"<tr><td>{escape(r['staff_id'])}</td><td>{escape(r['name'])}</td><td>{escape(r['whatsapp_phone'])}</td><td><form method='post' action='/pending/{r['id']}/approve'><button class='btn'>Approve</button></form></td><td><form method='post' action='/pending/{r['id']}/reject'><button class='btn danger'>Reject</button></form></td></tr>" for r in rows) or "<tr><td colspan='5'>কোনো pending registration নেই</td></tr>"
    return layout("Approvals", f"<div class='wrap'><div class='top'><div><div class='brand'>Registration Approvals</div><div class='sub'>One-click approval</div></div><a class='btn secondary' href='/dashboard'>Dashboard</a></div><div class='card'><table><thead><tr><th>Staff ID</th><th>Name</th><th>WhatsApp</th><th></th><th></th></tr></thead><tbody>{trs}</tbody></table></div></div>")

@app.post("/pending/{pending_id}/approve")
def approve_pending(request: Request, pending_id: int):
    require_login(request)
    with get_db() as c:
        row=c.execute("SELECT * FROM pending_registrations WHERE id=? AND status='pending'", (pending_id,)).fetchone()
        if row:
            c.execute("UPDATE employees SET whatsapp_phone=?,registration_status='approved',updated_at=CURRENT_TIMESTAMP WHERE id=?", (row['whatsapp_phone'],row['employee_id']))
            c.execute("UPDATE pending_registrations SET status='approved',updated_at=CURRENT_TIMESTAMP WHERE id=?", (pending_id,))
    return RedirectResponse("/pending",303)

@app.post("/pending/{pending_id}/reject")
def reject_pending(request: Request, pending_id: int):
    require_login(request)
    with get_db() as c: c.execute("UPDATE pending_registrations SET status='rejected',updated_at=CURRENT_TIMESTAMP WHERE id=?", (pending_id,))
    return RedirectResponse("/pending",303)

@app.get("/export/attendance.csv")
def export_attendance(request: Request):
    require_login(request)
    with get_db() as c: rows=c.execute("SELECT a.work_date,e.staff_id,e.name,e.department,e.shift,a.check_in,a.check_out,a.late_minutes,a.early_leave_minutes,a.overtime_minutes,a.status FROM attendance a JOIN employees e ON e.id=a.employee_id ORDER BY a.work_date DESC,e.staff_id").fetchall()
    output=io.StringIO(); writer=csv.writer(output); writer.writerow(["Date","Staff ID","Name","Department","Shift","Check In","Check Out","Late Minutes","Early Leave Minutes","Overtime Minutes","Status"])
    for r in rows: writer.writerow(list(r.values()))
    data=output.getvalue().encode("utf-8-sig")
    return StreamingResponse(io.BytesIO(data),media_type="text/csv",headers={"Content-Disposition":"attachment; filename=BURAQ-Attendance.csv"})

@app.get("/webhook/whatsapp", response_class=PlainTextResponse)
def verify(hub_mode: str | None = Query(None, alias="hub.mode"), hub_verify_token: str | None = Query(None, alias="hub.verify_token"), hub_challenge: str | None = Query(None, alias="hub.challenge")):
    if hub_mode == "subscribe" and hub_verify_token == get_setting("whatsapp_verify_token"):
        return hub_challenge or ""
    raise HTTPException(403, "Webhook verification failed")

@app.post("/webhook/whatsapp")
async def webhook(request: Request):
    payload=await request.json(); processed=await handle(payload); return {"status":"ok","processed":processed}
