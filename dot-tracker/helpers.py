from datetime import date, datetime
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


def usd(value):
    """Format value as USD, for Jinja filter use."""
    if value is None:
        return "—"
    return f"${value:,.2f}"
