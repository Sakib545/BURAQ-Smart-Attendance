"""Completed extra duty payments, distinct from regular attendance."""
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from app.config import settings
from app.database import get_db

KINDS = {'night': 'Night Extra', 'friday': 'Friday Extra', 'eid': 'Eid', 'other': 'Other'}


def interval(day, start, end):
    start_at = datetime.fromisoformat(f'{day}T{start}')
    end_at = datetime.fromisoformat(f'{day}T{end}')
    if end_at <= start_at:
        end_at += timedelta(days=1)
    return start_at, end_at


def overlaps(left, right):
    return left[0] < right[1] and right[0] < left[1]


def lock_employee(c, employee_id):
    # Serializes payment mutations for an employee on PostgreSQL and SQLite.
    changed = c.execute('UPDATE employees SET id=id WHERE id=?', (employee_id,))
    if changed.result.rowcount != 1:
        raise ValueError('Employee not found')


def ensure_unlocked(c, employee_id, month):
    row = c.execute('SELECT payment_status FROM payroll_records WHERE employee_id=? AND salary_month=?',
                    (employee_id, month)).fetchone()
    if row and row['payment_status'] in {'finalized', 'paid'}:
        raise ValueError('Payroll is locked. Reopen it before changing special duty.')


def regular_conflict(c, employee_id, duty_date, start, end):
    target = interval(duty_date, start, end)
    day = date.fromisoformat(duty_date) - timedelta(days=1)
    # Includes neighbouring work dates so overnight duty cannot be paid twice.
    while day <= target[1].date():
        duty = c.execute('SELECT start_time,end_time FROM custom_duties WHERE employee_id=? AND duty_date=? AND is_active',
                         (employee_id, day.isoformat())).fetchone()
        if not duty:
            duty = c.execute('SELECT start_time,end_time FROM duty_schedules WHERE employee_id=? AND weekday=? AND is_active',
                             (employee_id, day.weekday())).fetchone()
        if duty and overlaps(target, interval(day.isoformat(), duty['start_time'], duty['end_time'])):
            return True
        day += timedelta(days=1)
    return False


def add_completed(employee_id, duty_date, kind, start, end, amount, note, actor):
    if kind not in KINDS:
        raise ValueError('Invalid special duty type')
    try:
        parsed = date.fromisoformat(duty_date)
        if parsed.isoformat() != duty_date or len(start) != 5 or len(end) != 5:
            raise ValueError()
        times = interval(duty_date, start, end)
        value = Decimal(str(amount))
        if not value.is_finite() or value <= 0 or value > 10000000:
            raise ValueError()
        value = value.quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)
    except (ValueError, InvalidOperation):
        raise ValueError('Enter a valid date, time and positive amount')
    if times[1] > datetime.now(ZoneInfo(settings.timezone)).replace(tzinfo=None):
        raise ValueError('Only completed special duty can be recorded')
    if not 5 <= len(note.strip()) <= 1000:
        raise ValueError('Enter a completion note of 5–1000 characters')
    with get_db() as c:
        lock_employee(c, employee_id)
        ensure_unlocked(c, employee_id, duty_date[:7])
        employee = c.execute('SELECT join_date FROM employees WHERE id=?', (employee_id,)).fetchone()
        if employee['join_date'] and duty_date < str(employee['join_date'])[:10]:
            raise ValueError('Duty cannot be before the joining date')
        if regular_conflict(c, employee_id, duty_date, start, end):
            raise ValueError('This time overlaps regular duty, which is already covered by Basic Salary')
        existing = c.execute('SELECT * FROM special_duties WHERE employee_id=? AND cancelled_at IS NULL', (employee_id,)).fetchall()
        for row in existing:
            if (row['duty_date'] == duty_date and row['duty_type'] == kind) or overlaps(times, interval(row['duty_date'], row['start_time'], row['end_time'])):
                raise ValueError('Duplicate or overlapping special duty')
        row = c.execute('INSERT INTO special_duties(employee_id,duty_date,duty_type,start_time,end_time,payment_amount,note,created_by) VALUES(?,?,?,?,?,?,?,?) RETURNING id',
                        (employee_id, duty_date, kind, start, end, float(value), note.strip(), actor)).fetchone()
        return row['id']


def cancel(record_id, reason, actor):
    if not 5 <= len(reason.strip()) <= 1000:
        raise ValueError('A cancellation reason of 5–1000 characters is required')
    with get_db() as c:
        row = c.execute('SELECT * FROM special_duties WHERE id=?', (record_id,)).fetchone()
        if not row:
            raise ValueError('Special duty not found')
        lock_employee(c, row['employee_id'])
        ensure_unlocked(c, row['employee_id'], row['duty_date'][:7])
        changed = c.execute('UPDATE special_duties SET cancelled_at=CURRENT_TIMESTAMP,cancelled_by=?,cancel_reason=? WHERE id=? AND cancelled_at IS NULL',
                            (actor, reason.strip(), record_id))
        if changed.result.rowcount != 1:
            raise ValueError('Special duty was already cancelled')
        return row['duty_date'][:7]


def summary(employee_id, month, connection=None):
    if connection is None:
        with get_db() as c:
            return summary(employee_id, month, c)
    c = connection
    rows = c.execute('SELECT * FROM special_duties WHERE employee_id=? AND duty_date LIKE ? AND cancelled_at IS NULL ORDER BY duty_date,id',
                     (employee_id, month + '-%')).fetchall()
    totals = {kind: Decimal('0') for kind in KINDS}
    records, errors = [], []
    for row in rows:
        item = dict(row)
        item['payment_amount'] = float(item['payment_amount'])
        if regular_conflict(c, employee_id, row['duty_date'], row['start_time'], row['end_time']):
            errors.append(f"Special duty #{row['id']} overlaps regular duty; correct the records")
        else:
            totals[row['duty_type']] += Decimal(str(row['payment_amount']))
        records.append({key: item[key] for key in ('id','duty_date','duty_type','start_time','end_time','payment_amount','note','created_by')})
    return {'totals': {k: float(v) for k,v in totals.items()}, 'records': records, 'errors': errors}


def snapshot_blockers(row, snapshot, connection=None):
    """Avoid finalizing a stale payslip after duty payments are changed."""
    row = dict(row)
    if not row.get('employee_id') or not row.get('salary_month'):
        return []
    if snapshot.get('payroll_rule_version') == 3:
        first = date.fromisoformat(row['salary_month'] + '-01')
        last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        return ['This draft was calculated before month end. Save/recalculate it first.'] if snapshot.get('calculated_through', '') < last.isoformat() else []
    if snapshot.get('payroll_rule_version') == 2:
        return ['Payroll rules changed. Review manual extras and save this draft again.']
    state = summary(row['employee_id'], row['salary_month'], connection)
    errors = list(state['errors'])
    if snapshot.get('special_duty_records', []) != state['records']:
        errors.append('Special duty changed. Save/recalculate this draft before finalizing.')
    if snapshot.get('payroll_rule_version') == 2:
        # Compare to the actual month end, including February.
        first = date.fromisoformat(row['salary_month'] + '-01')
        last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        if snapshot.get('calculated_through', '') < last.isoformat():
            errors.append('This draft was calculated before month end. Save/recalculate it first.')
    return errors
