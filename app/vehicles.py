from datetime import date

import psycopg
from flask import Blueprint, abort, flash, g, redirect, render_template, request

from .db import get_db
from .helpers import reminder_status
from .people import people_choices, validate_person
from .permissions import can

bp = Blueprint("vehicles", __name__)


def vehicle_choices(current_vehicle_id=None):
    """Vehicles to list in a vehicle dropdown: the company's active (not archived) vehicles.
    When editing a record, pass its current vehicle so it still shows if since archived."""
    return get_db().execute(
        """
        SELECT id, unit_number, year, make, model, vin
        FROM vehicles
        WHERE company_id = %s AND (archived_at IS NULL OR id = %s)
        ORDER BY unit_number, id
        """,
        (g.user["company_id"], current_vehicle_id),
    ).fetchall()


def find_vehicle_for_form(value, current_vehicle_id=None):
    """Return the vehicle id submitted from a vehicle dropdown if it's one that may be
    chosen (an active vehicle of this company, or the record's current vehicle), else None."""
    try:
        vehicle_id = int(value)
    except (TypeError, ValueError):
        return None
    row = get_db().execute(
        """
        SELECT id FROM vehicles
        WHERE id = %s AND company_id = %s AND (archived_at IS NULL OR id = %s)
        """,
        (vehicle_id, g.user["company_id"], current_vehicle_id),
    ).fetchone()
    return row["id"] if row else None


def get_vehicle_or_404(vehicle_id):
    vehicle = get_db().execute(
        "SELECT * FROM vehicles WHERE id = %s AND company_id = %s",
        (vehicle_id, g.user["company_id"]),
    ).fetchone()
    if vehicle is None:
        abort(404)
    return vehicle


@bp.route("/vehicles")
def vehicles():
    rows = get_db().execute(
        "SELECT * FROM vehicles WHERE company_id = %s AND archived_at IS NULL ORDER BY id",
        (g.user["company_id"],),
    ).fetchall()
    return render_template("vehicles.html", vehicles=rows)


@bp.route("/vehicles", methods=["POST"])
def add_vehicle():
    db = get_db()
    company_id = g.user["company_id"]

    unit_number = request.form.get("unit_number") or None
    make = request.form.get("make")
    model = request.form.get("model")
    year = request.form.get("year")
    vin = request.form.get("vin") or None
    mileage = request.form.get("mileage") or 0
    is_dot = bool(request.form.get("is_dot_regulated"))
    gvwr = request.form.get("gvwr") or None

    if not make or not model or not year:
        flash("Make, model, and year are required.")
        return redirect("/vehicles")

    try:
        vehicle = db.execute(
            """
            INSERT INTO vehicles (company_id, unit_number, make, model, year, vin, mileage, is_dot_regulated, gvwr)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (company_id, unit_number, make, model, year, vin, mileage, is_dot, gvwr),
        ).fetchone()
    except psycopg.errors.UniqueViolation as e:
        db.rollback()
        if e.diag.constraint_name == "vehicles_company_vin_key":
            flash(f"VIN \"{vin}\" is already in use.")
        else:
            flash(f"Unit number \"{unit_number}\" is already in use.")
        return redirect("/vehicles")
    vehicle_id = vehicle["id"]

    # If flagged DOT-regulated, create an empty dot_details row to fill in later
    if is_dot:
        db.execute(
            "INSERT INTO dot_details (company_id, vehicle_id) VALUES (%s, %s)", (company_id, vehicle_id)
        )

    db.commit()
    return redirect(f"/vehicles/{vehicle_id}")


@bp.route("/vehicles/<int:vehicle_id>")
def vehicle_detail(vehicle_id):
    db = get_db()
    company_id = g.user["company_id"]
    vehicle = get_vehicle_or_404(vehicle_id)

    inspection_reports = db.execute(
        """
        SELECT inspection_reports.id, inspection_reports.inspection_type, inspection_reports.inspection_date,
               performed_by.full_name AS performed_by_name
        FROM inspection_reports
        JOIN users AS performed_by ON performed_by.id = inspection_reports.performed_by_user_id
        WHERE inspection_reports.vehicle_id = %s AND inspection_reports.company_id = %s
            AND inspection_reports.deleted_at IS NULL
        ORDER BY inspection_reports.inspection_date DESC, inspection_reports.id DESC
        """,
        (vehicle_id, company_id),
    ).fetchall()

    # Roles that can't work on work orders (drivers) get the basic vehicle info and
    # its inspections only. The shop data below is not even queried for them, so it
    # can't leak through the template.
    if not can(g.user["role"], "work_orders.work_orders"):
        return render_template("vehicle_detail.html", vehicle=vehicle, inspection_reports=inspection_reports,
                               shop_view=False)

    dot_details = None
    if vehicle["is_dot_regulated"]:
        dot_details = db.execute(
            "SELECT * FROM dot_details WHERE vehicle_id = %s AND company_id = %s", (vehicle_id, company_id)
        ).fetchone()

    reminders = db.execute(
        """
        SELECT inspection_reminders.*, assigned_to.full_name AS assigned_to_name
        FROM inspection_reminders
        LEFT JOIN users AS assigned_to ON assigned_to.id = inspection_reminders.assigned_to_user_id
        WHERE inspection_reminders.vehicle_id = %s AND inspection_reminders.company_id = %s
        ORDER BY inspection_reminders.due_date ASC
        """,
        (vehicle_id, company_id),
    ).fetchall()

    work_order_rows = db.execute(
        """
        SELECT * FROM work_orders
        WHERE vehicle_id = %s AND company_id = %s AND deleted_at IS NULL
        ORDER BY created_at DESC
        """,
        (vehicle_id, company_id),
    ).fetchall()

    reminders_with_status = [
        {**r, "bucket": reminder_status(r["due_date"])} for r in reminders
    ]

    return render_template(
        "vehicle_detail.html",
        vehicle=vehicle,
        inspection_reports=inspection_reports,
        shop_view=True,
        dot_details=dot_details,
        reminders=reminders_with_status,
        work_orders=work_order_rows,
        people=people_choices(),
    )


# ---------- MAINTENANCE LOGS ----------

@bp.route("/vehicles/<int:vehicle_id>/maintenance", methods=["POST"])
def add_maintenance(vehicle_id):
    db = get_db()
    company_id = g.user["company_id"]
    get_vehicle_or_404(vehicle_id)

    performed_by, error = validate_person(request.form.get("performed_by_user_id"), "Performed by", required=True)
    if error:
        flash(error)
        return redirect(f"/vehicles/{vehicle_id}")

    service_type = request.form.get("service_type")
    log_date = request.form.get("log_date") or date.today()
    mileage = request.form.get("mileage_at_service") or None
    cost = request.form.get("cost") or None
    notes = request.form.get("notes") or None

    log = db.execute(
        """
        INSERT INTO maintenance_logs
            (company_id, vehicle_id, log_date, service_type, mileage_at_service, cost, notes, performed_by_user_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (company_id, vehicle_id, log_date, service_type, mileage, cost, notes, performed_by),
    ).fetchone()
    log_id = log["id"]

    part_names = request.form.getlist("part_name[]")
    part_quantities = request.form.getlist("part_quantity[]")
    parts = []
    for name, qty_str in zip(part_names, part_quantities):
        name = name.strip()
        if not name:
            continue
        try:
            qty = int(qty_str)
        except ValueError:
            qty = 1
        parts.append((company_id, log_id, name, max(qty, 1)))

    if parts:
        db.cursor().executemany(
            "INSERT INTO maintenance_parts (company_id, maintenance_log_id, part_name, quantity) VALUES (%s, %s, %s, %s)",
            parts,
        )

    db.commit()
    return redirect(f"/vehicles/{vehicle_id}")


# ---------- DTC CODES ----------

@bp.route("/vehicles/<int:vehicle_id>/dtc", methods=["POST"])
def add_dtc(vehicle_id):
    db = get_db()
    get_vehicle_or_404(vehicle_id)

    code = request.form.get("code", "").upper().strip()

    # Look up plain-English description from the shared reference table, if we have one
    ref = db.execute(
        "SELECT description FROM dtc_reference WHERE code = %s", (code,)
    ).fetchone()
    description = ref["description"] if ref else request.form.get("description")

    db.execute(
        """
        INSERT INTO dtc_codes (company_id, vehicle_id, code, description, date_logged)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (g.user["company_id"], vehicle_id, code, description, date.today()),
    )
    db.commit()
    return redirect(f"/vehicles/{vehicle_id}")
