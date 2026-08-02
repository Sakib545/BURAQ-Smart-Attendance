from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_HALF_UP


TWOPLACES = Decimal("0.01")


def money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class PayrollInput:
    fixed_salary: float = 0
    scheduled_units: float = 0
    worked_units: float = 0
    paid_leave_units: float = 0
    unpaid_leave_units: float = 0
    late_minutes: float = 0
    late_deduction: float = 0
    payable_duty_minutes: float = 0
    overtime_hours: float = 0
    overtime_rate: float = 0
    bonus: float = 0
    advance: float = 0
    fine: float = 0
    other_deduction: float = 0


def calculate_payroll(data: PayrollInput) -> dict:
    values = asdict(data)
    if any(Decimal(str(value or 0)) < 0 for value in values.values()):
        raise ValueError("Payroll values cannot be negative")
    scheduled = Decimal(str(data.scheduled_units or 0))
    worked = min(Decimal(str(data.worked_units or 0)), scheduled)
    paid = min(Decimal(str(data.paid_leave_units or 0)), max(scheduled - worked, Decimal("0")))
    unpaid = min(Decimal(str(data.unpaid_leave_units or 0)), max(scheduled - worked - paid, Decimal("0")))
    absent = max(scheduled - worked - paid - unpaid, Decimal("0"))
    fixed = money(data.fixed_salary)
    per_day = (fixed / scheduled).quantize(TWOPLACES, rounding=ROUND_HALF_UP) if scheduled else Decimal("0.00")
    # A fixed salary is payable against assigned/completed duty. With no duty
    # at all, the employee does not receive the full fixed salary by accident.
    absent_deduction = fixed if scheduled == 0 else (per_day * absent).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    unpaid_leave_deduction = (per_day * unpaid).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    # Basic salary is earned only for completed duties and approved paid leave.
    # Absence/unpaid leave are kept as informational values and are not
    # subtracted a second time from the already reduced earned salary.
    earned_basic = money(per_day * (worked + paid)) if scheduled else Decimal("0.00")
    earned_basic = min(earned_basic, fixed)
    late_deduction = money(data.late_deduction)
    overtime_amount = money(Decimal(str(data.overtime_hours or 0)) * Decimal(str(data.overtime_rate or 0)))
    bonus = money(data.bonus); advance = money(data.advance); fine = money(data.fine); other = money(data.other_deduction)
    gross = money(earned_basic + overtime_amount + bonus)
    total_deduction = money(late_deduction + advance + fine + other)
    net = money(gross - total_deduction)
    return {
        "scheduled": float(scheduled), "worked": float(worked), "paid_leave": float(paid),
        "unpaid_leave": float(unpaid), "absent": float(absent), "fixed_salary": float(fixed),
        "per_day_salary": float(per_day), "absent_deduction": float(absent_deduction),
        "unpaid_leave_deduction": float(unpaid_leave_deduction), "earned_basic_salary": float(earned_basic),
        "late_minutes": float(data.late_minutes or 0), "late_deduction": float(late_deduction),
        "payable_duty_minutes": float(data.payable_duty_minutes or 0),
        "overtime_hours": float(data.overtime_hours or 0),
        "overtime_rate": float(data.overtime_rate or 0), "overtime_amount": float(overtime_amount),
        "bonus": float(bonus), "advance": float(advance), "fine": float(fine),
        "deduction": float(other), "gross_salary": float(gross),
        "total_deduction": float(total_deduction), "net_salary": float(net),
    }


def adjustment_reason_required(bonus: float, advance: float, fine: float, other_deduction: float) -> bool:
    return any(money(value) > 0 for value in (bonus, advance, fine, other_deduction))
