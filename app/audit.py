from datetime import datetime

from flask import Blueprint, render_template, request, session

from .constants import INSPECTION_STATUS_LABELS
from .db import get_db
from .helpers import login_required

bp = Blueprint("audit", __name__)


@bp.route("/audit")
@login_required
def audit():
    db = get_db()
    vehicles = db.execute(
        "SELECT id, unit_number, year, make, model, vin FROM vehicles WHERE user_id = ? ORDER BY unit_number",
        (session["user_id"],),
    ).fetchall()

    vehicle_id = request.args.get("vehicle_id") or ""
    doc_types = request.args.getlist("doc_type")
    start_date = request.args.get("start_date") or ""
    end_date = request.args.get("end_date") or ""

    ran_report = bool(start_date and end_date)
    work_orders = []
    inspection_reports = []
    selected_vehicle = None

    if ran_report:
        if not doc_types:
            doc_types = ["work_orders", "inspections"]

        if vehicle_id:
            selected_vehicle = db.execute(
                "SELECT id, unit_number, year, make, model, vin FROM vehicles WHERE id = ? AND user_id = ?",
                (vehicle_id, session["user_id"]),
            ).fetchone()

        vehicle_clause = " AND vehicles.id = ?" if selected_vehicle else ""
        vehicle_param = [vehicle_id] if selected_vehicle else []

        if "work_orders" in doc_types:
            wo_rows = db.execute(
                f"""
                SELECT work_orders.*, vehicles.unit_number, vehicles.year, vehicles.make, vehicles.model, vehicles.vin
                FROM work_orders
                JOIN vehicles ON vehicles.id = work_orders.vehicle_id
                WHERE vehicles.user_id = ? AND work_orders.status = 'closed'
                    AND date(work_orders.closed_at) BETWEEN ? AND ?{vehicle_clause}
                ORDER BY work_orders.closed_at ASC
                """,
                [session["user_id"], start_date, end_date] + vehicle_param,
            ).fetchall()

            wo_ids = [w["id"] for w in wo_rows]
            parts_by_wo = {}
            attachments_by_wo = {}
            if wo_ids:
                placeholders = ",".join("?" * len(wo_ids))
                for p in db.execute(
                    f"SELECT * FROM work_order_parts WHERE work_order_id IN ({placeholders})", wo_ids
                ).fetchall():
                    parts_by_wo.setdefault(p["work_order_id"], []).append(p)
                for a in db.execute(
                    f"""
                    SELECT * FROM work_order_attachments
                    WHERE work_order_id IN ({placeholders})
                    ORDER BY uploaded_at ASC
                    """,
                    wo_ids,
                ).fetchall():
                    attachments_by_wo.setdefault(a["work_order_id"], []).append(a)

            work_orders = []
            for w in wo_rows:
                parts = parts_by_wo.get(w["id"], [])
                parts_lines = "\n".join(
                    f"{p['part_name']}" + (f" (x{p['quantity']})" if p["quantity"] > 1 else "") for p in parts
                )
                work_orders.append({
                    **dict(w),
                    "parts": parts,
                    "parts_lines": parts_lines,
                    "attachments": attachments_by_wo.get(w["id"], []),
                })

        if "inspections" in doc_types:
            report_rows = db.execute(
                f"""
                SELECT inspection_reports.*, vehicles.unit_number, vehicles.year, vehicles.make, vehicles.model, vehicles.vin
                FROM inspection_reports
                JOIN vehicles ON vehicles.id = inspection_reports.vehicle_id
                WHERE vehicles.user_id = ?
                    AND inspection_reports.inspection_date BETWEEN ? AND ?{vehicle_clause}
                ORDER BY inspection_reports.inspection_date ASC
                """,
                [session["user_id"], start_date, end_date] + vehicle_param,
            ).fetchall()

            report_ids = [r["id"] for r in report_rows]
            items_by_report = {}
            if report_ids:
                placeholders = ",".join("?" * len(report_ids))
                for i in db.execute(
                    f"SELECT * FROM inspection_report_items WHERE inspection_report_id IN ({placeholders}) ORDER BY id",
                    report_ids,
                ).fetchall():
                    items_by_report.setdefault(i["inspection_report_id"], []).append(i)
            inspection_reports = [
                {**dict(r), "checklist_items": items_by_report.get(r["id"], [])} for r in report_rows
            ]

    db.close()

    return render_template(
        "audit.html",
        vehicles=vehicles,
        vehicle_id=vehicle_id,
        doc_types=doc_types,
        start_date=start_date,
        end_date=end_date,
        ran_report=ran_report,
        selected_vehicle=selected_vehicle,
        work_orders=work_orders,
        inspection_reports=inspection_reports,
        status_labels=INSPECTION_STATUS_LABELS,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
