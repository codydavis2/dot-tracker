import sqlite3
from datetime import date

from flask import Flask, flash, redirect, render_template, request, session
from flask_session import Session
from werkzeug.security import check_password_hash, generate_password_hash

from helpers import generate_reminder_dates, login_required, reminder_status, usd

app = Flask(__name__)

# Jinja filters
app.jinja_env.filters["usd"] = usd

# Server-side sessions (same pattern as CS50 Finance)
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

DATABASE = "dot_tracker.db"


def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@app.after_request
def after_request(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


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

    vehicles = db.execute(
        "SELECT * FROM vehicles WHERE user_id = ?", (session["user_id"],)
    ).fetchall()

    # Pull reminders for this user's vehicles, soonest due first
    reminders = db.execute(
        """
        SELECT inspection_reminders.*, vehicles.make, vehicles.model, vehicles.year
        FROM inspection_reminders
        JOIN vehicles ON vehicles.id = inspection_reminders.vehicle_id
        WHERE vehicles.user_id = ? AND inspection_reminders.status != 'completed'
        ORDER BY inspection_reminders.due_date ASC
        """,
        (session["user_id"],),
    ).fetchall()
    db.close()

    # Attach a status bucket (overdue / due_soon / upcoming) for template coloring
    reminders_with_status = [
        {**dict(r), "bucket": reminder_status(r["due_date"])} for r in reminders
    ]

    # Any DOT vehicle with an overdue annual inspection gets a hard warning
    dot_warnings = [
        r for r in reminders_with_status
        if r["reminder_type"] == "dot_annual" and r["bucket"] == "overdue"
    ]

    return render_template(
        "dashboard.html",
        vehicles=vehicles,
        reminders=reminders_with_status,
        dot_warnings=dot_warnings,
    )


# ---------- VEHICLES ----------

@app.route("/vehicles", methods=["GET", "POST"])
@login_required
def vehicles():
    db = get_db()

    if request.method == "POST":
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

        cur = db.execute(
            """
            INSERT INTO vehicles (user_id, make, model, year, vin, mileage, is_dot_regulated, gvwr)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (session["user_id"], make, model, year, vin, mileage, is_dot, gvwr),
        )
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

    maintenance = db.execute(
        "SELECT * FROM maintenance_logs WHERE vehicle_id = ? ORDER BY log_date DESC",
        (vehicle_id,),
    ).fetchall()

    parts_rows = db.execute(
        """
        SELECT maintenance_parts.*
        FROM maintenance_parts
        JOIN maintenance_logs ON maintenance_logs.id = maintenance_parts.maintenance_log_id
        WHERE maintenance_logs.vehicle_id = ?
        """,
        (vehicle_id,),
    ).fetchall()
    parts_by_log = {}
    for p in parts_rows:
        parts_by_log.setdefault(p["maintenance_log_id"], []).append(p)

    dtc_codes = db.execute(
        "SELECT * FROM dtc_codes WHERE vehicle_id = ? ORDER BY date_logged DESC",
        (vehicle_id,),
    ).fetchall()

    reminders = db.execute(
        "SELECT * FROM inspection_reminders WHERE vehicle_id = ? ORDER BY due_date ASC",
        (vehicle_id,),
    ).fetchall()
    db.close()

    reminders_with_status = [
        {**dict(r), "bucket": reminder_status(r["due_date"])} for r in reminders
    ]
    maintenance_with_parts = [
        {**dict(m), "parts": parts_by_log.get(m["id"], [])} for m in maintenance
    ]

    return render_template(
        "vehicle_detail.html",
        vehicle=vehicle,
        dot_details=dot_details,
        maintenance=maintenance_with_parts,
        dtc_codes=dtc_codes,
        reminders=reminders_with_status,
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
    return redirect(f"/vehicles/{vehicle_id}")


if __name__ == "__main__":
    app.run(debug=True)
