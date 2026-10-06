import sqlite3
from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, session

from .db import get_db
from .helpers import login_required, reminder_status

bp = Blueprint("vehicles", __name__)


@bp.route("/vehicles", methods=["GET", "POST"])
@login_required
def vehicles():
    db = get_db()

    if request.method == "POST":
        unit_number = request.form.get("unit_number") or None
        make = request.form.get("make")
        model = request.form.get("model")
        year = request.form.get("year")
        vin = request.form.get("vin") or None
        mileage = request.form.get("mileage") or 0
        is_dot = 1 if request.form.get("is_dot_regulated") else 0
        gvwr = request.form.get("gvwr") or None

        if not make or not model or not year:
            flash("Make, model, and year are required.")
            return redirect("/vehicles")

        try:
            cur = db.execute(
                """
                INSERT INTO vehicles (user_id, unit_number, make, model, year, vin, mileage, is_dot_regulated, gvwr)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (session["user_id"], unit_number, make, model, year, vin, mileage, is_dot, gvwr),
            )
        except sqlite3.IntegrityError:
            db.close()
            flash(f"Unit number \"{unit_number}\" is already in use.")
            return redirect("/vehicles")
        vehicle_id = cur.lastrowid

        # If flagged DOT-regulated, create an empty dot_details row to fill in later
        if is_dot:
            db.execute(
                "INSERT INTO dot_details (vehicle_id) VALUES (?)", (vehicle_id,)
            )

        db.commit()
        db.close()
        return redirect(f"/vehicles/{vehicle_id}")

    rows = db.execute(
        "SELECT * FROM vehicles WHERE user_id = ?", (session["user_id"],)
    ).fetchall()
    db.close()
    return render_template("vehicles.html", vehicles=rows)


@bp.route("/vehicles/<int:vehicle_id>")
@login_required
def vehicle_detail(vehicle_id):
    db = get_db()

    vehicle = db.execute(
        "SELECT * FROM vehicles WHERE id = ? AND user_id = ?",
        (vehicle_id, session["user_id"]),
    ).fetchone()
    if vehicle is None:
        db.close()
        flash("Vehicle not found.")
        return redirect("/vehicles")

    dot_details = None
    if vehicle["is_dot_regulated"]:
        dot_details = db.execute(
            "SELECT * FROM dot_details WHERE vehicle_id = ?", (vehicle_id,)
        ).fetchone()

    reminders = db.execute(
        "SELECT * FROM inspection_reminders WHERE vehicle_id = ? ORDER BY due_date ASC",
        (vehicle_id,),
    ).fetchall()

    work_order_rows = db.execute(
        "SELECT * FROM work_orders WHERE vehicle_id = ? ORDER BY created_at DESC",
        (vehicle_id,),
    ).fetchall()
    db.close()

    reminders_with_status = [
        {**dict(r), "bucket": reminder_status(r["due_date"])} for r in reminders
    ]

    return render_template(
        "vehicle_detail.html",
        vehicle=vehicle,
        dot_details=dot_details,
        reminders=reminders_with_status,
        work_orders=work_order_rows,
    )


# ---------- MAINTENANCE LOGS ----------

@bp.route("/vehicles/<int:vehicle_id>/maintenance", methods=["POST"])
@login_required
def add_maintenance(vehicle_id):
    db = get_db()
    owned = db.execute(
        "SELECT id FROM vehicles WHERE id = ? AND user_id = ?",
        (vehicle_id, session["user_id"]),
    ).fetchone()
    if owned is None:
        db.close()
        flash("Vehicle not found.")
        return redirect("/vehicles")

    service_type = request.form.get("service_type")
    log_date = request.form.get("log_date") or date.today().isoformat()
    mileage = request.form.get("mileage_at_service") or None
    cost = request.form.get("cost") or None
    notes = request.form.get("notes") or None
    mechanic_name = request.form.get("mechanic_name") or None

    cursor = db.execute(
        """
        INSERT INTO maintenance_logs
            (vehicle_id, log_date, service_type, mileage_at_service, cost, notes, mechanic_name)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (vehicle_id, log_date, service_type, mileage, cost, notes, mechanic_name),
    )
    log_id = cursor.lastrowid

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
        parts.append((log_id, name, max(qty, 1)))

    if parts:
        db.executemany(
            "INSERT INTO maintenance_parts (maintenance_log_id, part_name, quantity) VALUES (?, ?, ?)",
            parts,
        )

    db.commit()
    db.close()
    return redirect(f"/vehicles/{vehicle_id}")


# ---------- DTC CODES ----------

@bp.route("/vehicles/<int:vehicle_id>/dtc", methods=["POST"])
@login_required
def add_dtc(vehicle_id):
    db = get_db()
    owned = db.execute(
        "SELECT id FROM vehicles WHERE id = ? AND user_id = ?",
        (vehicle_id, session["user_id"]),
    ).fetchone()
    if owned is None:
        db.close()
        flash("Vehicle not found.")
        return redirect("/vehicles")

    code = request.form.get("code", "").upper().strip()

    # Look up plain-English description from reference table, if we have one
    ref = db.execute(
        "SELECT description FROM dtc_reference WHERE code = ?", (code,)
    ).fetchone()
    description = ref["description"] if ref else request.form.get("description")

    db.execute(
        """
        INSERT INTO dtc_codes (vehicle_id, code, description, date_logged)
        VALUES (?, ?, ?, ?)
        """,
        (vehicle_id, code, description, date.today().isoformat()),
    )
    db.commit()
    db.close()
    return redirect(f"/vehicles/{vehicle_id}")
