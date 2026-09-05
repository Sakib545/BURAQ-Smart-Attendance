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


def test_midmonth_divisor_does_not_overpay():
    # Prepared on the 10th of a 30-day month: only 10 days elapsed, all worked.
    # The daily rate must divide the fixed salary over the FULL month (30), not
    # over the 10 days elapsed, so the employee earns 10/30 of the salary.
    result=calculate_payroll(PayrollInput(
        fixed_salary=30000, scheduled_units=10, salary_divisor_units=30, worked_units=10,
    ))
    assert result['per_day_salary']==1000
    assert result['earned_basic_salary']==10000
    assert result['net_salary']==10000


def test_full_attendance_reconciles_exactly_without_rounding_drift():
    # A full month of perfect attendance must pay exactly the fixed salary even
    # when fixed/scheduled is not a clean number (10000/30 = 333.33...).
    result=calculate_payroll(PayrollInput(
        fixed_salary=10000, scheduled_units=30, salary_divisor_units=30, worked_units=30,
    ))
    assert result['earned_basic_salary']==10000
    assert result['net_salary']==10000


def test_divisor_defaults_to_scheduled_for_old_callers():
    # Callers that do not pass salary_divisor_units keep the previous behaviour.
    without=calculate_payroll(PayrollInput(fixed_salary=30000, scheduled_units=30, worked_units=27))
    withfull=calculate_payroll(PayrollInput(fixed_salary=30000, scheduled_units=30, salary_divisor_units=30, worked_units=27))
    assert without['net_salary']==withfull['net_salary']==27000


def test_partial_duty_month_pays_pro_rata_not_full_salary():
    # Someone assigned only 7 duty days in a 26-day standard month must earn
    # 7/26 of the salary for working all 7 — not the whole month's pay.
    result=calculate_payroll(PayrollInput(
        fixed_salary=26000, scheduled_units=7, salary_divisor_units=26, worked_units=7,
    ))
    assert result['per_day_salary']==1000
    assert result['absent']==0
    assert result['earned_basic_salary']==7000
    assert result['net_salary']==7000


def test_part_time_pattern_still_earns_its_full_salary():
    # A genuine part-timer whose weekly pattern gives 8 duty days a month has a
    # divisor of 8, so working all 8 pays the agreed salary in full.
    result=calculate_payroll(PayrollInput(
        fixed_salary=8000, scheduled_units=8, salary_divisor_units=8, worked_units=8,
    ))
    assert result['earned_basic_salary']==8000
    assert result['net_salary']==8000


def test_extra_assigned_days_cannot_exceed_the_fixed_salary():
    # Custom duties can add days beyond the standard month; basic pay is still
    # capped at the fixed salary (anything beyond belongs in overtime).
    result=calculate_payroll(PayrollInput(
        fixed_salary=26000, scheduled_units=30, salary_divisor_units=26, worked_units=30,
    ))
    assert result['earned_basic_salary']==26000


def test_allowances_are_added_to_gross_pay():
    # Reproduces a real row from the manual salary sheet:
    # basic 10500 + night 1890 + Friday 1400 = 13790 gross.
    result=calculate_payroll(PayrollInput(
        fixed_salary=10500, scheduled_units=31, salary_divisor_units=31, worked_units=31,
        night_allowance=1890, friday_allowance=1400,
    ))
    assert result['earned_basic_salary']==10500
    assert result['total_allowance']==3290
    assert result['gross_salary']==13790
    assert result['net_salary']==13790


def test_allowances_survive_deductions_and_eid_duty():
    result=calculate_payroll(PayrollInput(
        fixed_salary=10500, scheduled_units=31, salary_divisor_units=31, worked_units=31,
        night_allowance=1890, friday_allowance=1050, eid_duty_allowance=700,
        advance=500, fine=200,
    ))
    assert result['eid_duty_allowance']==700
    assert result['gross_salary']==14140
    assert result['total_deduction']==700
    assert result['net_salary']==13440


def test_allowances_default_to_zero_for_existing_payslips():
    result=calculate_payroll(PayrollInput(fixed_salary=26000, scheduled_units=26,
                                          salary_divisor_units=26, worked_units=26))
    assert result['total_allowance']==0
    assert result['gross_salary']==26000


def test_absences_are_not_free_when_the_roster_exceeds_the_divisor():
    # Earned pay is capped at the fixed salary, so a divisor smaller than the
    # roster silently absorbs the first few absences. With 31 rostered days the
    # divisor must be 31, never the 26-day fallback.
    full=calculate_payroll(PayrollInput(fixed_salary=10000, scheduled_units=31,
                                        salary_divisor_units=31, worked_units=31))
    short=calculate_payroll(PayrollInput(fixed_salary=10000, scheduled_units=31,
                                         salary_divisor_units=31, worked_units=27))
    assert full['net_salary']==10000
    assert short['net_salary'] < full['net_salary']
    assert short['net_salary']==round(10000*27/31, 2)
