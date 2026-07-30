# BURAQ Smart Attendance v4.0

WhatsApp Cloud API based employee attendance system.

## Features
- Meta webhook verification
- Real WhatsApp incoming/outgoing text
- Staff ID registration
- Check In / Check Out
- Morning shift 08:00–16:00
- Evening shift 16:00–22:00
- Overtime after 22:00
- SQLite database
- Employee CSV import and attendance CSV export

## Setup
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Windows:
```bat
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Webhook callback: `https://YOUR-DOMAIN/webhook/whatsapp`
Subscribe to the `messages` field. Verify token must match `.env`.

Commands: `Hi`, `Register`, `Check In`, `Check Out`, `My Attendance`, `Help`.

Import employees:
```bash
python scripts/import_employees.py employees.csv
```

Never upload `.env` or database files to GitHub.
