import json
import os
import shutil
import uuid
from datetime import datetime

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_from_directory, session
from werkzeug.utils import secure_filename

from .db import get_db
from .helpers import login_required

bp = Blueprint("work_orders", __name__)

ALLOWED_ATTACHMENT_EXTENSIONS = {
    "png", "jpg", "jpeg", "gif", "webp", "heic",
    "pdf", "doc", "docx", "xls", "xlsx", "txt", "csv",
}


def save_work_order_attachments(db, work_order_id, files):
    """Save uploaded files for a work order to disk and record them in the DB.
    Silently skips empty file inputs and disallowed extensions."""
    wo_dir = os.path.join(current_app.config["UPLOAD_DIR"], str(work_order_id))
    for f in files:
        if not f or not f.filename:
            continue
        original = secure_filename(f.filename)
        ext = original.rsplit(".", 1)[-1].lower() if "." in original else ""
        if ext not in ALLOWED_ATTACHMENT_EXTENSIONS:
            flash(f'Skipped "{f.filename}" — unsupported file type.')
            continue
        os.makedirs(wo_dir, exist_ok=True)
        stored = f"{uuid.uuid4().hex}.{ext}"
        f.save(os.path.join(wo_dir, stored))
        db.execute(
            """
            INSERT INTO work_order_attachments (work_order_id, original_filename, stored_filename, content_type)
            VALUES (?, ?, ?, ?)
            """,
            (work_order_id, original, stored, f.mimetype),
        )


@bp.route("/work-orders", methods=["GET", "POST"])
@login_required
def work_orders():
    db = get_db()

    if request.method == "POST":
        vehicle_id = request.form.get("vehicle_id")
        owned = db.execute(
            "SELECT id FROM vehicles WHERE id = ? AND user_id = ?",
            (vehicle_id, session["user_id"]),
        ).fetchone()
        if owned is None:
            db.close()
            flash("Select a valid unit number.")
            return redirect("/work-orders")

        status = request.form.get("status")
        if status not in ("open", "closed"):
            status = "open"
        created_by = request.form.get("created_by") or None
        assigned_to = request.form.get("assigned_to") or None
        description = request.form.get("description") or None
        notes = request.form.get("notes") or None
        hours = request.form.get("hours") or None
        scheduled_completion_date = request.form.get("scheduled_completion_date") or None

        # Match SQLite's CURRENT_TIMESTAMP format ('YYYY-MM-DD HH:MM:SS', UTC) used by the close route
        closed_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S") if status == "closed" else None

        cursor = db.execute(
            """
            INSERT INTO work_orders
                (vehicle_id, status, created_by, assigned_to, description, notes, hours,
                 scheduled_completion_date, closed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (vehicle_id, status, created_by, assigned_to, description, notes, hours,
             scheduled_completion_date, closed_at),
        )
        work_order_id = cursor.lastrowid

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
            parts.append((work_order_id, name, max(qty, 1)))

        if parts:
            db.executemany(
                "INSERT INTO work_order_parts (work_order_id, part_name, quantity) VALUES (?, ?, ?)",
                parts,
            )

        save_work_order_attachments(db, work_order_id, request.files.getlist("attachments"))

        db.commit()
        db.close()
        return redirect("/work-orders")

    vehicles = db.execute(
        "SELECT id, unit_number, year, make, model, vin FROM vehicles WHERE user_id = ? ORDER BY unit_number",
        (session["user_id"],),
    ).fetchall()

    work_order_rows = db.execute(
        """
        SELECT work_orders.*, vehicles.unit_number, vehicles.year, vehicles.make, vehicles.model, vehicles.vin
        FROM work_orders
        JOIN vehicles ON vehicles.id = work_orders.vehicle_id
        WHERE vehicles.user_id = ?
        ORDER BY work_orders.created_at DESC
        """,
        (session["user_id"],),
    ).fetchall()
    db.close()

    open_work_orders = [dict(w) for w in work_order_rows if w["status"] == "open"]
    closed_work_orders = [dict(w) for w in work_order_rows if w["status"] == "closed"]

    vehicles_json = json.dumps({
        str(v["id"]): {"year": v["year"], "make": v["make"], "model": v["model"], "vin": v["vin"]}
        for v in vehicles
    }).replace("</", "<\\/")

    return render_template(
        "work_orders.html",
        vehicles=vehicles,
        vehicles_json=vehicles_json,
        open_work_orders=open_work_orders,
        closed_work_orders=closed_work_orders,
        prefill_vehicle_id=request.args.get("vehicle_id"),
        prefill_description=request.args.get("description"),
    )


@bp.route("/work-orders/<int:work_order_id>")
@login_required
def work_order_detail(work_order_id):
    db = get_db()
    w = db.execute(
        """
        SELECT work_orders.*, vehicles.unit_number, vehicles.year, vehicles.make, vehicles.model, vehicles.vin
        FROM work_orders
        JOIN vehicles ON vehicles.id = work_orders.vehicle_id
        WHERE work_orders.id = ? AND vehicles.user_id = ?
        """,
        (work_order_id, session["user_id"]),
    ).fetchone()

    if w is None:
        db.close()
        flash("Work order not found.")
        return redirect("/work-orders")

    parts = db.execute(
        "SELECT * FROM work_order_parts WHERE work_order_id = ?", (work_order_id,)
    ).fetchall()
    attachments = db.execute(
        "SELECT * FROM work_order_attachments WHERE work_order_id = ? ORDER BY uploaded_at ASC",
        (work_order_id,),
    ).fetchall()
    vehicles = db.execute(
        "SELECT id, unit_number, year, make, model, vin FROM vehicles WHERE user_id = ? ORDER BY unit_number",
        (session["user_id"],),
    ).fetchall()
    db.close()

    vehicles_json = json.dumps({
        str(v["id"]): {"year": v["year"], "make": v["make"], "model": v["model"], "vin": v["vin"]}
        for v in vehicles
    }).replace("</", "<\\/")

    parts_lines = "\n".join(
        f"{p['part_name']}" + (f" (x{p['quantity']})" if p["quantity"] > 1 else "") for p in parts
    )
    work_order = {**dict(w), "parts": parts, "attachments": attachments, "parts_lines": parts_lines}

    return render_template(
        "work_order_detail.html",
        w=work_order,
        vehicles=vehicles,
        vehicles_json=vehicles_json,
    )


@bp.route("/work-orders/<int:work_order_id>/close", methods=["POST"])
@login_required
def close_work_order(work_order_id):
    db = get_db()
    owned = db.execute(
        """
        SELECT work_orders.id
        FROM work_orders
        JOIN vehicles ON vehicles.id = work_orders.vehicle_id
        WHERE work_orders.id = ? AND vehicles.user_id = ?
        """,
        (work_order_id, session["user_id"]),
    ).fetchone()

    if owned is None:
        db.close()
        flash("Work order not found.")
        return redirect("/work-orders")

    db.execute(
        "UPDATE work_orders SET status = 'closed', closed_at = CURRENT_TIMESTAMP WHERE id = ?",
        (work_order_id,),
    )
    db.commit()
    db.close()
    return redirect(f"/work-orders/{work_order_id}")


@bp.route("/work-orders/<int:work_order_id>/delete", methods=["POST"])
@login_required
def delete_work_order(work_order_id):
    db = get_db()
    owned = db.execute(
        """
        SELECT work_orders.id
        FROM work_orders
        JOIN vehicles ON vehicles.id = work_orders.vehicle_id
        WHERE work_orders.id = ? AND vehicles.user_id = ?
        """,
        (work_order_id, session["user_id"]),
    ).fetchone()

    if owned is None:
        db.close()
        flash("Work order not found.")
        return redirect("/work-orders")

    db.execute("DELETE FROM work_order_parts WHERE work_order_id = ?", (work_order_id,))
    db.execute("DELETE FROM work_order_attachments WHERE work_order_id = ?", (work_order_id,))
    db.execute("DELETE FROM work_orders WHERE id = ?", (work_order_id,))
    db.commit()
    db.close()

    wo_dir = os.path.join(current_app.config["UPLOAD_DIR"], str(work_order_id))
    shutil.rmtree(wo_dir, ignore_errors=True)

    flash("Work order deleted.")
    return redirect("/work-orders")


@bp.route("/work-orders/<int:work_order_id>/edit", methods=["POST"])
@login_required
def edit_work_order(work_order_id):
    db = get_db()
    current = db.execute(
        """
        SELECT work_orders.id, work_orders.status, work_orders.closed_at
        FROM work_orders
        JOIN vehicles ON vehicles.id = work_orders.vehicle_id
        WHERE work_orders.id = ? AND vehicles.user_id = ?
        """,
        (work_order_id, session["user_id"]),
    ).fetchone()

    if current is None:
        db.close()
        flash("Work order not found.")
        return redirect("/work-orders")

    vehicle_id = request.form.get("vehicle_id")
    owned_vehicle = db.execute(
        "SELECT id FROM vehicles WHERE id = ? AND user_id = ?",
        (vehicle_id, session["user_id"]),
    ).fetchone()
    if owned_vehicle is None:
        db.close()
        flash("Select a valid unit number.")
        return redirect("/work-orders")

    status = request.form.get("status")
    if status not in ("open", "closed"):
        status = current["status"]
    created_by = request.form.get("created_by") or None
    assigned_to = request.form.get("assigned_to") or None
    description = request.form.get("description") or None
    notes = request.form.get("notes") or None
    hours = request.form.get("hours") or None
    scheduled_completion_date = request.form.get("scheduled_completion_date") or None

    if status == "closed" and current["status"] != "closed":
        closed_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    elif status == "open" and current["status"] == "closed":
        closed_at = None  # reopened
    else:
        closed_at = current["closed_at"]

    db.execute(
        """
        UPDATE work_orders
        SET vehicle_id = ?, status = ?, created_by = ?, assigned_to = ?,
            description = ?, notes = ?, hours = ?, scheduled_completion_date = ?, closed_at = ?
        WHERE id = ?
        """,
        (vehicle_id, status, created_by, assigned_to, description, notes, hours,
         scheduled_completion_date, closed_at, work_order_id),
    )

    # Parts are edited as a whole list: drop the old rows and re-insert what was submitted
    db.execute("DELETE FROM work_order_parts WHERE work_order_id = ?", (work_order_id,))
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
        parts.append((work_order_id, name, max(qty, 1)))
    if parts:
        db.executemany(
            "INSERT INTO work_order_parts (work_order_id, part_name, quantity) VALUES (?, ?, ?)",
            parts,
        )

    save_work_order_attachments(db, work_order_id, request.files.getlist("attachments"))

    db.commit()
    db.close()
    return redirect(f"/work-orders/{work_order_id}")


@bp.route("/work-orders/<int:work_order_id>/attachments/<int:attachment_id>")
@login_required
def download_attachment(work_order_id, attachment_id):
    db = get_db()
    attachment = db.execute(
        """
        SELECT work_order_attachments.*
        FROM work_order_attachments
        JOIN work_orders ON work_orders.id = work_order_attachments.work_order_id
        JOIN vehicles ON vehicles.id = work_orders.vehicle_id
        WHERE work_order_attachments.id = ? AND work_order_attachments.work_order_id = ? AND vehicles.user_id = ?
        """,
        (attachment_id, work_order_id, session["user_id"]),
    ).fetchone()
    db.close()

    if attachment is None:
        abort(404)

    wo_dir = os.path.join(current_app.config["UPLOAD_DIR"], str(work_order_id))
    return send_from_directory(
        wo_dir,
        attachment["stored_filename"],
        mimetype=attachment["content_type"],
        download_name=attachment["original_filename"],
    )


@bp.route("/work-orders/<int:work_order_id>/attachments/<int:attachment_id>/delete", methods=["POST"])
@login_required
def delete_attachment(work_order_id, attachment_id):
    db = get_db()
    attachment = db.execute(
        """
        SELECT work_order_attachments.*
        FROM work_order_attachments
        JOIN work_orders ON work_orders.id = work_order_attachments.work_order_id
        JOIN vehicles ON vehicles.id = work_orders.vehicle_id
        WHERE work_order_attachments.id = ? AND work_order_attachments.work_order_id = ? AND vehicles.user_id = ?
        """,
        (attachment_id, work_order_id, session["user_id"]),
    ).fetchone()

    if attachment is None:
        db.close()
        flash("Attachment not found.")
        return redirect("/work-orders")

    wo_dir = os.path.join(current_app.config["UPLOAD_DIR"], str(work_order_id))
    try:
        os.remove(os.path.join(wo_dir, attachment["stored_filename"]))
    except OSError:
        pass

    db.execute("DELETE FROM work_order_attachments WHERE id = ?", (attachment_id,))
    db.commit()
    db.close()
    return redirect(f"/work-orders/{work_order_id}")
