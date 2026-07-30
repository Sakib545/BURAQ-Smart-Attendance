import logging
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from app.config import settings
from app.database import database_ok, get_db, init_db
from app.whatsapp import handle, send_text

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logger = logging.getLogger(__name__)

app = FastAPI(title=settings.app_name, version="4.1.0")


class TestMessage(BaseModel):
    phone: str
    message: str = "BURAQ Attendance test ✅"


def admin(key):
    if not settings.admin_api_key or key != settings.admin_api_key:
        raise HTTPException(401, "Invalid admin API key")


@app.on_event("startup")
def startup():
    init_db()
    missing = settings.validate()
    if missing:
        logger.warning("Missing environment variables: %s", ", ".join(missing))


@app.get("/")
def root():
    return {
        "name": settings.app_name,
        "version": "4.1.0",
        "status": "running",
        "database": "postgresql" if settings.is_postgres else "sqlite",
        "docs": "/docs",
    }


@app.get("/health")
def health():
    ok = database_ok()
    if not ok:
        raise HTTPException(503, "Database unavailable")
    return {"ok": True, "database": "connected"}


@app.get("/webhook/whatsapp", response_class=PlainTextResponse)
def verify(
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        return hub_challenge or ""
    raise HTTPException(403, "Webhook verification failed")


@app.post("/webhook/whatsapp")
async def webhook(request: Request):
    payload = await request.json()
    processed = await handle(payload)
    # Meta requires a quick 200 response, including for status-only webhooks.
    return {"status": "ok", "processed": processed}


@app.post("/api/admin/test-message")
async def test_message(body: TestMessage, x_admin_key: str | None = Header(None, alias="X-Admin-Key")):
    admin(x_admin_key)
    return await send_text(body.phone, body.message)


@app.get("/api/admin/summary")
def summary(x_admin_key: str | None = Header(None, alias="X-Admin-Key")):
    admin(x_admin_key)
    with get_db() as c:
        return {
            "employees": c.execute("SELECT COUNT(*) c FROM employees").fetchone()["c"],
            "registered": c.execute("SELECT COUNT(*) c FROM employees WHERE registration_status='approved'").fetchone()["c"],
            "pending": c.execute("SELECT COUNT(*) c FROM pending_registrations WHERE status='pending'").fetchone()["c"],
            "attendance": c.execute("SELECT COUNT(*) c FROM attendance").fetchone()["c"],
        }


@app.get("/api/admin/employees")
def employees(x_admin_key: str | None = Header(None, alias="X-Admin-Key")):
    admin(x_admin_key)
    with get_db() as c:
        return [dict(row) for row in c.execute("SELECT * FROM employees ORDER BY staff_id").fetchall()]


@app.get("/api/admin/attendance")
def attendance(x_admin_key: str | None = Header(None, alias="X-Admin-Key")):
    admin(x_admin_key)
    with get_db() as c:
        return [dict(row) for row in c.execute("SELECT a.*,e.staff_id,e.name FROM attendance a JOIN employees e ON e.id=a.employee_id ORDER BY work_date DESC").fetchall()]
