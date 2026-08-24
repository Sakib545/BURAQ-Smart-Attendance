"""Restore a BURAQ .buraq file into the configured DATABASE_URL."""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore a BURAQ full backup")
    parser.add_argument("backup", type=Path)
    parser.add_argument("--confirm", required=True, help="Must be RESTORE-BURAQ")
    args = parser.parse_args()
    if args.confirm != "RESTORE-BURAQ":
        raise SystemExit("Refusing restore: pass --confirm RESTORE-BURAQ")
    if not os.getenv("DATABASE_URL", "").strip():
        raise SystemExit("DATABASE_URL is required; refusing to restore into an accidental local database")
    from app.database import init_db
    from app.backups import restore_full_backup
    init_db()
    result = restore_full_backup(args.backup)
    print(f"Restore complete: source={result['created_at']} safety={result['safety_backup']}")


if __name__ == "__main__":
    main()
