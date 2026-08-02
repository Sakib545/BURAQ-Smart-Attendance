from app.time_format import format_time_12h


def test_duty_time_is_friendly_12_hour_format():
    assert format_time_12h("23:00") == "11:00 PM"
    assert format_time_12h("01:00") == "1:00 AM"
    assert format_time_12h("12:30") == "12:30 PM"
