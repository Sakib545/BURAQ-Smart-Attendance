from app.payroll import PayrollInput, adjustment_reason_required, calculate_payroll


def test_salary_is_earned_from_completed_duty():
    result=calculate_payroll(PayrollInput(fixed_salary=26000,scheduled_units=26,worked_units=20,paid_leave_units=2,unpaid_leave_units=1,overtime_hours=5,overtime_rate=100,bonus=500,advance=1000,fine=200,other_deduction=300))
    assert result['per_day_salary']==1000
    assert result['absent']==3
    assert result['absent_deduction']==3000
    assert result['unpaid_leave_deduction']==1000
    assert result['earned_basic_salary']==22000
    assert result['gross_salary']==23000
    assert result['total_deduction']==1500
    assert result['net_salary']==21500


def test_paid_leave_has_no_deduction_and_half_day_is_supported():
    result=calculate_payroll(PayrollInput(fixed_salary=10000,scheduled_units=10,worked_units=8.5,paid_leave_units=1.5))
    assert result['absent']==0
    assert result['absent_deduction']==0
    assert result['net_salary']==10000


def test_no_duty_means_no_fixed_salary_payment():
    result=calculate_payroll(PayrollInput(fixed_salary=15000,scheduled_units=0,worked_units=0))
    assert result['absent_deduction']==15000
    assert result['earned_basic_salary']==0
    assert result['total_deduction']==0
    assert result['net_salary']==0


def test_late_minutes_are_deducted_from_earned_salary():
    result=calculate_payroll(PayrollInput(
        fixed_salary=26000, scheduled_units=26, worked_units=26,
        late_minutes=15, late_deduction=35.71, payable_duty_minutes=10920,
    ))
    assert result['earned_basic_salary']==26000
    assert result['late_minutes']==15
    assert result['late_deduction']==35.71
    assert result['net_salary']==25964.29


def test_adjustments_require_reason():
    assert adjustment_reason_required(1,0,0,0)
    assert adjustment_reason_required(0,1,0,0)
    assert not adjustment_reason_required(0,0,0,0)
