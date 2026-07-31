# BURAQ Smart Attendance v8.0 Enterprise — Phase 1

This build keeps the existing WhatsApp registration, Face AI, GPS, liveness challenge, attendance, employee import, secure settings, backup and Railway support, and adds the first enterprise HR layer.

## New in v8.0 Phase 1
- Multiple HR accounts
- HR Manager, HR Executive, HR Officer and Viewer roles
- Role-based navigation and permissions
- Email + password HR login from the normal login page
- Enable/disable/delete HR accounts
- Last-login tracking
- Security/activity audit log with actor, action, target, details and IP
- Existing Super Admin login remains available by leaving Email blank
- Database migrations are automatic and preserve existing employee/attendance data

## Railway update
Replace the old repository files with this build and redeploy. Keep the existing PostgreSQL service and Railway variables.

Required stable variables:
- `SESSION_SECRET`
- `CONFIG_ENCRYPTION_KEY`

Office variables:
- `OFFICE_LATITUDE=25.18892481916644`
- `OFFICE_LONGITUDE=89.87014577946071`
- `OFFICE_RADIUS_METERS=100`

After login, open **HR Accounts** to create the first HR user. HR passwords must contain at least 8 characters.

## Important production note
The face/liveness system remains the implementation from the preceding build. Before a public launch, validate it with real employee photos, low-light tests, replay/photo-screen attacks, PostgreSQL persistence, WhatsApp webhook retries and Railway memory limits.

## v8.1.1 Unified Login Stable

- One `/login` page for Super Admin, Admin and HR accounts.
- Super Admin default email: `admin@buraq.com` (override once with `SUPER_ADMIN_EMAIL`).
- Fixed the 30-second HR login failure caused by nested SQLite write transactions while saving the audit log.
- HR login, session creation, dashboard redirect and audit logging tested end-to-end.


## v8.1.2 Super Admin Access Control

- Webhook URL and WhatsApp credentials are visible only to the Super Admin.
- Settings, config backup/restore and WhatsApp test messages are Super Admin only.
- User Accounts is hidden from HR/Admin navigation and direct URL access returns 403.
- Only the Super Admin can create, disable or delete Admin/HR accounts.
- Super Admin may create an Admin, HR Manager, HR Executive, HR Officer or Viewer.
- HR users retain attendance/employee permissions according to their role but cannot manage accounts.

## v8.2 Dynamic Permissions

Super Admin can open **User Accounts → Permissions** and choose exactly what each Admin/HR account can see or do. Menus are hidden when access is not granted, and direct URL/API access returns HTTP 403.

Granular permissions include dashboard, employee view/add/edit/delete, Face AI reset, approval view/manage, report view/export, audit logs, general settings, WhatsApp credentials/webhook, and user-account view/manage. Existing accounts keep safe role defaults until the Super Admin saves a custom permission set.
