"""Apply the v9.12 Payroll Pro schema migration safely."""

from app.database import init_db


if __name__ == "__main__":
    init_db()
    print("v9.12 Payroll Pro migration complete")
