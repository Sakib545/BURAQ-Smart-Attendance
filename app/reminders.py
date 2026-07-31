"""Database-backed, idempotent WhatsApp duty reminders."""
import asyncio
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import settings
from app.database import get_db
from app.whatsapp import send_template

logger=logging.getLogger(__name__)

TEMPLATES={
    "before":os.getenv("WHATSAPP_DUTY_TEMPLATE","duty_reminder"),
    "late":os.getenv("WHATSAPP_LATE_TEMPLATE","late_reminder"),
    "checkout":os.getenv("WHATSAPP_CHECKOUT_TEMPLATE","checkout_reminder"),
}

def _minutes(now, value):
    target=datetime.combine(now.date(),datetime.strptime(value,"%H:%M").time(),tzinfo=now.tzinfo)
    return (target-now).total_seconds()/60

async def run_reminder_cycle():
    now=datetime.now(ZoneInfo(settings.timezone)); duty_date=now.date().isoformat()
    with get_db() as c:
        rows=c.execute("""SELECT d.*,e.name,e.whatsapp_phone,e.phone,
            (SELECT check_in FROM attendance a WHERE a.employee_id=e.id AND a.work_date=?) check_in,
            (SELECT check_out FROM attendance a WHERE a.employee_id=e.id AND a.work_date=?) check_out
            FROM duty_schedules d JOIN employees e ON e.id=d.employee_id
            WHERE d.weekday=? AND d.is_active AND e.is_active AND e.registration_status='approved'""",(duty_date,duty_date,now.weekday())).fetchall()
    for row in rows:
        phone=row['whatsapp_phone'] or row['phone']
        if not phone: continue
        start_gap=_minutes(now,row['start_time']); end_gap=_minutes(now,row['end_time']); kind=None
        if 28<=start_gap<=32: kind="before"
        elif -12<=start_gap<=-8 and not row['check_in']: kind="late"
        elif 8<=end_gap<=12 and row['check_in'] and not row['check_out']: kind="checkout"
        if not kind: continue
        with get_db() as c:
            exists=c.execute("SELECT 1 FROM duty_reminder_logs WHERE employee_id=? AND duty_date=? AND reminder_type=?",(row['employee_id'],duty_date,kind)).fetchone()
        if exists: continue
        values=[row['name'],row['start_time'],row['end_time'],row['office_name'] or 'BURAQ Office']
        result=await send_template(phone,TEMPLATES[kind],values)
        if result.get('sent'):
            with get_db() as c:
                c.execute("INSERT INTO duty_reminder_logs(employee_id,duty_date,reminder_type,status,details) VALUES(?,?,?,?,?) ON CONFLICT(employee_id,duty_date,reminder_type) DO NOTHING",(row['employee_id'],duty_date,kind,'sent',TEMPLATES[kind]))
        else: logger.warning("Duty reminder failed employee=%s type=%s result=%s",row['employee_id'],kind,result)

async def reminder_worker():
    while True:
        try: await run_reminder_cycle()
        except asyncio.CancelledError: raise
        except Exception: logger.exception("Duty reminder cycle failed")
        await asyncio.sleep(60)
