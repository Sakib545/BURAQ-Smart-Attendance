"""Run the normal idempotent migration path for v9.5."""
from app.database import init_db

if __name__ == "__main__":
    init_db()
    print("v9.5 migration complete")
