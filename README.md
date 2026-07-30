# BURAQ Smart Attendance v4.1 — Railway + PostgreSQL

A production-ready WhatsApp Cloud API attendance system. Railway keeps it online even when your Mac is off.

## Main features
- Permanent Railway webhook URL
- PostgreSQL on Railway; SQLite fallback locally
- Staff ID registration
- Check In / Check Out / My Attendance
- Morning shift 08:00–16:00 and evening shift 16:00–22:00
- Overtime after 22:00
- Duplicate Meta webhook protection
- WhatsApp API error logging
- Database-aware `/health` endpoint
- Employee CSV import and attendance CSV export

## Railway deployment
1. Upload this project to a GitHub repository.
2. In Railway choose **New Project → Deploy from GitHub Repo**.
3. In the same Railway project choose **New → Database → PostgreSQL**.
4. Railway will provide `DATABASE_URL` to the app automatically. If it does not, add a variable reference from PostgreSQL to the app service.
5. Add these variables to the app service:

```env
APP_NAME=BURAQ Smart Attendance
TIMEZONE=Asia/Dhaka
WHATSAPP_VERIFY_TOKEN=your-long-random-verify-token
WHATSAPP_ACCESS_TOKEN=your-meta-access-token
WHATSAPP_PHONE_NUMBER_ID=your-phone-number-id
META_API_VERSION=v23.0
ADMIN_API_KEY=your-long-random-admin-key
LOG_LEVEL=INFO
```

6. In Railway service settings generate a public domain.
7. Open `https://YOUR-RAILWAY-DOMAIN/health`. It should return `{"ok":true,"database":"connected"}`.
8. In Meta set the callback URL:

```text
https://YOUR-RAILWAY-DOMAIN/webhook/whatsapp
```

9. Enter the exact same `WHATSAPP_VERIFY_TOKEN`, verify it, and subscribe to the `messages` field.
10. Send `Hi` to the WhatsApp test/business number.

## Local setup
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## Commands
`Hi`, `Register`, `Check In`, `Check Out`, `My Attendance`, `Help`

## Import employees
```bash
python scripts/import_employees.py employees.csv
```

## Export attendance
```bash
python scripts/export_attendance.py
```

Never commit `.env`, access tokens, databases, or exported private employee data.
