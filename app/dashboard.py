import calendar as calendar_module
from datetime import date

from flask import Blueprint, g, render_template, request

from .db import get_db
from .helpers import reminder_status

bp = Blueprint("dashboard", __name__)


@bp.route("/")
def index():
    db = get_db()
    company_id = g.user["company_id"]

    vehicles_all = db.execute(
        "SELECT * FROM vehicles WHERE company_id = %s AND archived_at IS NULL ORDER BY id",
        (company_id,),
    ).fetchall()
    vehicles = vehicles_all[:10]
    more_vehicles = len(vehicles_all) > 10

    # Pull all open reminders for this company's vehicles, for the DOT compliance warning banner
    reminders = db.execute(
        """
        SELECT inspection_reminders.*, vehicles.unit_number, vehicles.make, vehicles.model, vehicles.year
        FROM inspection_reminders
        JOIN vehicles ON vehicles.id = inspection_reminders.vehicle_id
        WHERE inspection_reminders.company_id = %s AND inspection_reminders.status != 'completed'
        ORDER BY inspection_reminders.due_date ASC
        """,
        (company_id,),
    ).fetchall()

    # Attach a status bucket (overdue / due_soon / upcoming) for template coloring
    reminders_with_status = [
        {**r, "bucket": reminder_status(r["due_date"])} for r in reminders
    ]

    # Any DOT vehicle with an overdue annual inspection gets a hard warning
    dot_warnings = [
        r for r in reminders_with_status
        if r["reminder_type"] == "dot_annual" and r["bucket"] == "overdue"
    ]

    today = date.today()

    open_work_order_rows = db.execute(
        """
        SELECT work_orders.*, vehicles.unit_number, vehicles.make, vehicles.model, vehicles.year,
               assigned_to.full_name AS assigned_to_name
        FROM work_orders
        JOIN vehicles ON vehicles.id = work_orders.vehicle_id
        LEFT JOIN users AS assigned_to ON assigned_to.id = work_orders.assigned_to_user_id
        WHERE work_orders.company_id = %s AND work_orders.deleted_at IS NULL AND work_orders.status = 'open'
        ORDER BY
            CASE WHEN work_orders.scheduled_completion_date IS NULL THEN 1 ELSE 0 END,
            work_orders.scheduled_completion_date ASC
        """,
        (company_id,),
    ).fetchall()
    open_work_orders_all = [
        {
            **w,
            "overdue": bool(
                w["scheduled_completion_date"] and w["scheduled_completion_date"] < today
            ),
        }
        for w in open_work_order_rows
    ]
    open_work_orders = open_work_orders_all[:5]
    more_open_work_orders = len(open_work_orders_all) > 5

    try:
        year = int(request.args.get("year", today.year))
        month = int(request.args.get("month", today.month))
    except ValueError:
        year, month = today.year, today.month
    # Normalize out-of-range month (e.g. from hand-edited URLs) instead of erroring
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1

    first_day = date(year, month, 1)
    last_day = date(year, month, calendar_module.monthrange(year, month)[1])

    calendar_reminders = db.execute(
        """
        SELECT inspection_reminders.*, vehicles.unit_number, vehicles.make, vehicles.model, vehicles.year
        FROM inspection_reminders
        JOIN vehicles ON vehicles.id = inspection_reminders.vehicle_id
        WHERE inspection_reminders.company_id = %s AND inspection_reminders.due_date BETWEEN %s AND %s
        """,
        (company_id, first_day, last_day),
    ).fetchall()

    calendar_work_orders = db.execute(
        """
        SELECT work_orders.*, vehicles.unit_number, vehicles.make, vehicles.model, vehicles.year
        FROM work_orders
        JOIN vehicles ON vehicles.id = work_orders.vehicle_id
        WHERE work_orders.company_id = %s AND work_orders.deleted_at IS NULL
            AND work_orders.scheduled_completion_date BETWEEN %s AND %s
        """,
        (company_id, first_day, last_day),
    ).fetchall()

    events_by_day = {}
    for r in calendar_reminders:
        day_num = r["due_date"].day
        vehicle_label = f"Unit {r['unit_number']}" if r["unit_number"] else f"{r['year']} {r['make']} {r['model']}"
        bucket = reminder_status(r["due_date"])
        css_class = "bg-success" if r["status"] == "completed" else (
            "bg-danger" if bucket == "overdue" else "bg-warning text-dark" if bucket == "due_soon" else "bg-info text-dark"
        )
        events_by_day.setdefault(day_num, []).append({
            "label": f"{vehicle_label}: {r['reminder_type'].replace('_', ' ').title()}",
            "css_class": css_class,
            "url": f"/vehicles/{r['vehicle_id']}",
        })

    for w in calendar_work_orders:
        day_num = w["scheduled_completion_date"].day
        vehicle_label = f"Unit {w['unit_number']}" if w["unit_number"] else f"{w['year']} {w['make']} {w['model']}"
        css_class = "bg-warning text-dark" if w["status"] == "open" else "bg-secondary"
        events_by_day.setdefault(day_num, []).append({
            "label": f"{vehicle_label}: W/O #{w['id']}",
            "css_class": css_class,
            "url": f"/work-orders/{w['id']}",
        })

    month_days = calendar_module.Calendar(firstweekday=6).monthdayscalendar(year, month)

    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)

    return render_template(
        "dashboard.html",
        vehicles=vehicles,
        more_vehicles=more_vehicles,
        dot_warnings=dot_warnings,
        open_work_orders=open_work_orders,
        more_open_work_orders=more_open_work_orders,
        month_days=month_days,
        events_by_day=events_by_day,
        month_label=first_day.strftime("%B %Y"),
        year=year,
        month=month,
        today=today,
        prev_year=prev_year,
        prev_month=prev_month,
        next_year=next_year,
        next_month=next_month,
    )
