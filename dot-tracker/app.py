import calendar as calendar_module
import json
import os
import shutil
import sqlite3
import uuid
from datetime import date, datetime

from flask import Flask, abort, flash, redirect, render_template, request, send_from_directory, session
from flask_session import Session
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from helpers import generate_reminder_dates, login_required, reminder_status, usd

app = Flask(__name__)

# Jinja filters
app.jinja_env.filters["usd"] = usd

# Server-side sessions (same pattern as CS50 Finance)
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

DATABASE = "dot_tracker.db"

# Work order attachments (invoices, quotes, photos) — stored outside static/ so
# they're only reachable through the login-gated download route, not served directly.
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads", "work_orders")
os.makedirs(UPLOAD_DIR, exist_ok=True)
ALLOWED_ATTACHMENT_EXTENSIONS = {
    "png", "jpg", "jpeg", "gif", "webp", "heic",
    "pdf", "doc", "docx", "xls", "xlsx", "txt", "csv",
}
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB per request

# Vehicle inspection checklist. Item text is copied onto each submitted report,
# so changing this list only affects inspections filled out afterward.
INSPECTION_CHECKLIST = [
    "Oil", "Coolant", "Belts", "Exhaust", "Intake", "Fuel",
    "Springs", "Shocks",
    "Brakes", "ABS", "Tires", "Rims", "Frame", "Lights", "Electrical",
    "Reflectors", "Windshield", "Steering", "Battery", "Coupling Devices",
    "Horn", "Mirrors", "Error Codes", "Misc.",
]
INSPECTION_STATUS_LABELS = {"good": "Good", "needs_attention": "Needs Attention", "out_of_spec": "Out of Spec"}


def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@app.after_request
def after_request(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.errorhandler(413)
def file_too_large(e):
    flash("That upload is too large. Max total upload size is 20 MB.")
    return redirect(request.referrer or "/work-orders")


def save_work_order_attachments(db, work_order_id, files):
    """Save uploaded files for a work order to disk and record them in the DB.
    Silently skips empty file inputs and disallowed extensions."""
    wo_dir = os.path.join(UPLOAD_DIR, str(work_order_id))
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


# ---------- AUTH ----------

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        confirmation = request.form.get("confirmation")

        if not username or not password:
            flash("Username and password are required.")
            return redirect("/register")
        if password != confirmation:
            flash("Passwords do not match.")
            return redirect("/register")

        db = get_db()
        try:
            db.execute(
                "INSERT INTO users (username, hash) VALUES (?, ?)",
                (username, generate_password_hash(password)),
            )
            db.commit()
        except sqlite3.IntegrityError:
            flash("Username already taken.")
            return redirect("/register")
        finally:
            db.close()

        return redirect("/login")

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    session.clear()

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        db = get_db()
        row = db.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        db.close()

        if row is None or not check_password_hash(row["hash"], password):
            flash("Invalid username or password.")
            return redirect("/login")

        session["user_id"] = row["id"]
        session["username"] = row["username"]
        return redirect("/")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


# ---------- DASHBOARD ----------

@app.route("/")
@login_required
def index():
    db = get_db()

    vehicles_all = db.execute(
        "SELECT * FROM vehicles WHERE user_id = ?", (session["user_id"],)
    ).fetchall()
    vehicles = vehicles_all[:10]
    more_vehicles = len(vehicles_all) > 10

    # Pull all open reminders for this user's vehicles, for the DOT compliance warning banner
    reminders = db.execute(
        """
        SELECT inspection_reminders.*, vehicles.unit_number, vehicles.make, vehicles.model, vehicles.year
        FROM inspection_reminders
        JOIN vehicles ON vehicles.id = inspection_reminders.vehicle_id
        WHERE vehicles.user_id = ? AND inspection_reminders.status != 'completed'
        ORDER BY inspection_reminders.due_date ASC
        """,
        (session["user_id"],),
    ).fetchall()

    # Attach a status bucket (overdue / due_soon / upcoming) for template coloring
    reminders_with_status = [
        {**dict(r), "bucket": reminder_status(r["due_date"])} for r in reminders
    ]

    # Any DOT vehicle with an overdue annual inspection gets a hard warning
    dot_warnings = [
        r for r in reminders_with_status
        if r["reminder_type"] == "dot_annual" and r["bucket"] == "overdue"
    ]

    today = date.today()

    open_work_order_rows = db.execute(
        """
        SELECT work_orders.*, vehicles.unit_number, vehicles.make, vehicles.model, vehicles.year
        FROM work_orders
        JOIN vehicles ON vehicles.id = work_orders.vehicle_id
        WHERE vehicles.user_id = ? AND work_orders.status = 'open'
        ORDER BY
            CASE WHEN work_orders.scheduled_completion_date IS NULL THEN 1 ELSE 0 END,
            work_orders.scheduled_completion_date ASC
        """,
        (session["user_id"],),
    ).fetchall()
    open_work_orders_all = [
        {
            **dict(w),
            "overdue": bool(
                w["scheduled_completion_date"] and w["scheduled_completion_date"] < today.isoformat()
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
        WHERE vehicles.user_id = ? AND inspection_reminders.due_date BETWEEN ? AND ?
        """,
        (session["user_id"], first_day.isoformat(), last_day.isoformat()),
    ).fetchall()

    calendar_work_orders = db.execute(
        """
        SELECT work_orders.*, vehicles.unit_number, vehicles.make, vehicles.model, vehicles.year
        FROM work_orders
        JOIN vehicles ON vehicles.id = work_orders.vehicle_id
        WHERE vehicles.user_id = ? AND work_orders.scheduled_completion_date BETWEEN ? AND ?
        """,
        (session["user_id"], first_day.isoformat(), last_day.isoformat()),
    ).fetchall()
    db.close()

    events_by_day = {}
    for r in calendar_reminders:
        day_num = int(r["due_date"][8:10])
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
        day_num = int(w["scheduled_completion_date"][8:10])
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


# ---------- VEHICLES ----------

@app.route("/vehicles", methods=["GET", "POST"])
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


@app.route("/vehicles/<int:vehicle_id>")
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

@app.route("/vehicles/<int:vehicle_id>/maintenance", methods=["POST"])
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

@app.route("/vehicles/<int:vehicle_id>/dtc", methods=["POST"])
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


# ---------- INSPECTIONS ----------

@app.route("/inspections")
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


@app.route("/inspections/reports", methods=["POST"])
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


@app.route("/inspections/reports/<int:report_id>")
@login_required
def inspection_report_detail(report_id):
    db = get_db()
    report = db.execute(
        """
        SELECT inspection_reports.*, vehicles.unit_number, vehicles.year, vehicles.make, vehicles.model
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

@app.route("/vehicles/<int:vehicle_id>/reminders", methods=["POST"])
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


# ---------- WORK ORDERS ----------

@app.route("/work-orders", methods=["GET", "POST"])
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


@app.route("/work-orders/<int:work_order_id>")
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

    work_order = {**dict(w), "parts": parts, "attachments": attachments}

    return render_template(
        "work_order_detail.html",
        w=work_order,
        vehicles=vehicles,
        vehicles_json=vehicles_json,
    )


@app.route("/work-orders/<int:work_order_id>/close", methods=["POST"])
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


@app.route("/work-orders/<int:work_order_id>/delete", methods=["POST"])
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

    wo_dir = os.path.join(UPLOAD_DIR, str(work_order_id))
    shutil.rmtree(wo_dir, ignore_errors=True)

    flash("Work order deleted.")
    return redirect("/work-orders")


@app.route("/work-orders/<int:work_order_id>/edit", methods=["POST"])
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


@app.route("/work-orders/<int:work_order_id>/attachments/<int:attachment_id>")
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

    wo_dir = os.path.join(UPLOAD_DIR, str(work_order_id))
    return send_from_directory(
        wo_dir,
        attachment["stored_filename"],
        mimetype=attachment["content_type"],
        download_name=attachment["original_filename"],
    )


@app.route("/work-orders/<int:work_order_id>/attachments/<int:attachment_id>/delete", methods=["POST"])
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

    wo_dir = os.path.join(UPLOAD_DIR, str(work_order_id))
    try:
        os.remove(os.path.join(wo_dir, attachment["stored_filename"]))
    except OSError:
        pass

    db.execute("DELETE FROM work_order_attachments WHERE id = ?", (attachment_id,))
    db.commit()
    db.close()
    return redirect(f"/work-orders/{work_order_id}")


@app.route("/reminders/<int:reminder_id>/complete", methods=["POST"])
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


# ---------- INVENTORY ----------

@app.route("/inventory", methods=["GET", "POST"])
@login_required
def inventory():
    db = get_db()

    if request.method == "POST":
        part_number = request.form.get("part_number") or None
        name = request.form.get("name")
        cost = request.form.get("cost") or None
        vendor = request.form.get("vendor") or None
        location = request.form.get("location") or None
        quantity = request.form.get("quantity") or 0
        low_stock_threshold = request.form.get("low_stock_threshold") or None

        if not name:
            db.close()
            flash("Part name is required.")
            return redirect("/inventory")

        db.execute(
            """
            INSERT INTO inventory
                (user_id, part_number, name, cost, vendor, location, quantity, low_stock_threshold)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (session["user_id"], part_number, name, cost, vendor, location, quantity, low_stock_threshold),
        )
        db.commit()
        db.close()
        return redirect("/inventory")

    rows = db.execute(
        "SELECT * FROM inventory WHERE user_id = ? ORDER BY name", (session["user_id"],)
    ).fetchall()
    db.close()

    items = [
        {
            **dict(r),
            "low_stock": r["low_stock_threshold"] is not None and r["quantity"] <= r["low_stock_threshold"],
        }
        for r in rows
    ]

    return render_template("inventory.html", items=items)


@app.route("/inventory/<int:item_id>/edit", methods=["POST"])
@login_required
def edit_inventory_item(item_id):
    db = get_db()
    owned = db.execute(
        "SELECT id FROM inventory WHERE id = ? AND user_id = ?",
        (item_id, session["user_id"]),
    ).fetchone()
    if owned is None:
        db.close()
        flash("Inventory item not found.")
        return redirect("/inventory")

    part_number = request.form.get("part_number") or None
    name = request.form.get("name")
    cost = request.form.get("cost") or None
    vendor = request.form.get("vendor") or None
    location = request.form.get("location") or None
    quantity = request.form.get("quantity") or 0
    low_stock_threshold = request.form.get("low_stock_threshold") or None

    if not name:
        db.close()
        flash("Part name is required.")
        return redirect("/inventory")

    db.execute(
        """
        UPDATE inventory
        SET part_number = ?, name = ?, cost = ?, vendor = ?, location = ?, quantity = ?, low_stock_threshold = ?
        WHERE id = ?
        """,
        (part_number, name, cost, vendor, location, quantity, low_stock_threshold, item_id),
    )
    db.commit()
    db.close()
    return redirect("/inventory")


if __name__ == "__main__":
    app.run(debug=True)
