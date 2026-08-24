# BURAQ Smart Attendance v9.26.0

This is a data-safe overlay for the existing BURAQ Smart Attendance project.
Copy the included `app/` and `tests/` paths over the same paths in the current
repository. Do not replace the database and do not create a separate project.

## Included changes

- WhatsApp guided leave requests (`leave`, `ছুটি`, `my leave`)
- Casual, Sick, Annual and Unpaid leave types
- ISO, DD/MM/YYYY, Bengali-digit, today and tomorrow date parsing
- Date-range, 30-day past, 60-day maximum and overlap validation
- HR dashboard approval/rejection with WhatsApp decision notice
- Monthly performance score: Attendance 50, Punctuality 35, Checkout 15
- Approved leave is not penalized; fewer than 10 scheduled days is ineligible
- Manual-only `/performance-awards` preview and send flow
- Atomic duplicate-send protection; failed WhatsApp sends remain retryable
- Correct Second Shift detection cutoff: 4:00 PM

## Deployment

1. Back up the production database.
2. Copy the files while keeping the `app/` and `tests/` directories.
3. Commit and push to the Railway-connected branch.
4. Wait for Railway health check to pass.
5. Verify `/ready`, then test `leave` from one approved employee number.
6. Open `/performance-awards` as HR/Admin and preview a past month. Nothing is
   sent until HR presses the send button.

The startup migration only creates missing structures and marks the release;
it does not drop, truncate or replace employee, attendance, duty or payroll
data.

Suggested commit (under 50 characters):

`feat: add leave and performance flows`
