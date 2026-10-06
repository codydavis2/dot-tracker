from datetime import date, timedelta

import pytest

from app.helpers import add_months, generate_reminder_dates, reminder_status, usd


@pytest.mark.parametrize("start, months, expected", [
    (date(2027, 1, 15), 1, date(2027, 2, 15)),
    (date(2027, 1, 31), 1, date(2027, 2, 28)),   # clamp to short month
    (date(2028, 1, 31), 1, date(2028, 2, 29)),   # leap year
    (date(2027, 3, 31), 1, date(2027, 4, 30)),
    (date(2027, 12, 31), 2, date(2028, 2, 29)),  # crosses year end into leap Feb
    (date(2027, 11, 15), 3, date(2028, 2, 15)),
    (date(2028, 2, 29), 12, date(2029, 2, 28)),
    (date(2027, 1, 31), 0, date(2027, 1, 31)),
])
def test_add_months(start, months, expected):
    assert add_months(start, months) == expected


def test_weekly_dates():
    dates = generate_reminder_dates(date(2027, 1, 1), "weekly")
    assert dates[:3] == [date(2027, 1, 1), date(2027, 1, 8), date(2027, 1, 15)]
    assert all(b - a == timedelta(weeks=1) for a, b in zip(dates, dates[1:]))
    assert dates[-1] <= date(2029, 1, 1) < dates[-1] + timedelta(weeks=1)


def test_monthly_dates_do_not_drift_after_clamping():
    dates = generate_reminder_dates(date(2027, 1, 31), "monthly")
    assert dates[:5] == [date(2027, 1, 31), date(2027, 2, 28), date(2027, 3, 31),
                         date(2027, 4, 30), date(2027, 5, 31)]
    assert len(dates) == 25
    assert dates[-1] == date(2029, 1, 31)


def test_yearly_dates():
    assert generate_reminder_dates(date(2028, 2, 29), "yearly") == [
        date(2028, 2, 29), date(2029, 2, 28), date(2030, 2, 28),
    ]


def test_custom_days():
    dates = generate_reminder_dates(date(2027, 1, 1), "custom", 10, "days")
    assert dates[:3] == [date(2027, 1, 1), date(2027, 1, 11), date(2027, 1, 21)]
    assert dates[-1] <= date(2029, 1, 1)


def test_custom_weeks():
    dates = generate_reminder_dates(date(2027, 1, 1), "custom", 2, "weeks")
    assert dates[:3] == [date(2027, 1, 1), date(2027, 1, 15), date(2027, 1, 29)]


def test_custom_months():
    dates = generate_reminder_dates(date(2027, 1, 31), "custom", 3, "months")
    assert dates == [date(2027, 1, 31), date(2027, 4, 30), date(2027, 7, 31), date(2027, 10, 31),
                     date(2028, 1, 31), date(2028, 4, 30), date(2028, 7, 31), date(2028, 10, 31),
                     date(2029, 1, 31)]


def test_span_years():
    assert len(generate_reminder_dates(date(2027, 1, 1), "monthly", span_years=1)) == 13


def test_unknown_interval_raises():
    with pytest.raises(ValueError):
        generate_reminder_dates(date(2027, 1, 1), "fortnightly")


def test_reminder_status_buckets():
    today = date.today()
    assert reminder_status((today - timedelta(days=1)).isoformat()) == "overdue"
    assert reminder_status(today.isoformat()) == "due_soon"
    assert reminder_status((today + timedelta(days=14)).isoformat()) == "due_soon"
    assert reminder_status((today + timedelta(days=15)).isoformat()) == "upcoming"


def test_usd():
    assert usd(None) == "—"
    assert usd(1234.5) == "$1,234.50"
