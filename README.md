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


## v8.3
Attendance reports (CSV/Excel/PDF), leave approvals, attendance corrections, shift and department management with dynamic permissions.

## v9.0 Production Foundation

This release hardens the existing v8.3 application without deleting existing employee, attendance, HR, permission, WhatsApp, GPS or Face AI data.

### Added
- Strict production configuration validation.
- PostgreSQL failure now stops deployment instead of silently using temporary storage.
- Temporary SQLite fallback is available only when `ALLOW_TEMP_DB_FALLBACK=true` is explicitly set.
- Separate `/health` liveness and `/ready` database-readiness endpoints.
- Request IDs, response timing, structured request logs and safe 500 responses.
- Security response headers.
- Schema migration bookkeeping through `schema_migrations`.
- Automated smoke tests for startup, health, readiness and login page.

### Required Railway variables
```text
ENVIRONMENT=production
DATABASE_URL=<Railway PostgreSQL reference>
SESSION_SECRET=<at least 32 random characters>
CONFIG_ENCRYPTION_KEY=<at least 32 random characters>
ALLOW_TEMP_DB_FALLBACK=false
REQUIRE_SECURE_SECRETS=true
```

Keep the existing WhatsApp and office-location variables unchanged.

### Test locally
```bash
pip install -r requirements-dev.txt
ENVIRONMENT=development REQUIRE_SECURE_SECRETS=false pytest -q
```

## v9.3 Enterprise Control Center

- Commercial HRMS-style control center
- Live KPI cards: present, late, absent, leave and overtime
- Seven-day attendance trend chart without external JavaScript dependencies
- Live employee attendance timeline
- Pending registration, leave and correction workload
- Permission-aware quick actions
- Workforce registration and attendance progress
- Production service health panel
- Fully responsive desktop, tablet and mobile layout

Existing employee, attendance, HR, permission and WhatsApp data are preserved.


## v9.3 Enterprise Employee Center
- Employee 360-degree profile
- Attendance calendar and timeline
- Advanced search and filters
- Bulk shift, department and status actions
- Emergency contact and reporting manager
- Private HR notes with audit trail
- Documents intentionally excluded from this release
- Existing PostgreSQL/SQLite data is migrated automatically


## v9.3 Simple Performance Review
- Employee summary card with attendance, late, overtime and latest rating
- Profile tabs for Profile, Attendance, Leave, Performance and Activity
- 1–5 ratings across six practical categories
- HR comments, goals and review history
- Dynamic `performance_view` and `performance_manage` permissions
- No Documents module

## v9.4 Face Detection Accuracy Hotfix

- Fixed a YuNet issue where one selfie could be reported as 2–4 faces.
- Added landmark validation and non-maximum suppression for overlapping detections.
- Tiny background faces/posters no longer reject a close single-person selfie.
- A real second person is still rejected when the second face is confidently detected and materially sized.
- Duplicate registration selfies are rejected; employees must provide genuinely different live angles.
- The same face cannot be registered under another employee profile without HR/Admin intervention.

## v9.5 Duplicate Selfie Intelligence

- `attendance_fingerprints` stores pHash, aHash, dHash, face embedding, pose and normalized landmark signatures.
- Weighted duplicate engine returns Accept, Pending or Reject without replacing GPS, liveness or face verification.
- Admin/HR Duplicate Analysis page shows component scores, the matched fingerprint and review controls.
- Existing SQLite and PostgreSQL databases upgrade automatically; `python scripts/migrate_v9_5.py` is also available.
- Thresholds are configurable with `DUPLICATE_ACCEPT_BELOW` (0.70), `DUPLICATE_REJECT_AT` (0.90), and the four `DUPLICATE_*_WEIGHT` variables.
- Pending analysis does not create attendance automatically; approval records the security review and the employee then retries with a fresh live selfie.

## v9.6 Private HR/Admin Payroll

- HR/Admin manually enters fixed salary, overtime hours/rate, bonus, deduction and private notes.
- Net salary and overtime amount are calculated automatically.
- Monthly paid/unpaid tracking, individual PDF payslips, monthly PDF report and styled Excel export.
- `payroll_view`, `payroll_manage` and `payroll_export` permissions protect every page and download.
- Employees receive no salary command, menu item or payroll access; their attendance experience is unchanged.
- v9.6.1 fixes active-employee filtering on Railway PostgreSQL for Payroll and Performance pages.
- v9.6.2 fixes the missing regular-expression import used by Payroll month validation.
- v9.7 adds a polished confidential Payroll section inside each Employee 360 profile with inline salary create/edit, month summary, history, payment controls and payslip download.

## v9.8 Performance & Railway Optimization

- Face AI models are cached per worker thread instead of being recreated for every selfie.
- Permission lookups are cached for each HTTP request, removing repeated database queries from complex pages.
- Dashboard attendance metrics and seven-day trend use grouped aggregate queries.
- Duplicate detection checks indexed exact hashes plus a bounded recent comparison set.
- New indexes accelerate face samples, attendance, approvals, leave, corrections, performance and duplicate fingerprints.
- Existing data, permissions, attendance behavior and payroll calculations remain unchanged.

## v9.9 Zero-Touch Duty & Registration

- Known employee numbers receive the interactive attendance menu immediately after `Hi`.
- Unknown numbers are asked only for Staff ID; mismatched numbers go directly to Admin Pending Approval without YES/CANCEL typing.
- HR/Admin weekly duty roster with employee, weekday, start/end time and office.
- Automatic WhatsApp utility-template reminders 30 minutes before duty, 10 minutes after a missed check-in and 10 minutes before checkout.
- Employee `My Duty` button shows the next seven days without typing.
- Database-backed reminder logs prevent repeated messages and provide an Admin audit view.
- v9.9.1 adds one-day Custom Duty assignments; a custom date/time/office overrides that employee's weekly duty and reminder for the selected date.
- v9.9.2 fixes sidebar account-card overlap when the navigation menu is taller than the viewport.

## v9.10 Employee Duty Control

- A Duty button now sits beside Profile and Reset Face in Employee Center.
- Employee-specific Duty page supports selectable regular weekly, custom date, Friday and night assignments.
- Morning, evening and night presets remain fully overridable with selectable start/end time and office.
- Night duty can be one-time or weekly; an end time earlier than start is treated as next day.
- Attendance checkout and WhatsApp checkout reminders correctly follow overnight duties across midnight.
