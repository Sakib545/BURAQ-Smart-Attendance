## v9.24.0 Configurable Shifts & Manual Overtime

- Default First Shift **08:30 AM–04:00 PM**, Default Second Shift **04:00 PM–10:00 AM**, Second Shift auto-detection cutoff **03:00 PM**।
- `/duty` page-এ নতুন **Shift Rules** card থেকে Admin/HR প্রতিটি shift সময়, detection cutoff এবং late-grace minutes (default `0`) নিজে পরিবর্তন করতে পারবেন।
- সব rule বিদ্যমান `system_settings` table-এ স্থায়ীভাবে সংরক্ষিত হয়; Admin আবার না বদলানো পর্যন্ত পরের মাসগুলোতেও একই নিয়ম চালু থাকে।
- Duty priority অপরিবর্তিত এবং পরিষ্কার: **Employee custom duty → Employee weekly duty → Global Shift Rules**। এক employee-এর duty save করলে অন্য কারো duty বদলায় না।
- Late minutes প্রযোজ্য duty start থেকে গণনা হয় এবং configured late-grace শেষ হওয়ার পরেই record হয়। সব হিসাব `Asia/Dhaka` timezone-এ, dashboard-এ 12-hour format-এ।
- **Overtime এখন সম্পূর্ণ manual**: দেরিতে Check-out করলেও কোনো automatic overtime তৈরি হয় না; আসল check-in/check-out সময় অপরিবর্তিত থাকে। HR/Admin Payroll page-এ manually overtime hours ও rate দেবেন এবং Payroll, payslip, PDF ও XLSX শুধু সেই manual overtime দেখায়।
- Break minutes weekly ও custom duty-তে আগের মতোই configurable; salary break বাদ দিয়ে payable duty time থেকেই হিসাব হয়।
- Basic Salary HR না বদলানো পর্যন্ত স্থায়ী থাকে; duty সম্পূর্ণ না হলে full Basic Salary স্বয়ংক্রিয়ভাবে যোগ হয় না। Payroll শুধু authorized HR/Admin দেখতে ও edit করতে পারেন।
- Migration সম্পূর্ণ idempotent: কোনো table drop/truncate/recreate হয়নি; employees, attendance, duty, custom duty, face data, settings, payroll ও WhatsApp data অক্ষত থাকে।

## v9.23.0 Automatic Attendance Shift

- Check-in সম্পন্ন না হলে Check-out Location বা Selfie flow শুরু হবে না।
- সকাল/দুপুরের Check-in স্বয়ংক্রিয়ভাবে First Shift এবং 3:00 PM থেকে Second Shift হিসেবে সংরক্ষিত হবে।
- `SECOND_SHIFT_FROM` Railway variable দিয়ে Second Shift cutoff পরিবর্তন করা যাবে (default `15:00`)।
- Dashboard, WhatsApp report এবং attendance exports-এ attendance-এর আসল shift দেখা যাবে।
- পুরোনো database-এ `attendance_shift` column নিরাপদ migration-এর মাধ্যমে নিজে থেকে যোগ হবে।

## v9.22.2 Dashboard Selfie Approval Fix

- Selfie approval এখন attendance, evidence ও fingerprint status একই atomic database transaction-এ final করে।
- PostgreSQL-এ একই selfie একসঙ্গে দুইবার approve হওয়া row lock দিয়ে বন্ধ করা হয়েছে।
- Double-click বা page retry হলে 404 না দেখিয়ে আগের successful approval-ই দেখায়।
- Pending list-এ একই employee-এর Check-in selfie Check-out-এর আগে দেখায়।
- পুরোনো `checkin`/`checkout` action values-ও safely approve হয় এবং failure হলে dashboard-এ পরিষ্কার কারণ দেখা যায়।

## v9.22.1 Hybrid WhatsApp Location

- Check In/Out প্রথমে WhatsApp-এর native `Send Location` button ব্যবহার করে।
- ৪৫ সেকেন্ডের মধ্যে native location না এলে bot database state যাচাই করে শুধু তখনই secure browser fallback link পাঠায়।
- Native location আগে গ্রহণ হলে fallback link পাঠানো হয় না।
- Browser page Location permission চায়, office radius যাচাই করে এবং সফল হলে WhatsApp-এ selfie নির্দেশনা পাঠায়।
- Signed link employee ও Check In/Out action-এর সঙ্গে বাঁধা, ১০ মিনিটে expire হয় এবং location গ্রহণের পর পুনরায় ব্যবহার করা যায় না।
- Employee profile-এর `01...`, `+8801...` এবং Meta webhook-এর `8801...` format একই নম্বর হিসেবে match হয়; ভুল expired message আর দেখায় না।
- Optional: `PUBLIC_BASE_URL=https://smart-attendance.pro` canonical link host নির্ধারণ করে এবং `LOCATION_FALLBACK_DELAY_SECONDS=45` fallback delay নিয়ন্ত্রণ করে।

## v9.18.2 Self-Service Account Settings

- Admin/HR users can change their own name and email.
- Admin/HR users can securely change their own password.
- Current password is required for all account changes.
- Duplicate email addresses are blocked.
- All changes are written to the audit log.
- Existing permissions and system features are unchanged.

## v9.18.1 Dashboard PostgreSQL Hotfix

- Fixed dashboard 500 error on Railway PostgreSQL caused by comparing a Boolean column with integer `1`.
- No feature, database data, permission, attendance, duty, payroll, WhatsApp, or Face AI behavior changed.

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

- HR/Admin manually enters basic salary, overtime hours/rate, bonus, deduction and private notes.
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

## v9.11 Duty-Based Salary Export

- Fixed salary is divided by scheduled duty days to calculate the per-day salary.
- Missed scheduled duties are deducted automatically; approved paid leave is excluded from absence.
- The monthly Excel workbook includes every active employee in one file, with Summary and Salary Sheet tabs.
- Salary Sheet shows scheduled, worked, leave and absent days alongside basic salary, deductions, overtime, bonus, gross and net salary.
- Payroll management and exports remain private to authorized HR/Admin users.
- v9.11.1 renames the monthly PDF to BURAQ Payment Sheet and displays the selected month prominently.
- v9.11.2 sets both Excel tabs to centered A4 landscape, fitting each complete sheet onto one printed page.

## v9.12 Payroll Pro

- Fixed salary and default overtime rate are employee master values: set once and reuse automatically until HR changes them.
- One central payroll engine powers preview, saved records, Excel, monthly PDF and individual payslips.
- Regular/custom/Friday/night duty, half-day attendance, paid leave, unpaid leave, absence and automatic attendance overtime are calculated separately.
- Bonus, advance, fine and other deductions require an adjustment reason; HR can override overtime manually.
- Payroll follows Draft -> Finalized -> Paid. Finalized records are locked, and only Super Admin can reopen an unpaid record with a reason.
- Paid payroll requires payment method and reference; every save, finalize, reopen and payment stores a snapshot audit log.
- Prepare All Employees generates the selected month from persistent salary master values without copying the previous month.
- Finalization blocks records with incomplete checkout dates, and Super Admin can download a full payroll JSON backup.
- Daily payroll backups are written to `BACKUP_DIR` (default `/data/backups`); mount a Railway persistent volume at `/data`.
- Joining and resignation date proration are intentionally not included.

## v9.13 Simple Control Center

- Sidebar is reduced to Dashboard, Employees, Attendance, Payroll and Admin.
- Attendance Center combines reports, exports, duty schedules, leave and attendance corrections.
- Admin Center combines approvals, duplicate review, users, permissions, activity logs, office setup and settings.
- Performance remains inside each Employee Profile, avoiding a duplicate top-level menu.
- Existing feature URLs remain available, while active navigation highlights their new parent section.
- Responsive mobile navigation exposes the same five simple sections.

## v9.14 Disaster Recovery

- A daily full backup includes every database table: employees, face embeddings, attendance, duties, approvals, payroll, users, settings and audit logs.
- Backups use an atomic write, gzip compression and encryption with `BACKUP_ENCRYPTION_KEY` (or `CONFIG_ENCRYPTION_KEY` when a separate key is not set).
- Super Admin can download a `.buraq` backup or restore it from Settings. A safety snapshot is created automatically before every restore.
- The portable format supports PostgreSQL-to-PostgreSQL and SQLite/PostgreSQL migration after the new host creates the current application schema.
- Local retention defaults to 30 backups. Optional S3-compatible upload supports AWS S3, Cloudflare R2, Backblaze B2 and MinIO.
- PostgreSQL serial sequences are repaired after restore, so new records continue normally.

### Required production recovery variables

Keep these values in a password manager outside the hosting provider. Losing the encryption key makes encrypted backups impossible to restore.

```text
BACKUP_DIR=/data/backups
BACKUP_RETENTION_DAYS=30
BACKUP_ENCRYPTION_KEY=<a permanent random value of at least 32 characters>
```

For a genuinely crash-independent copy, configure an S3-compatible bucket:

```text
BACKUP_S3_BUCKET=<bucket name>
BACKUP_S3_ENDPOINT=<provider endpoint; omit for AWS S3>
BACKUP_S3_REGION=auto
BACKUP_S3_ACCESS_KEY_ID=<access key>
BACKUP_S3_SECRET_ACCESS_KEY=<secret key>
BACKUP_S3_PREFIX=buraq-attendance
```

### Move to another hosting provider

1. Deploy the same code and attach PostgreSQL.
2. Copy the original `BACKUP_ENCRYPTION_KEY`, `CONFIG_ENCRYPTION_KEY` and `SESSION_SECRET` values.
3. Set the new `DATABASE_URL` and start the app once so the schema is created.
4. Sign in as Super Admin, open Settings and upload the latest `.buraq` file under Disaster Recovery.
5. Update the Meta webhook callback URL only if the public domain changed.

If the dashboard is unavailable, restore from the command line:

```bash
python scripts/restore_full_backup.py latest.buraq --confirm RESTORE-BURAQ
```

## v9.15 Production Safety Edition

- Every generated backup is immediately decrypted, parsed and row-count verified before it is marked successful.
- Backup creation uses a repeatable-read PostgreSQL transaction for a consistent multi-table snapshot.
- Off-site uploads retry three times and verify the remote object size before reporting success.
- Settings shows encryption, verification, local retention, last local/off-site success and the latest error.
- Super Admin can inspect any `.buraq` file without restoring or changing live data.
- Restore validates required tables and row counts before replacement, then verifies all committed table counts afterward.
- Production validation blocks incomplete S3 credentials or a short backup encryption key.
- PostgreSQL connection and pool timeouts prevent long hangs, while Docker health checks and graceful shutdown improve crash recovery.
- `.env.example` provides one portable variable checklist for Railway or another host.

### v9.15.1 Selfie review notification

- Admin Approve বা Reject করলে employee সঙ্গে সঙ্গে WhatsApp notification পায়।
- Message-এ Check-in/Check-out action, review score এবং পরবর্তী করণীয় থাকে।
- Notification background-এ যায়; temporary send failure হলে সর্বোচ্চ তিনবার retry হয়।
- WhatsApp number না থাকলে approval action বন্ধ হয় না এবং server log-এ কারণ লেখা হয়।

### v9.15.2 Startup fail-safe

- Optional backup/S3 variable ভুল বা অসম্পূর্ণ হলেও attendance app আর health-check failure দিয়ে বন্ধ হবে না।
- ছোট `BACKUP_ENCRYPTION_KEY` উপেক্ষা করে নিরাপদ `CONFIG_ENCRYPTION_KEY` fallback ব্যবহার হয়।
- অসম্পূর্ণ S3 credentials থাকলে শুধু off-site upload বন্ধ থাকে; database, webhook এবং dashboard সচল থাকে।
- Configuration সমস্যা Deploy Logs-এ warning হিসেবে দেখা যায়, fatal startup error হিসেবে নয়।

### v9.15.3 One-time Admin setup

- Admin setup এখন password hash-এর পাশাপাশি permanent completion marker রাখে।
- Existing installations startup-এর সময় automatic marker migration পায়।
- Database সাময়িকভাবে unavailable হলে app আর ভুল করে Initial Setup page দেখায় না।
- Setup marker থাকলেও credential missing হলে new Admin তৈরির বদলে protected recovery error দেখায়।
- `/ready` response-এ `admin_setup_complete` status পাওয়া যায়।
- Super Admin Settings থেকে current password যাচাই করে login email ও password পরিবর্তন করতে পারে।

## v9.17 Easy Duty Management

- Added a dedicated `/duty` page and separate Duty sidebar menu.
- Select all employees or choose individual employees with search.
- Assign duty using Start Date and End Date.
- Sunday–Thursday use one regular schedule.
- Friday has a separate start/end time and note.
- Saturday is kept off automatically.
- Existing duties in the selected range are updated safely.
- Advanced reminder logs and legacy weekly tools remain available at `/duty-schedules`.


## v9.17.1 Duty Hotfix

- Saturday now uses the regular duty schedule.
- Friday remains a separate special duty schedule.
- Select All reliably selects every active employee.
- Clear resets employee selection, search, dates, times, office and notes.
- Preview text and bulk duty creation now match the corrected weekly rules.

## v9.18 Dashboard UI
- Dashboard redesigned to match the approved clean green-and-white layout.
- Added five KPI cards, 7-day trend, workforce readiness, live attendance, pending work, and quick actions.
- Sidebar simplified into direct module links based on permissions.
- Removed non-essential global subtitle text and payroll calculation formula from the visible UI.
- Existing attendance, duty, payroll, permission, WhatsApp, and Face AI logic remains unchanged.
