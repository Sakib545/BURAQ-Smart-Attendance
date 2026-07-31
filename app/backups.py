"""Daily payroll backup worker for persistent Railway volumes."""
import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import settings
from app.database import get_db

logger=logging.getLogger(__name__)


def create_payroll_backup() -> Path:
    backup_dir=Path(os.getenv("BACKUP_DIR","/data/backups")); backup_dir.mkdir(parents=True,exist_ok=True)
    today=datetime.now(ZoneInfo(settings.timezone)).date().isoformat(); target=backup_dir/f"buraq-payroll-{today}.json"
    with get_db() as c:
        payload={"version":2,"type":"buraq_payroll_backup","created_at":datetime.now(ZoneInfo(settings.timezone)).isoformat(),"employee_salary_master":[dict(r) for r in c.execute("SELECT id,staff_id,name,fixed_salary,default_overtime_rate FROM employees ORDER BY id").fetchall()],"payroll_records":[dict(r) for r in c.execute("SELECT * FROM payroll_records ORDER BY salary_month,id").fetchall()],"payroll_change_logs":[dict(r) for r in c.execute("SELECT * FROM payroll_change_logs ORDER BY id").fetchall()]}
    temporary=target.with_suffix(".tmp"); temporary.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding="utf-8"); temporary.replace(target)
    return target


async def payroll_backup_worker():
    last_date=""
    while True:
        try:
            today=datetime.now(ZoneInfo(settings.timezone)).date().isoformat()
            if today!=last_date:
                path=await asyncio.to_thread(create_payroll_backup); last_date=today; logger.info("Daily payroll backup saved: %s",path)
        except asyncio.CancelledError: raise
        except Exception: logger.exception("Daily payroll backup failed")
        await asyncio.sleep(3600)
