# BURAQ Smart Attendance v7.0 — Professional Dashboard

This release keeps the v6.2 Face AI, GPS, guided WhatsApp attendance and live selfie challenge, and upgrades the administrator experience.

## New in v7.0

- One-time setup now asks only for an admin password.
- WhatsApp credentials are imported automatically from Railway Variables when available.
- Credentials are managed later from **Dashboard → Settings**.
- Access Token, Phone Number ID and Verify Token are masked in the UI.
- Sensitive values are encrypted before database storage.
- Modern responsive sidebar dashboard with light/dark theme.
- System health panel for Database, WhatsApp, Webhook and Face AI.
- Secure credential editing without exposing the current token.
- Admin password change page.
- Encrypted configuration backup and restore.
- Existing employees, registrations, face samples and attendance records are preserved.

## Recommended Railway Variables

```env
SESSION_SECRET=use-a-long-random-value
CONFIG_ENCRYPTION_KEY=use-another-long-random-value
WHATSAPP_ACCESS_TOKEN=your-meta-token
WHATSAPP_PHONE_NUMBER_ID=your-phone-number-id
WHATSAPP_VERIFY_TOKEN=your-verify-token
OFFICE_LATITUDE=25.18892481916644
OFFICE_LONGITUDE=89.87014577946071
OFFICE_RADIUS_METERS=100
TIMEZONE=Asia/Dhaka
```

`CONFIG_ENCRYPTION_KEY` must remain unchanged. Changing it prevents previously encrypted credentials and backups from being decrypted.

## Deployment

Replace the repository files with this package and deploy normally on Railway. Existing PostgreSQL data is not deleted. After deployment:

1. Open the Railway public URL.
2. On the first run, create the admin password once.
3. Future visits open the login page, then the dashboard.
4. WhatsApp credentials can be viewed in masked form and changed from Settings.

## Important

The exported configuration backup is encrypted. Restore it only to an installation using the same `CONFIG_ENCRYPTION_KEY`.
