# BURAQ Smart Attendance

A staff attendance and HR system for BURAQ. Employees check in and out over
WhatsApp by sending their location and a selfie; the server confirms they are
inside the office radius and matches the selfie against their registered face.
A web check-in at `/me` (QR + Staff ID + PIN + GPS + selfie) is the fallback.

HR and Admin users get a web dashboard for employees, duty rosters and shifts,
leave, attendance reports, payroll and payslips, performance reviews, approvals,
audit logs and encrypted backups. Optional AI features use Google Gemini.

Built with Python 3.12, FastAPI and server-rendered HTML. It uses PostgreSQL in
production and SQLite locally.

Release history lives in [CHANGELOG.md](CHANGELOG.md). Deeper notes:
[FACE_AI.md](FACE_AI.md), [PAYROLL_SPECIAL_DUTY.md](PAYROLL_SPECIAL_DUTY.md),
[DESIGN_SYSTEM.md](DESIGN_SYSTEM.md).

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

cat > .env <<'EOF'
ENVIRONMENT=development
REQUIRE_SECURE_SECRETS=false
SESSION_SECRET=change-me-local-session-secret-0000
CONFIG_ENCRYPTION_KEY=change-me-local-config-key-000000
EOF

./start.sh
```

Open http://localhost:8000 and create the Super Admin account on the first
visit. `start.sh` reads `PORT` (default `8000`). The app loads `.env`
automatically, and with no `DATABASE_URL` it stores data in
`data/buraq_attendance.db`.

`ENVIRONMENT=development` is required locally: the default is `production`,
which refuses to start without `DATABASE_URL` and 32-character secrets.

Check it is up with `curl localhost:8000/health` (liveness) and
`curl localhost:8000/ready` (database and setup status).

## Environment variables

### Required in production

| Variable | Purpose |
| --- | --- |
| `ENVIRONMENT` | `production` (default) or `development` |
| `DATABASE_URL` | PostgreSQL connection string; Railway sets it when Postgres is attached |
| `SESSION_SECRET` | Signs login sessions; at least 32 random characters |
| `CONFIG_ENCRYPTION_KEY` | Encrypts stored credentials and backups; at least 32 random characters |

Keep `SESSION_SECRET` and `CONFIG_ENCRYPTION_KEY` in a password manager.
Changing or losing `CONFIG_ENCRYPTION_KEY` makes stored credentials and
encrypted backups unreadable.

### WhatsApp

| Variable | Purpose |
| --- | --- |
| `WHATSAPP_ACCESS_TOKEN` | Meta Cloud API token |
| `WHATSAPP_PHONE_NUMBER_ID` | Sending phone number ID |
| `WHATSAPP_VERIFY_TOKEN` | Token Meta sends when verifying the webhook |
| `WHATSAPP_APP_SECRET` | Verifies webhook signatures (strongly recommended) |
| `PUBLIC_BASE_URL` | Canonical public URL used in links, e.g. `https://smart-attendance.pro` |

The three WhatsApp credentials can also be entered in the dashboard under
Settings. The webhook URL is `<your domain>/webhook/whatsapp`.

### Office and time

| Variable | Default |
| --- | --- |
| `OFFICE_LATITUDE`, `OFFICE_LONGITUDE` | none |
| `OFFICE_RADIUS_METERS` | `150` |
| `TIMEZONE` | `Asia/Dhaka` |

### Optional

| Variable | Purpose |
| --- | --- |
| `GEMINI_API_KEY` | Turns on AI insights and salary-sheet reading |
| `SUPER_ADMIN_EMAIL` | Super Admin login email (default `admin@buraq.com`) |
| `BACKUP_DIR` | Where daily backups go (default `/data/backups`) |
| `BACKUP_ENCRYPTION_KEY` | Separate backup key; falls back to `CONFIG_ENCRYPTION_KEY` |
| `BACKUP_S3_BUCKET`, `BACKUP_S3_ACCESS_KEY_ID`, `BACKUP_S3_SECRET_ACCESS_KEY`, `BACKUP_S3_ENDPOINT`, `BACKUP_S3_REGION` | Off-site backup to any S3-compatible bucket |
| `ALLOW_TEMP_DB_FALLBACK` | Leave `false` in production so a database outage stops the app instead of writing to throwaway storage |

Face matching, liveness and duplicate-selfie thresholds (`FACE_*`,
`LIVENESS_*`, `DUPLICATE_*`) have working defaults; see
[FACE_AI.md](FACE_AI.md) before changing them.

## Run the tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Use `python -m pytest` rather than bare `pytest` so the `app` package is
importable. The suite uses its own SQLite file in `/tmp` and needs no
environment variables.

## Deploy to Railway

The repo deploys with the `Dockerfile`; `railway.toml` sets the build and a
health check on `/health`.

1. Create a Railway project from this GitHub repo and add a PostgreSQL service.
2. On the app service, set `DATABASE_URL` to the Postgres reference variable,
   then set `SESSION_SECRET`, `CONFIG_ENCRYPTION_KEY` and the WhatsApp and
   office variables above.
3. Attach a volume at `/data` so daily backups survive redeploys.
4. Push to the connected branch. Railway builds the image, starts `start.sh`
   and waits for `/health` to pass.
5. Open `/ready` on the live domain, then sign in and finish setup.
6. In Meta's WhatsApp settings, point the webhook at
   `https://<your domain>/webhook/whatsapp` with your `WHATSAPP_VERIFY_TOKEN`.

Database migrations run automatically at startup and never drop existing data.
Back up the database before a release anyway (Settings → Disaster Recovery).
