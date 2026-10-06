import calendar
from datetime import date, datetime, timedelta
from functools import wraps
from flask import redirect, session


def login_required(f):
    """
    Decorate routes to require login.
    Same pattern as CS50 Finance: https://cs50.harvard.edu/x/psets/9/finance/
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get("user_id") is None:
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated_function


def days_until(due_date_str):
    """Return integer days between today and a 'YYYY-MM-DD' date string.
    Negative means overdue."""
    due = datetime.strptime(due_date_str, "%Y-%m-%d").date()
    return (due - date.today()).days


def reminder_status(due_date_str):
    """Bucket a due date into upcoming / due_soon / overdue for UI coloring."""
    delta = days_until(due_date_str)
    if delta < 0:
        return "overdue"       # red
    elif delta <= 14:
        return "due_soon"      # yellow
    else:
        return "upcoming"      # green


def add_months(base_date, months):
    """Add a number of months to a date, clamping the day to the target month's length
    (e.g. Jan 31 + 1 month -> Feb 28/29)."""
    month_index = base_date.month - 1 + months
    year = base_date.year + month_index // 12
    month = month_index % 12 + 1
    day = min(base_date.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _nth_occurrence(start_date, n, interval, custom_value=None, custom_unit=None):
    """Return the nth occurrence (n=0 is start_date itself), computed from start_date
    rather than chained from the previous occurrence, so day-of-month clamping
    (e.g. Jan 31 -> Feb 28) doesn't permanently drift the series to the 28th."""
    if interval == "weekly":
        return start_date + timedelta(weeks=n)
    elif interval == "monthly":
        return add_months(start_date, n)
    elif interval == "yearly":
        return add_months(start_date, 12 * n)
    elif interval == "custom":
        if custom_unit == "days":
            return start_date + timedelta(days=custom_value * n)
        elif custom_unit == "weeks":
            return start_date + timedelta(weeks=custom_value * n)
        elif custom_unit == "months":
            return add_months(start_date, custom_value * n)
    raise ValueError(f"Unrecognized interval: {interval!r}")


def generate_reminder_dates(start_date, interval, custom_value=None, custom_unit=None, span_years=2):
    """Return [start_date, next, next, ...] stepping by the given interval,
    covering span_years out from start_date."""
    end_date = add_months(start_date, 12 * span_years)
    dates = []
    n = 0
    while True:
        occurrence = _nth_occurrence(start_date, n, interval, custom_value, custom_unit)
        if occurrence > end_date:
            break
        dates.append(occurrence)
        n += 1
    return dates


def usd(value):
    """Format value as USD, for Jinja filter use."""
    if value is None:
        return "—"
    return f"${value:,.2f}"
