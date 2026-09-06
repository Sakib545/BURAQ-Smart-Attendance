"""Exercise full-month rates against real rosters and elapsed attendance."""
from datetime import datetime as RealDatetime
import pytest
from app import main
from app.database import get_db, init_db


@pytest.fixture
def employee(monkeypatch):
    init_db()
    class Clock(RealDatetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 10, 12, tzinfo=tz)
    monkeypatch.setattr(main, 'datetime', Clock)
    with get_db() as db:
        db.execute("INSERT INTO employees(staff_id,name,shift,is_active) VALUES(?,?,?,?)",
                   ('RATE-TEST', 'Rate test', 'morning', True))
        eid = db.execute("SELECT id FROM employees WHERE staff_id='RATE-TEST'").fetchone()['id']
        # Future custom duties must be loaded even though attendance stops today.
        for day in range(1, 31):
            date = f'2026-09-{day:02d}'
            db.execute("INSERT INTO custom_duties(employee_id,duty_date,start_time,end_time,break_minutes,is_active) VALUES(?,?,?,?,?,?)",
                       (eid, date, '09:00', '17:00', 0, True))
            if day <= 10:
                db.execute("INSERT INTO attendance(employee_id,work_date,check_in,check_out,status,late_minutes) VALUES(?,?,?,?,?,?)",
                           (eid, date, date+' 09:00:00', date+' 17:00:00', 'present', 0))
    yield eid
    with get_db() as db:
        for table in ('attendance', 'custom_duties', 'duty_schedules', 'leave_requests'):
            db.execute(f'DELETE FROM {table} WHERE employee_id=?', (eid,))
        db.execute('DELETE FROM employees WHERE id=?', (eid,))


def test_midmonth_real_custom_roster(employee):
    result = main._calculate_employee_payroll(employee, '2026-09', 30000, 0)
    assert result['scheduled'] == 9
    assert result['worked'] == 9
    assert result['absent'] == 0  # The next twenty duties are not absences.
    assert result['per_day_salary'] == 1153.85
    assert result['earned_basic_salary'] == result['net_salary'] == 10384.62


def test_late_half_day_and_leave_use_full_month_rate(employee):
    with get_db() as db:
        db.execute("UPDATE attendance SET status='half_day',late_minutes=120 WHERE employee_id=? AND work_date='2026-09-01'", (employee,))
        db.execute("DELETE FROM attendance WHERE employee_id=? AND work_date IN ('2026-09-02','2026-09-03')", (employee,))
        for day, kind in [(2, 'Casual'), (3, 'Unpaid'), (20, 'Casual')]:
            date = f'2026-09-{day:02d}'
            db.execute("INSERT INTO leave_requests(employee_id,leave_type,start_date,end_date,status) VALUES(?,?,?,?,?)",
                       (employee, kind, date, date, 'approved'))
    result = main._calculate_employee_payroll(employee, '2026-09', 30000, 0)
    assert result['paid_leave'] == result['unpaid_leave'] == 1
    assert result['worked'] == 6.5
    assert result['earned_basic_salary'] == 8653.85
    assert result['late_deduction'] == 307.69
    assert result['net_salary'] == 8346.16
    assert result['absent'] == 0.5


def test_future_month_has_rate_but_no_earnings_or_absence(employee, monkeypatch):
    class Clock(RealDatetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 31, 12, tzinfo=tz)
    monkeypatch.setattr(main, 'datetime', Clock)
    result = main._calculate_employee_payroll(employee, '2026-09', 30000, 0)
    assert result['per_day_salary'] == 1153.85
    assert result['scheduled'] == result['worked'] == result['absent'] == 0
    assert result['net_salary'] == result['absent_deduction'] == 0


def test_weekly_roster_and_custom_override_count_each_date_once(employee):
    with get_db() as db:
        for weekday in range(7):
            db.execute("INSERT INTO duty_schedules(employee_id,weekday,start_time,end_time,break_minutes,is_active) VALUES(?,?,?,?,?,?)",
                       (employee, weekday, '08:00', '16:00', 0, True))
    metrics = main._payroll_duty_metrics(employee, '2026-09')
    assert metrics['full_scheduled'] == 26
    assert metrics['scheduled'] == 9
    assert main._calculate_employee_payroll(employee, '2026-09', 30000, 0)['net_salary'] == 10384.62


def test_actual_attendance_needs_no_assignment_and_excludes_night(employee):
    with get_db() as db:
        db.execute('DELETE FROM custom_duties WHERE employee_id=?',(employee,))
        db.execute("UPDATE attendance SET attendance_shift='second' WHERE employee_id=? AND work_date='2026-09-01'",(employee,))
    r=main._calculate_employee_payroll(employee,'2026-09',10000,50,manual_overtime_hours=2,friday_allowance=200,night_allowance=300,bonus=100)
    assert r['salary_divisor']==26 and r['worked']==8
    assert r['earned_basic_salary']==3076.92
    assert r['net_salary']==3776.92


@pytest.mark.parametrize('month,divisor',[('2024-02',25),('2025-02',24),('2026-07',26),('2026-09',26)])
def test_calendar_divisor_handles_month_lengths(employee,month,divisor):
    assert main._payroll_duty_metrics(employee,month)['salary_divisor']==divisor
