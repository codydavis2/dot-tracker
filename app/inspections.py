from datetime import date

from flask import Blueprint, abort, flash, g, redirect, render_template, request

from .constants import INSPECTION_CHECKLIST, INSPECTION_STATUS_LABELS
from .db import get_db
from .helpers import generate_reminder_dates, reminder_status
from .people import people_choices, validate_person
from .permissions import can
from .vehicles import find_vehicle_for_form, get_vehicle_or_404, vehicle_choices

bp = Blueprint("inspections", __name__)


@bp.route("/inspections")
def inspections():
    db = get_db()
    company_id = g.user["company_id"]

    # Scheduled-inspection reminders are shop data: only for roles that can work
    # them (not drivers), and not even queried otherwise.
    show_reminders = can(g.user["role"], "inspections.complete_reminder")
    rows = [] if not show_reminders else db.execute(
        """
        SELECT inspection_reminders.*, vehicles.unit_number, vehicles.make, vehicles.model, vehicles.year,
               assigned_to.full_name AS assigned_to_name
        FROM inspection_reminders
        JOIN vehicles ON vehicles.id = inspection_reminders.vehicle_id
        LEFT JOIN users AS assigned_to ON assigned_to.id = inspection_reminders.assigned_to_user_id
        WHERE inspection_reminders.company_id = %s AND inspection_reminders.status != 'completed'
        """,
        (company_id,),
    ).fetchall()

    upcoming = sorted(
        ({**r, "bucket": reminder_status(r["due_date"])} for r in rows),
        key=lambda r: r["due_date"],
    )

    report_rows = db.execute(
        """
        SELECT inspection_reports.*, vehicles.unit_number, vehicles.year, vehicles.make, vehicles.model,
               performed_by.full_name AS performed_by_name
        FROM inspection_reports
        JOIN vehicles ON vehicles.id = inspection_reports.vehicle_id
        JOIN users AS performed_by ON performed_by.id = inspection_reports.performed_by_user_id
        WHERE inspection_reports.company_id = %s AND inspection_reports.deleted_at IS NULL
        ORDER BY inspection_reports.inspection_date DESC, inspection_reports.id DESC
        """,
        (company_id,),
    ).fetchall()
    flagged_rows = db.execute(
        """
        SELECT inspection_report_items.inspection_report_id, COUNT(*) AS flagged_count
        FROM inspection_report_items
        JOIN inspection_reports ON inspection_reports.id = inspection_report_items.inspection_report_id
        WHERE inspection_report_items.company_id = %s AND inspection_reports.deleted_at IS NULL
            AND inspection_report_items.status IN ('needs_attention', 'out_of_spec')
        GROUP BY inspection_report_items.inspection_report_id
        """,
        (company_id,),
    ).fetchall()

    flagged_by_report = {r["inspection_report_id"]: r["flagged_count"] for r in flagged_rows}
    reports = [
        {**r, "flagged_count": flagged_by_report.get(r["id"], 0)}
        for r in report_rows
    ]

    return render_template(
        "inspections.html",
        upcoming=upcoming,
        show_reminders=show_reminders,
        vehicles=vehicle_choices(),
        people=people_choices(),
        reports=reports,
        checklist=INSPECTION_CHECKLIST,
    )


@bp.route("/inspections/reports", methods=["POST"])
def add_inspection_report():
    db = get_db()
    company_id = g.user["company_id"]
    vehicle_id = find_vehicle_for_form(request.form.get("vehicle_id"))
    if vehicle_id is None:
        flash("Select a valid unit number.")
        return redirect("/inspections")

    performed_by, error = validate_person(request.form.get("performed_by_user_id"), "Inspector", required=True)
    if error:
        flash(error)
        return redirect("/inspections")

    inspection_date = request.form.get("inspection_date") or date.today()
    mileage = request.form.get("mileage") or None
    notes = request.form.get("notes") or None

    report = db.execute(
        """
        INSERT INTO inspection_reports
            (company_id, vehicle_id, performed_by_user_id, inspection_type, inspection_date, mileage, notes)
        VALUES (%s, %s, %s, 'general', %s, %s, %s)
        RETURNING id
        """,
        (company_id, vehicle_id, performed_by, inspection_date, mileage, notes),
    ).fetchone()
    report_id = report["id"]

    items = []
    for item_index, item in enumerate(INSPECTION_CHECKLIST):
        status = request.form.get(f"status_{item_index}", "good")
        if status not in ("good", "needs_attention", "out_of_spec"):
            status = "good"
        comments = request.form.get(f"comments_{item_index}") or None
        items.append((company_id, report_id, item, status, comments))

    db.cursor().executemany(
        """
        INSERT INTO inspection_report_items (company_id, inspection_report_id, item, status, comments)
        VALUES (%s, %s, %s, %s, %s)
        """,
        items,
    )
    db.commit()
    return redirect(f"/inspections/reports/{report_id}?new=1")


@bp.route("/inspections/reports/<int:report_id>")
def inspection_report_detail(report_id):
    db = get_db()
    company_id = g.user["company_id"]
    report = db.execute(
        """
        SELECT inspection_reports.*, vehicles.unit_number, vehicles.year, vehicles.make, vehicles.model, vehicles.vin,
               performed_by.full_name AS performed_by_name
        FROM inspection_reports
        JOIN vehicles ON vehicles.id = inspection_reports.vehicle_id
        JOIN users AS performed_by ON performed_by.id = inspection_reports.performed_by_user_id
        WHERE inspection_reports.id = %s AND inspection_reports.company_id = %s
            AND inspection_reports.deleted_at IS NULL
        """,
        (report_id, company_id),
    ).fetchone()

    if report is None:
        abort(404)

    items = db.execute(
        """
        SELECT * FROM inspection_report_items
        WHERE inspection_report_id = %s AND company_id = %s
        ORDER BY id
        """,
        (report_id, company_id),
    ).fetchall()

    out_of_spec_items = [i for i in items if i["status"] == "out_of_spec"]
    work_order_prefill_description = "\n".join(
        f"{i['item']}: {i['comments']}" if i["comments"] else i["item"]
        for i in out_of_spec_items
    )

    return render_template(
        "inspection_report_detail.html",
        report=report,
        items=items,
        status_labels=INSPECTION_STATUS_LABELS,
        out_of_spec_items=out_of_spec_items,
        work_order_prefill_description=work_order_prefill_description,
        prompt_work_order=(request.args.get("new") == "1" and bool(out_of_spec_items)
                           and can(g.user["role"], "work_orders.create_work_order")),
    )


# ---------- INSPECTION REMINDERS ----------

@bp.route("/vehicles/<int:vehicle_id>/reminders", methods=["POST"])
def add_reminder(vehicle_id):
    db = get_db()
    get_vehicle_or_404(vehicle_id)

    reminder_type = request.form.get("reminder_type")
    due_date_str = request.form.get("due_date")
    interval = request.form.get("interval", "none")

    assigned_to, error = validate_person(request.form.get("assigned_to_user_id"), "Assigned to")
    if error:
        flash(error)
        return redirect(f"/vehicles/{vehicle_id}")

    if not due_date_str:
        flash("Due date is required.")
        return redirect(f"/vehicles/{vehicle_id}")

    due_date = date.fromisoformat(due_date_str)

    if interval == "none":
        due_dates = [due_date]
    else:
        custom_value = None
        custom_unit = None
        if interval == "custom":
            try:
                custom_value = int(request.form.get("custom_value", ""))
            except ValueError:
                custom_value = 0
            custom_unit = request.form.get("custom_unit")
            if custom_value < 1 or custom_unit not in ("days", "weeks", "months"):
                flash("Enter a valid custom interval.")
                return redirect(f"/vehicles/{vehicle_id}")
        due_dates = generate_reminder_dates(due_date, interval, custom_value, custom_unit)

    db.cursor().executemany(
        """
        INSERT INTO inspection_reminders (company_id, vehicle_id, reminder_type, due_date, status, assigned_to_user_id)
        VALUES (%s, %s, %s, %s, 'upcoming', %s)
        """,
        [(g.user["company_id"], vehicle_id, reminder_type, d, assigned_to) for d in due_dates],
    )
    db.commit()
    return redirect(f"/vehicles/{vehicle_id}")


@bp.route("/reminders/<int:reminder_id>/complete", methods=["POST"])
def complete_reminder(reminder_id):
    db = get_db()
    row = db.execute(
        "SELECT id, vehicle_id FROM inspection_reminders WHERE id = %s AND company_id = %s",
        (reminder_id, g.user["company_id"]),
    ).fetchone()

    if row is None:
        abort(404)

    db.execute(
        "UPDATE inspection_reminders SET status = 'completed' WHERE id = %s AND company_id = %s",
        (reminder_id, g.user["company_id"]),
    )
    db.commit()
    vehicle_id = row["vehicle_id"]
    return redirect(request.referrer or f"/vehicles/{vehicle_id}")
