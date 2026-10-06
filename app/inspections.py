from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, session

from .constants import INSPECTION_CHECKLIST, INSPECTION_STATUS_LABELS
from .db import get_db
from .helpers import generate_reminder_dates, login_required, reminder_status

bp = Blueprint("inspections", __name__)


@bp.route("/inspections")
@login_required
def inspections():
    db = get_db()
    rows = db.execute(
        """
        SELECT inspection_reminders.*, vehicles.unit_number, vehicles.make, vehicles.model, vehicles.year
        FROM inspection_reminders
        JOIN vehicles ON vehicles.id = inspection_reminders.vehicle_id
        WHERE vehicles.user_id = ? AND inspection_reminders.status != 'completed'
        """,
        (session["user_id"],),
    ).fetchall()

    upcoming = sorted(
        ({**dict(r), "bucket": reminder_status(r["due_date"])} for r in rows),
        key=lambda r: r["due_date"],
    )

    vehicles = db.execute(
        "SELECT id, unit_number, year, make, model FROM vehicles WHERE user_id = ? ORDER BY unit_number",
        (session["user_id"],),
    ).fetchall()
    report_rows = db.execute(
        """
        SELECT inspection_reports.*, vehicles.unit_number, vehicles.year, vehicles.make, vehicles.model
        FROM inspection_reports
        JOIN vehicles ON vehicles.id = inspection_reports.vehicle_id
        WHERE vehicles.user_id = ?
        ORDER BY inspection_reports.inspection_date DESC, inspection_reports.id DESC
        """,
        (session["user_id"],),
    ).fetchall()
    flagged_rows = db.execute(
        """
        SELECT inspection_report_items.inspection_report_id, COUNT(*) AS flagged_count
        FROM inspection_report_items
        JOIN inspection_reports ON inspection_reports.id = inspection_report_items.inspection_report_id
        JOIN vehicles ON vehicles.id = inspection_reports.vehicle_id
        WHERE vehicles.user_id = ? AND inspection_report_items.status IN ('needs_attention', 'out_of_spec')
        GROUP BY inspection_report_items.inspection_report_id
        """,
        (session["user_id"],),
    ).fetchall()
    db.close()

    flagged_by_report = {r["inspection_report_id"]: r["flagged_count"] for r in flagged_rows}
    reports = [
        {**dict(r), "flagged_count": flagged_by_report.get(r["id"], 0)}
        for r in report_rows
    ]

    return render_template(
        "inspections.html",
        upcoming=upcoming,
        vehicles=vehicles,
        reports=reports,
        checklist=INSPECTION_CHECKLIST,
    )


@bp.route("/inspections/reports", methods=["POST"])
@login_required
def add_inspection_report():
    db = get_db()
    vehicle_id = request.form.get("vehicle_id")
    owned = db.execute(
        "SELECT id FROM vehicles WHERE id = ? AND user_id = ?",
        (vehicle_id, session["user_id"]),
    ).fetchone()
    if owned is None:
        db.close()
        flash("Select a valid unit number.")
        return redirect("/inspections")

    inspector_name = request.form.get("inspector_name") or None
    inspection_date = request.form.get("inspection_date") or date.today().isoformat()
    mileage = request.form.get("mileage") or None
    notes = request.form.get("notes") or None

    cursor = db.execute(
        """
        INSERT INTO inspection_reports (vehicle_id, inspector_name, inspection_date, mileage, notes)
        VALUES (?, ?, ?, ?, ?)
        """,
        (vehicle_id, inspector_name, inspection_date, mileage, notes),
    )
    report_id = cursor.lastrowid

    items = []
    for item_index, item in enumerate(INSPECTION_CHECKLIST):
        status = request.form.get(f"status_{item_index}", "good")
        if status not in ("good", "needs_attention", "out_of_spec"):
            status = "good"
        comments = request.form.get(f"comments_{item_index}") or None
        items.append((report_id, item, status, comments))

    db.executemany(
        "INSERT INTO inspection_report_items (inspection_report_id, item, status, comments) VALUES (?, ?, ?, ?)",
        items,
    )
    db.commit()
    db.close()
    return redirect(f"/inspections/reports/{report_id}?new=1")


@bp.route("/inspections/reports/<int:report_id>")
@login_required
def inspection_report_detail(report_id):
    db = get_db()
    report = db.execute(
        """
        SELECT inspection_reports.*, vehicles.unit_number, vehicles.year, vehicles.make, vehicles.model, vehicles.vin
        FROM inspection_reports
        JOIN vehicles ON vehicles.id = inspection_reports.vehicle_id
        WHERE inspection_reports.id = ? AND vehicles.user_id = ?
        """,
        (report_id, session["user_id"]),
    ).fetchone()

    if report is None:
        db.close()
        flash("Inspection report not found.")
        return redirect("/inspections")

    items = db.execute(
        "SELECT * FROM inspection_report_items WHERE inspection_report_id = ? ORDER BY id",
        (report_id,),
    ).fetchall()
    db.close()

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
        prompt_work_order=(request.args.get("new") == "1" and bool(out_of_spec_items)),
    )


# ---------- INSPECTION REMINDERS ----------

@bp.route("/vehicles/<int:vehicle_id>/reminders", methods=["POST"])
@login_required
def add_reminder(vehicle_id):
    db = get_db()
    owned = db.execute(
        "SELECT id FROM vehicles WHERE id = ? AND user_id = ?",
        (vehicle_id, session["user_id"]),
    ).fetchone()
    if owned is None:
        db.close()
        flash("Vehicle not found.")
        return redirect("/vehicles")

    reminder_type = request.form.get("reminder_type")
    due_date_str = request.form.get("due_date")
    interval = request.form.get("interval", "none")
    mechanic_name = request.form.get("mechanic_name") or None

    if not due_date_str:
        db.close()
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
                db.close()
                flash("Enter a valid custom interval.")
                return redirect(f"/vehicles/{vehicle_id}")
        due_dates = generate_reminder_dates(due_date, interval, custom_value, custom_unit)

    db.executemany(
        """
        INSERT INTO inspection_reminders (vehicle_id, reminder_type, due_date, status, mechanic_name)
        VALUES (?, ?, ?, 'upcoming', ?)
        """,
        [(vehicle_id, reminder_type, d.isoformat(), mechanic_name) for d in due_dates],
    )
    db.commit()
    db.close()
    return redirect(f"/vehicles/{vehicle_id}")


@bp.route("/reminders/<int:reminder_id>/complete", methods=["POST"])
@login_required
def complete_reminder(reminder_id):
    db = get_db()
    # Ensure the reminder belongs to a vehicle owned by this user before updating
    row = db.execute(
        """
        SELECT inspection_reminders.id, inspection_reminders.vehicle_id
        FROM inspection_reminders
        JOIN vehicles ON vehicles.id = inspection_reminders.vehicle_id
        WHERE inspection_reminders.id = ? AND vehicles.user_id = ?
        """,
        (reminder_id, session["user_id"]),
    ).fetchone()

    if row is None:
        db.close()
        flash("Reminder not found.")
        return redirect("/")

    db.execute(
        "UPDATE inspection_reminders SET status = 'completed' WHERE id = ?",
        (reminder_id,),
    )
    db.commit()
    vehicle_id = row["vehicle_id"]
    db.close()
    return redirect(request.referrer or f"/vehicles/{vehicle_id}")
