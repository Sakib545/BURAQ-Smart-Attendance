"""Payroll-only interface for completed extra duty; no employee messaging."""
from datetime import datetime
from html import escape
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from app.config import settings
from app.database import get_db
from app import special_duties

router = APIRouter()


def redirect(month, error=''):
    return RedirectResponse('/payroll/special-duties?' + urlencode({'month': month, 'error': error}), 303)


@router.get('/payroll/special-duties')
def special_page(request: Request, month: str='', error: str=''):
    from app.main import require_permission, has_permission, layout
    require_permission(request, 'payroll_view')
    month = month or datetime.now(ZoneInfo(settings.timezone)).strftime('%Y-%m')
    try:
        month = datetime.strptime(month, '%Y-%m').strftime('%Y-%m')
    except ValueError:
        month = datetime.now(ZoneInfo(settings.timezone)).strftime('%Y-%m')
    manage = has_permission(request, 'payroll_manage')
    with get_db() as c:
        employees = c.execute('SELECT id,staff_id,name FROM employees WHERE is_active ORDER BY staff_id').fetchall()
        rows = c.execute('SELECT s.*,e.name,e.staff_id,p.payment_status FROM special_duties s JOIN employees e ON e.id=s.employee_id LEFT JOIN payroll_records p ON p.employee_id=s.employee_id AND p.salary_month=? WHERE s.duty_date LIKE ? ORDER BY s.duty_date,s.id', (month,month+'-%')).fetchall()
    form = ''
    entries=[]
    for row in rows:
        status='Cancelled' if row['cancelled_at'] else 'Recorded'
        action=''
        if manage and not row['cancelled_at'] and row['payment_status'] not in {'finalized','paid'}:
            action=f"""<details><summary>Cancel / correct</summary><form method='post' action='/payroll/special-duties/{row['id']}/cancel'>
            <input type='hidden' name='month' value='{month}'><label>Reason</label><input name='reason' minlength='5' maxlength='1000' required>
            <button class='btn secondary'>Cancel record</button></form><p>After cancellation, add the corrected record. History is retained.</p></details>"""
        notes=escape(row['note'])
        if row['cancelled_at']:
            notes+=f"<br>Cancelled by {escape(row['cancelled_by'])}: {escape(row['cancel_reason'])} ({escape(str(row['cancelled_at']))})"
        entries.append(f"<tr><td>{escape(row['name'])}<br>{escape(row['staff_id'])}</td><td>{row['duty_date']}<br>{row['start_time']}–{row['end_time']}</td><td>{special_duties.KINDS[row['duty_type']]}</td><td>{float(row['payment_amount']):,.2f}</td><td>{status}<br>{notes}<br>By {escape(row['created_by'])} · {escape(str(row['created_at']))}</td><td>{action}</td></tr>")
    body=f"""<div class='card'><h1>Special Duty</h1><a class='btn secondary' href='/payroll?month={month}'>Back to Payroll</a>
    <form method='get'><label>Month</label><input type='month' name='month' value='{month}'><button class='btn'>Open month</button></form>
    <p>Historical records only. New payroll uses manual Friday, Night and special amounts entered on the Payroll form. These records are not automatically added.</p></div>
    {f"<div class='notice bad'>{escape(error)}</div>" if error else ''}{form}
    <div class='card table-scroll'><table><thead><tr><th>Employee</th><th>Duty</th><th>Type</th><th>Amount</th><th>History</th><th>Action</th></tr></thead><tbody>{''.join(entries) or '<tr><td colspan=6>No special duties recorded.</td></tr>'}</tbody></table></div>"""
    return layout('Special Duty',body,request,'payroll')


@router.post('/payroll/special-duties')
def add(request: Request, employee_id: int=Form(...), duty_date: str=Form(...), duty_type: str=Form(...),
        start_time: str=Form(...), end_time: str=Form(...), payment_amount: str=Form(...), note: str=Form(...),
        completed: str=Form(''), month: str=Form('')):
    from app.main import require_permission, _payroll_actor
    require_permission(request,'payroll_manage')
    return redirect(month, 'Enter manual extras on the Payroll form. This ledger is historical.')


@router.post('/payroll/special-duties/{record_id}/cancel')
def cancel(request: Request, record_id: int, reason: str=Form(...), month: str=Form('')):
    from app.main import require_permission, _payroll_actor
    require_permission(request,'payroll_manage')
    try:
        month=special_duties.cancel(record_id,reason,_payroll_actor(request))
    except ValueError as exc:
        return redirect(month,str(exc))
    return redirect(month)
