from datetime import date, datetime

from flask import Blueprint, flash, g, render_template, request

from .constants import INSPECTION_STATUS_LABELS, INSPECTION_TYPE_LABELS
from .db import get_db
from .vehicles import vehicle_choices

bp = Blueprint("audit", __name__)


@bp.route("/audit")
def audit():
    db = get_db()
    company_id = g.user["company_id"]
    vehicles = vehicle_choices()

    vehicle_id = request.args.get("vehicle_id") or ""
    doc_types = request.args.getlist("doc_type")
    # Inspection types to include; none ticked means all types
    inspection_types = [t for t in request.args.getlist("inspection_type") if t in INSPECTION_TYPE_LABELS]
    start_date = request.args.get("start_date") or ""
    end_date = request.args.get("end_date") or ""

    ran_report = bool(start_date and end_date)
    work_orders = []
    inspection_reports = []
    selected_vehicle = None

    if ran_report:
        try:
            start = date.fromisoformat(start_date)
            end = date.fromisoformat(end_date)
        except ValueError:
            flash("Enter valid From and To dates.")
            ran_report = False

    if ran_report:
        if not doc_types:
            doc_types = ["work_orders", "inspections"]

        if vehicle_id.isdigit():
            selected_vehicle = db.execute(
                "SELECT id, unit_number, year, make, model, vin FROM vehicles WHERE id = %s AND company_id = %s",
                (int(vehicle_id), company_id),
            ).fetchone()
        # None means "all assets"; a vehicle id from another company also falls back to all of
        # *this* company's assets, so nothing outside the company is ever included.
        selected_vehicle_id = selected_vehicle["id"] if selected_vehicle else None
        params = {"company_id": company_id, "start": start, "end": end, "vehicle_id": selected_vehicle_id,
                  "inspection_types": inspection_types or list(INSPECTION_TYPE_LABELS)}

        if "work_orders" in doc_types:
            wo_rows = db.execute(
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
                WHERE work_orders.company_id = %(company_id)s
                    AND work_orders.deleted_at IS NULL
                    AND work_orders.status = 'closed'
                    AND (work_orders.closed_at AT TIME ZONE 'UTC')::date BETWEEN %(start)s AND %(end)s
                    AND (%(vehicle_id)s::bigint IS NULL OR work_orders.vehicle_id = %(vehicle_id)s)
                ORDER BY work_orders.closed_at ASC
                """,
                params,
            ).fetchall()

            wo_ids = [w["id"] for w in wo_rows]
            parts_by_wo = {}
            attachments_by_wo = {}
            if wo_ids:
                for p in db.execute(
                    """
                    SELECT * FROM work_order_parts
                    WHERE company_id = %s AND work_order_id = ANY(%s)
                    ORDER BY id
                    """,
                    (company_id, wo_ids),
                ).fetchall():
                    parts_by_wo.setdefault(p["work_order_id"], []).append(p)
                for a in db.execute(
                    """
                    SELECT * FROM work_order_attachments
                    WHERE company_id = %s AND work_order_id = ANY(%s)
                    ORDER BY uploaded_at ASC, id ASC
                    """,
                    (company_id, wo_ids),
                ).fetchall():
                    attachments_by_wo.setdefault(a["work_order_id"], []).append(a)

            work_orders = []
            for w in wo_rows:
                parts = parts_by_wo.get(w["id"], [])
                parts_lines = "\n".join(
                    f"{p['part_name']}" + (f" (x{p['quantity']})" if p["quantity"] > 1 else "") for p in parts
                )
                work_orders.append({
                    **w,
                    "parts": parts,
                    "parts_lines": parts_lines,
                    "attachments": attachments_by_wo.get(w["id"], []),
                })

        if "inspections" in doc_types:
            report_rows = db.execute(
                """
                SELECT inspection_reports.*, vehicles.unit_number, vehicles.year, vehicles.make, vehicles.model, vehicles.vin,
                       performed_by.full_name AS performed_by_name
                FROM inspection_reports
                JOIN vehicles ON vehicles.id = inspection_reports.vehicle_id
                JOIN users AS performed_by ON performed_by.id = inspection_reports.performed_by_user_id
                WHERE inspection_reports.company_id = %(company_id)s
                    AND inspection_reports.deleted_at IS NULL
                    AND inspection_reports.inspection_date BETWEEN %(start)s AND %(end)s
                    AND (%(vehicle_id)s::bigint IS NULL OR inspection_reports.vehicle_id = %(vehicle_id)s)
                    AND inspection_reports.inspection_type = ANY(%(inspection_types)s)
                ORDER BY inspection_reports.inspection_date ASC, inspection_reports.id ASC
                """,
                params,
            ).fetchall()

            report_ids = [r["id"] for r in report_rows]
            items_by_report = {}
            if report_ids:
                for i in db.execute(
                    """
                    SELECT * FROM inspection_report_items
                    WHERE company_id = %s AND inspection_report_id = ANY(%s)
                    ORDER BY id
                    """,
                    (company_id, report_ids),
                ).fetchall():
                    items_by_report.setdefault(i["inspection_report_id"], []).append(i)
            inspection_reports = [
                {**r, "checklist_items": items_by_report.get(r["id"], [])} for r in report_rows
            ]

    return render_template(
        "audit.html",
        vehicles=vehicles,
        vehicle_id=vehicle_id,
        doc_types=doc_types,
        inspection_types=inspection_types,
        start_date=start_date,
        end_date=end_date,
        ran_report=ran_report,
        selected_vehicle=selected_vehicle,
        work_orders=work_orders,
        inspection_reports=inspection_reports,
        status_labels=INSPECTION_STATUS_LABELS,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
