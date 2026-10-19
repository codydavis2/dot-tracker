import json
import os
import uuid
from datetime import datetime, timezone

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

from .db import get_db
from .inspections import get_report_or_404, out_of_spec_description
from .people import people_choices, validate_person
from .vehicles import find_vehicle_for_form, vehicle_choices

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
            INSERT INTO work_order_attachments (company_id, work_order_id, original_filename, stored_filename, content_type)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (g.user["company_id"], work_order_id, original, stored, f.mimetype),
        )


def read_parts_form(company_id, work_order_id):
    """Rows for work_order_parts from the submitted parts list. Blank names are skipped;
    a missing or non-numeric quantity counts as 1."""
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
        parts.append((company_id, work_order_id, name, max(qty, 1)))
    return parts


def read_work_order_form(current=None):
    """Read and validate the work order form shared by create and edit.

    `current` is the existing work order when editing (None when creating).
    Returns (values, None) when valid or (None, error_message).
    """
    current = current or {}
    form = request.form

    vehicle_id = find_vehicle_for_form(form.get("vehicle_id"), current.get("vehicle_id"))
    if vehicle_id is None:
        return None, "Select a valid unit number."

    status = form.get("status")
    if status not in ("open", "closed"):
        status = current.get("status", "open")

    created_by, error = validate_person(
        form.get("created_by_user_id"), "Created by", required=True,
        current_user_id=current.get("created_by_user_id"),
    )
    if error:
        return None, error

    assigned_to, error = validate_person(
        form.get("assigned_to_user_id"), "Assigned to",
        current_user_id=current.get("assigned_to_user_id"),
    )
    if error:
        return None, error

    # "Completed by" is only kept on closed work orders; reopening clears it.
    performed_by = None
    if status == "closed":
        performed_by, error = validate_person(
            form.get("performed_by_user_id"), "Completed by", required=True,
            current_user_id=current.get("performed_by_user_id"),
        )
        if error:
            return None, error

    return {
        "vehicle_id": vehicle_id,
        "status": status,
        "created_by_user_id": created_by,
        "assigned_to_user_id": assigned_to,
        "performed_by_user_id": performed_by,
        "description": form.get("description") or None,
        "notes": form.get("notes") or None,
        "hours": form.get("hours") or None,
        "scheduled_completion_date": form.get("scheduled_completion_date") or None,
    }, None


def read_inspection_link(vehicle_id):
    """The inspection_report_id hidden field on the create form (set when the work
    order comes from a red-item alert). Returns (report_id or None, error or None).
    It must be one of this company's (not deleted) reports, for the same unit."""
    value = request.form.get("inspection_report_id")
    if not value:
        return None, None
    try:
        report_id = int(value)
    except ValueError:
        return None, "That inspection report wasn't found."
    report = get_db().execute(
        "SELECT id, vehicle_id FROM inspection_reports WHERE id = %s AND company_id = %s AND deleted_at IS NULL",
        (report_id, g.user["company_id"]),
    ).fetchone()
    if report is None:
        return None, "That inspection report wasn't found."
    if report["vehicle_id"] != vehicle_id:
        return None, "The work order must be for the same unit as the inspection it comes from."
    return report_id, None


def vehicles_json_for(vehicles):
    return json.dumps({
        str(v["id"]): {"year": v["year"], "make": v["make"], "model": v["model"], "vin": v["vin"]}
        for v in vehicles
    }).replace("</", "<\\/")


def get_work_order_or_404(work_order_id):
    """The current company's (not deleted) work order, or a 404."""
    work_order = get_db().execute(
        """
        SELECT * FROM work_orders
        WHERE id = %s AND company_id = %s AND deleted_at IS NULL
        """,
        (work_order_id, g.user["company_id"]),
    ).fetchone()
    if work_order is None:
        abort(404)
    return work_order


@bp.route("/work-orders")
def work_orders():
    db = get_db()
    company_id = g.user["company_id"]
    vehicles = vehicle_choices()

    work_order_rows = db.execute(
        """
        SELECT work_orders.*, vehicles.unit_number, vehicles.year, vehicles.make, vehicles.model, vehicles.vin
        FROM work_orders
        JOIN vehicles ON vehicles.id = work_orders.vehicle_id
        WHERE work_orders.company_id = %s AND work_orders.deleted_at IS NULL
        ORDER BY work_orders.created_at DESC, work_orders.id DESC
        """,
        (company_id,),
    ).fetchall()

    open_work_orders = [w for w in work_order_rows if w["status"] == "open"]
    closed_work_orders = [w for w in work_order_rows if w["status"] == "closed"]

    # ?inspection_report_id=N (from a red-item alert or the post-inspection prompt):
    # open the new work order form pre-filled from that report's Out of Spec items
    # and linked to it. Another company's report is a 404.
    from_report = None
    prefill_description = None
    if request.args.get("inspection_report_id"):
        try:
            report_id = int(request.args["inspection_report_id"])
        except ValueError:
            abort(404)
        from_report = get_report_or_404(report_id)
        prefill_description = out_of_spec_description(report_id)

    return render_template(
        "work_orders.html",
        vehicles=vehicles,
        vehicles_json=vehicles_json_for(vehicles),
        people=people_choices(),
        open_work_orders=open_work_orders,
        closed_work_orders=closed_work_orders,
        from_report=from_report,
        prefill_vehicle_id=from_report["vehicle_id"] if from_report else None,
        prefill_description=prefill_description,
    )


@bp.route("/work-orders", methods=["POST"])
def create_work_order():
    db = get_db()
    company_id = g.user["company_id"]

    values, error = read_work_order_form()
    if error is None:
        inspection_report_id, error = read_inspection_link(values["vehicle_id"])
    if error:
        flash(error)
        return redirect("/work-orders")

    closed_at = datetime.now(timezone.utc) if values["status"] == "closed" else None

    work_order = db.execute(
        """
        INSERT INTO work_orders
            (company_id, vehicle_id, status, created_by_user_id, assigned_to_user_id, performed_by_user_id,
             description, notes, hours, scheduled_completion_date, closed_at, inspection_report_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (company_id, values["vehicle_id"], values["status"], values["created_by_user_id"],
         values["assigned_to_user_id"], values["performed_by_user_id"], values["description"],
         values["notes"], values["hours"], values["scheduled_completion_date"], closed_at,
         inspection_report_id),
    ).fetchone()
    work_order_id = work_order["id"]

    parts = read_parts_form(company_id, work_order_id)
    if parts:
        db.cursor().executemany(
            "INSERT INTO work_order_parts (company_id, work_order_id, part_name, quantity) VALUES (%s, %s, %s, %s)",
            parts,
        )

    save_work_order_attachments(db, work_order_id, request.files.getlist("attachments"))

    db.commit()
    return redirect("/work-orders")


@bp.route("/work-orders/<int:work_order_id>")
def work_order_detail(work_order_id):
    db = get_db()
    company_id = g.user["company_id"]
    w = db.execute(
        """
        SELECT work_orders.*, vehicles.unit_number, vehicles.year, vehicles.make, vehicles.model, vehicles.vin,
               created_by.full_name AS created_by_name,
               assigned_to.full_name AS assigned_to_name,
               performed_by.full_name AS performed_by_name
        FROM work_orders
        JOIN vehicles ON vehicles.id = work_orders.vehicle_id
        JOIN users AS created_by ON created_by.id = work_orders.created_by_user_id
        LEFT JOIN users AS assigned_to ON assigned_to.id = work_orders.assigned_to_user_id
        LEFT JOIN users AS performed_by ON performed_by.id = work_orders.performed_by_user_id
        WHERE work_orders.id = %s AND work_orders.company_id = %s AND work_orders.deleted_at IS NULL
        """,
        (work_order_id, company_id),
    ).fetchone()

    if w is None:
        abort(404)

    parts = db.execute(
        "SELECT * FROM work_order_parts WHERE work_order_id = %s AND company_id = %s ORDER BY id",
        (work_order_id, company_id),
    ).fetchall()
    attachments = db.execute(
        """
        SELECT * FROM work_order_attachments
        WHERE work_order_id = %s AND company_id = %s
        ORDER BY uploaded_at ASC, id ASC
        """,
        (work_order_id, company_id),
    ).fetchall()
    vehicles = vehicle_choices(w["vehicle_id"])

    parts_lines = "\n".join(
        f"{p['part_name']}" + (f" (x{p['quantity']})" if p["quantity"] > 1 else "") for p in parts
    )
    work_order = {**w, "parts": parts, "attachments": attachments, "parts_lines": parts_lines}

    return render_template(
        "work_order_detail.html",
        w=work_order,
        vehicles=vehicles,
        vehicles_json=vehicles_json_for(vehicles),
        people=people_choices(w["created_by_user_id"], w["assigned_to_user_id"], w["performed_by_user_id"]),
    )


@bp.route("/work-orders/<int:work_order_id>/close", methods=["POST"])
def close_work_order(work_order_id):
    db = get_db()
    get_work_order_or_404(work_order_id)

    performed_by, error = validate_person(request.form.get("performed_by_user_id"), "Completed by", required=True)
    if error:
        flash(error)
        return redirect(f"/work-orders/{work_order_id}")

    db.execute(
        """
        UPDATE work_orders
        SET status = 'closed', closed_at = now(), performed_by_user_id = %s
        WHERE id = %s AND company_id = %s
        """,
        (performed_by, work_order_id, g.user["company_id"]),
    )
    db.commit()
    return redirect(f"/work-orders/{work_order_id}")


@bp.route("/work-orders/<int:work_order_id>/delete", methods=["POST"])
def delete_work_order(work_order_id):
    """Soft delete: the work order is hidden everywhere but kept (with its parts and
    attachment files) for the compliance record."""
    db = get_db()
    get_work_order_or_404(work_order_id)

    db.execute(
        "UPDATE work_orders SET deleted_at = now() WHERE id = %s AND company_id = %s",
        (work_order_id, g.user["company_id"]),
    )
    db.commit()

    flash("Work order deleted.")
    return redirect("/work-orders")


@bp.route("/work-orders/<int:work_order_id>/edit", methods=["POST"])
def edit_work_order(work_order_id):
    db = get_db()
    company_id = g.user["company_id"]
    current = get_work_order_or_404(work_order_id)

    values, error = read_work_order_form(current)
    if error:
        flash(error)
        return redirect(f"/work-orders/{work_order_id}")

    if values["status"] == "closed" and current["status"] != "closed":
        closed_at = datetime.now(timezone.utc)
    elif values["status"] == "open":
        closed_at = None  # still open, or reopened
    else:
        closed_at = current["closed_at"]

    db.execute(
        """
        UPDATE work_orders
        SET vehicle_id = %s, status = %s, created_by_user_id = %s, assigned_to_user_id = %s,
            performed_by_user_id = %s, description = %s, notes = %s, hours = %s,
            scheduled_completion_date = %s, closed_at = %s
        WHERE id = %s AND company_id = %s
        """,
        (values["vehicle_id"], values["status"], values["created_by_user_id"], values["assigned_to_user_id"],
         values["performed_by_user_id"], values["description"], values["notes"], values["hours"],
         values["scheduled_completion_date"], closed_at, work_order_id, company_id),
    )

    # Parts are edited as a whole list: drop the old rows and re-insert what was submitted
    db.execute(
        "DELETE FROM work_order_parts WHERE work_order_id = %s AND company_id = %s", (work_order_id, company_id)
    )
    parts = read_parts_form(company_id, work_order_id)
    if parts:
        db.cursor().executemany(
            "INSERT INTO work_order_parts (company_id, work_order_id, part_name, quantity) VALUES (%s, %s, %s, %s)",
            parts,
        )

    save_work_order_attachments(db, work_order_id, request.files.getlist("attachments"))

    db.commit()
    return redirect(f"/work-orders/{work_order_id}")


def get_attachment_or_404(work_order_id, attachment_id):
    attachment = get_db().execute(
        """
        SELECT work_order_attachments.*
        FROM work_order_attachments
        JOIN work_orders ON work_orders.id = work_order_attachments.work_order_id
        WHERE work_order_attachments.id = %s AND work_order_attachments.work_order_id = %s
            AND work_order_attachments.company_id = %s AND work_orders.deleted_at IS NULL
        """,
        (attachment_id, work_order_id, g.user["company_id"]),
    ).fetchone()
    if attachment is None:
        abort(404)
    return attachment


@bp.route("/work-orders/<int:work_order_id>/attachments/<int:attachment_id>")
def download_attachment(work_order_id, attachment_id):
    attachment = get_attachment_or_404(work_order_id, attachment_id)

    wo_dir = os.path.join(current_app.config["UPLOAD_DIR"], str(work_order_id))
    return send_from_directory(
        wo_dir,
        attachment["stored_filename"],
        mimetype=attachment["content_type"],
        download_name=attachment["original_filename"],
    )


@bp.route("/work-orders/<int:work_order_id>/attachments/<int:attachment_id>/delete", methods=["POST"])
def delete_attachment(work_order_id, attachment_id):
    db = get_db()
    attachment = get_attachment_or_404(work_order_id, attachment_id)

    db.execute(
        "DELETE FROM work_order_attachments WHERE id = %s AND company_id = %s",
        (attachment_id, g.user["company_id"]),
    )
    db.commit()

    # Remove the file only after the row is gone, so a failed commit never leaves a dangling row
    wo_dir = os.path.join(current_app.config["UPLOAD_DIR"], str(work_order_id))
    try:
        os.remove(os.path.join(wo_dir, attachment["stored_filename"]))
    except OSError:
        pass

    return redirect(f"/work-orders/{work_order_id}")
