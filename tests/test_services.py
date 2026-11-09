from datetime import date

from app.services import count_working_days


def test_single_weekday():
    assert count_working_days(date(2026, 11, 9), date(2026, 11, 9)) == 1


def test_full_week_with_weekend():
    assert count_working_days(date(2026, 11, 9), date(2026, 11, 15)) == 5


def test_weekend_only():
    assert count_working_days(date(2026, 11, 14), date(2026, 11, 15)) == 0
