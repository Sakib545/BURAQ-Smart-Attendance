from app.payroll import PayrollInput, adjustment_reason_required, calculate_payroll


def test_salary_deducts_absent_and_unpaid_leave_separately():
    result=calculate_payroll(PayrollInput(fixed_salary=26000,scheduled_units=26,worked_units=20,paid_leave_units=2,unpaid_leave_units=1,overtime_hours=5,overtime_rate=100,bonus=500,advance=1000,fine=200,other_deduction=300))
    assert result['per_day_salary']==1000
    assert result['absent']==3
    assert result['absent_deduction']==3000
    assert result['unpaid_leave_deduction']==1000
    assert result['gross_salary']==27000
    assert result['total_deduction']==5500
    assert result['net_salary']==21500


def test_paid_leave_has_no_deduction_and_half_day_is_supported():
    result=calculate_payroll(PayrollInput(fixed_salary=10000,scheduled_units=10,worked_units=8.5,paid_leave_units=1.5))
    assert result['absent']==0
    assert result['absent_deduction']==0
    assert result['net_salary']==10000


def test_adjustments_require_reason():
    assert adjustment_reason_required(1,0,0,0)
    assert adjustment_reason_required(0,1,0,0)
    assert not adjustment_reason_required(0,0,0,0)
