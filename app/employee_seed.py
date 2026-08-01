"""Initial employee load from employees.csv.

This runs at startup. It used to upsert every row on every boot, which meant any
edit made in the HR panel — name, phone, department, shift — was silently
reverted to the CSV value on the next deploy, and deleted employees came back.

The CSV is now treated as a one-time seed: it loads only when the employees
table is empty, and it never overwrites a row that already exists. Set
SEED_EMPLOYEES=true to force it to run again after the first boot.
"""

import csv
import logging
import os
from pathlib import Path

from app.database import get_db
from app.services import normalize_phone

logger = logging.getLogger(__name__)


def _forced() -> bool:
    return os.getenv("SEED_EMPLOYEES", "").strip().lower() in {"1", "true", "yes", "on"}


def import_employees(path: str = "employees.csv") -> int:
    source = Path(path)
    if not source.exists():
        return 0

    with get_db() as c:
        existing = int(c.execute("SELECT COUNT(*) c FROM employees").fetchone()["c"] or 0)

    if existing and not _forced():
        logger.info("Employee seed skipped; %s employees already in the database", existing)
        return 0

    count = 0
    with source.open(encoding="utf-8-sig", newline="") as f, get_db() as c:
        for row in csv.DictReader(f):
            staff_id = (row.get("staff_id") or "").strip()
            name = (row.get("name") or "").strip()
            if not staff_id or not name:
                continue
            shift = (row.get("shift") or "morning").strip().lower()
            if shift not in {"morning", "evening"}:
                shift = "morning"
            # Insert only. An employee record that already exists is the source
            # of truth from here on, not the CSV.
            c.execute(
                "INSERT INTO employees(staff_id,name,phone,department,shift) VALUES(?,?,?,?,?) "
                "ON CONFLICT(staff_id) DO NOTHING",
                (staff_id, name, normalize_phone(row.get("phone", "")) or None,
                 (row.get("department") or "").strip() or None, shift),
            )
            count += 1

    logger.info("Employee seed loaded %s rows from %s", count, source)
    return count
