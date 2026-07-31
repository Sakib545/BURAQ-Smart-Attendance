import csv
from pathlib import Path
from app.database import get_db
from app.services import normalize_phone


def import_employees(path: str = "employees.csv") -> int:
    source = Path(path)
    if not source.exists():
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
            c.execute(
                "INSERT INTO employees(staff_id,name,phone,department,shift) VALUES(?,?,?,?,?) "
                "ON CONFLICT(staff_id) DO UPDATE SET name=excluded.name,phone=excluded.phone,department=excluded.department,shift=excluded.shift,updated_at=CURRENT_TIMESTAMP",
                (staff_id, name, normalize_phone(row.get("phone", "")) or None, (row.get("department") or "").strip() or None, shift),
            )
            count += 1
    return count
