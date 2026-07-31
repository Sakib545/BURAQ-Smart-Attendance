from __future__ import annotations

import csv
import logging
from pathlib import Path

from app.database import get_db
from app.services import normalize_phone

logger = logging.getLogger(__name__)


def seed_employees(csv_path: str | Path = "employees.csv") -> int:
    """Import the bundled BURAQ employee list on every startup.

    Existing registrations and WhatsApp numbers are preserved. Only the master
    employee fields (name, phone, department and shift) are refreshed.
    """
    path = Path(csv_path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[1] / path
    if not path.exists():
        logger.warning("Employee seed file not found: %s", path)
        return 0

    count = 0
    with path.open(encoding="utf-8-sig", newline="") as file, get_db() as db:
        for row in csv.DictReader(file):
            staff_id = (row.get("staff_id") or "").strip().upper()
            name = (row.get("name") or "").strip()
            if not staff_id or not name:
                continue
            phone = normalize_phone(row.get("phone") or "") or None
            department = (row.get("department") or "").strip() or None
            shift = (row.get("shift") or "morning").strip().lower()
            if shift not in {"morning", "evening"}:
                shift = "morning"

            existing = db.execute(
                "SELECT id FROM employees WHERE LOWER(staff_id)=LOWER(?)",
                (staff_id,),
            ).fetchone()
            if existing:
                db.execute(
                    "UPDATE employees SET name=?,phone=?,department=?,shift=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (name, phone, department, shift, existing["id"]),
                )
            else:
                db.execute(
                    "INSERT INTO employees(staff_id,name,phone,department,shift) VALUES(?,?,?,?,?)",
                    (staff_id, name, phone, department, shift),
                )
            count += 1

        # Remove only the old bundled demo record. Real manually-created staff
        # remain untouched.
        db.execute(
            "DELETE FROM employees WHERE UPPER(staff_id)='BRQ001' AND name='Demo Employee'"
        )

    logger.info("Employee master list synced: %s employees", count)
    return count
