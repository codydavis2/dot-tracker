"""`flask seed-demo`: create a demo company with realistic sample data for local testing."""
import secrets
from datetime import date, datetime, timedelta, timezone

import click
import psycopg
from flask import current_app
from flask.cli import with_appcontext
from psycopg.rows import dict_row
from werkzeug.security import generate_password_hash

from .config import NOT_CONFIGURED_MESSAGE
from .constants import INSPECTION_CHECKLIST

DEMO_COMPANY_NAME = "Demo Fleet Co"

DEMO_USERS = [
    # (email, full name, role)
    ("owner@demofleet.example.com", "Dana Owner", "owner"),
    ("mechanic@demofleet.example.com", "Mike Ramirez", "mechanic"),
    ("driver@demofleet.example.com", "Darrell Hughes", "driver"),
]

# (unit, year, make, model, VIN, mileage, DOT-regulated, GVWR lbs)
# VINs are made up (right length/characters, not real check digits).
DEMO_VEHICLES = [
    ("101", 2020, "Freightliner", "Cascadia 126", "3AKJHHDR5LSLA4101", 412000, True, 52000),
    ("102", 2019, "Peterbilt", "579", "1XPBDP9X5KD410102", 488500, True, 52000),
    ("103", 2021, "Kenworth", "T680", "1XKYD49X0MJ410103", 301250, True, 52000),
    ("104", 2018, "International", "LT625", "3HSDZAPR5JN410104", 563900, True, 52000),
    ("201", 2022, "Ford", "F-250 Super Duty", "1FT7W2BT4NE410201", 48300, False, 10000),
    ("202", 2021, "Ram", "2500", "3C6UR5CJ1MG410202", 61750, False, 10000),
]


def seed_demo(conn, today=None):
    """Insert the demo company and its data on `conn` (one transaction).
    Returns {email: password} for the created users."""
    today = today or date.today()
    now = datetime.now(timezone.utc)

    def insert(sql, params):
        return conn.execute(sql, params).fetchone()["id"]

    company_id = insert("INSERT INTO companies (name) VALUES (%s) RETURNING id", (DEMO_COMPANY_NAME,))

    passwords = {}
    user_ids = {}
    for email, full_name, role in DEMO_USERS:
        password = secrets.token_urlsafe(9)
        passwords[email] = password
        user_ids[role] = insert(
            """
            INSERT INTO users (company_id, email, password_hash, full_name, role)
            VALUES (%s, %s, %s, %s, %s) RETURNING id
            """,
            (company_id, email, generate_password_hash(password), full_name, role),
        )
    owner, mechanic, driver = user_ids["owner"], user_ids["mechanic"], user_ids["driver"]

    vehicle_ids = {}
    for unit, year, make, model, vin, mileage, is_dot, gvwr in DEMO_VEHICLES:
        vehicle_id = insert(
            """
            INSERT INTO vehicles (company_id, unit_number, make, model, year, vin, mileage, is_dot_regulated, gvwr)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (company_id, unit, make, model, year, vin, mileage, is_dot, gvwr),
        )
        vehicle_ids[unit] = vehicle_id
        if is_dot:
            conn.execute(
                """
                INSERT INTO dot_details (company_id, vehicle_id, cdl_class_required, last_annual_inspection_date)
                VALUES (%s, %s, 'A', %s)
                """,
                (company_id, vehicle_id, today - timedelta(days=300)),
            )

    # Work orders: (unit, description, assigned_to, closed_days_ago or None if open, hours, parts, due_in_days)
    work_orders = [
        ("101", "Replace RR brake chamber — air leak found on pre-trip", mechanic, None, None,
         [("Brake chamber 30/30", 1)], 2),
        ("103", "Steer tire cupping, check alignment", mechanic, None, None, [], -3),
        ("201", "Rear differential service", None, None, None, [("75W-90 gear oil (qt)", 3)], 10),
        ("102", "PM service — oil, fuel and air filters", mechanic, 12, 2.5,
         [("Oil filter", 1), ("Fuel filter", 2), ("Air filter", 1), ("15W-40 oil (gal)", 11)], None),
        ("104", "Replace cracked marker lamp, driver side", mechanic, 30, 0.5, [("LED marker lamp", 1)], None),
    ]
    for unit, description, assigned_to, closed_days_ago, hours, parts, due_in_days in work_orders:
        is_closed = closed_days_ago is not None
        work_order_id = insert(
            """
            INSERT INTO work_orders
                (company_id, vehicle_id, status, created_by_user_id, assigned_to_user_id, performed_by_user_id,
                 description, hours, scheduled_completion_date, created_at, closed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (company_id, vehicle_ids[unit], "closed" if is_closed else "open", owner, assigned_to,
             mechanic if is_closed else None, description, hours,
             today + timedelta(days=due_in_days) if due_in_days is not None else None,
             now - timedelta(days=(closed_days_ago or 0) + 2),
             now - timedelta(days=closed_days_ago) if is_closed else None),
        )
        for part_name, quantity in parts:
            conn.execute(
                "INSERT INTO work_order_parts (company_id, work_order_id, part_name, quantity) VALUES (%s, %s, %s, %s)",
                (company_id, work_order_id, part_name, quantity),
            )

    # Inspection reports: (unit, inspector, days ago, {checklist item: (status, comment)})
    reports = [
        ("101", driver, 1, {"Brakes": ("out_of_spec", "RR chamber leaking, audible air leak")}),
        ("103", mechanic, 5, {"Tires": ("needs_attention", "LF steer tire cupping")}),
        ("102", mechanic, 14, {}),
    ]
    for unit, inspector, days_ago, flagged in reports:
        report_id = insert(
            """
            INSERT INTO inspection_reports
                (company_id, vehicle_id, performed_by_user_id, inspection_type, inspection_date, mileage)
            VALUES (%s, %s, %s, 'general', %s, %s) RETURNING id
            """,
            (company_id, vehicle_ids[unit], inspector, today - timedelta(days=days_ago),
             next(v[5] for v in DEMO_VEHICLES if v[0] == unit)),
        )
        for item in INSPECTION_CHECKLIST:
            status, comments = flagged.get(item, ("good", None))
            conn.execute(
                """
                INSERT INTO inspection_report_items (company_id, inspection_report_id, item, status, comments)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (company_id, report_id, item, status, comments),
            )

    # Reminders: (unit, type, due in days (negative = overdue), assigned to)
    reminders = [
        ("104", "dot_annual", -6, mechanic),     # overdue -> dashboard DOT warning
        ("101", "dot_annual", 9, mechanic),      # due soon
        ("102", "dot_annual", 75, None),
        ("103", "quarterly", 20, mechanic),
        ("201", "monthly", 4, None),
    ]
    for unit, reminder_type, due_in_days, assigned_to in reminders:
        conn.execute(
            """
            INSERT INTO inspection_reminders (company_id, vehicle_id, reminder_type, due_date, assigned_to_user_id)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (company_id, vehicle_ids[unit], reminder_type, today + timedelta(days=due_in_days), assigned_to),
        )

    # Inventory: (part #, name, cost, vendor, location, qty, low-stock threshold)
    inventory = [
        ("LF9009", "Oil filter (Cummins X15)", "38.50", "FleetPride", "Shelf A1", 2, 4),   # low stock
        ("FS1065", "Fuel/water separator", "52.75", "FleetPride", "Shelf A2", 6, 3),
        ("GC3030", "Brake chamber 30/30", "64.99", "TruckPro", "Bay 2, Shelf C", 3, 2),
        ("AF25557", "Air filter, primary", "89.00", "TruckPro", "Shelf B1", 5, 2),
        ("LED-M1", "LED marker lamp, amber", "11.25", "Grote dealer", "Drawer 4", 14, 5),
    ]
    for part_number, name, cost, vendor, location, quantity, threshold in inventory:
        conn.execute(
            """
            INSERT INTO inventory (company_id, part_number, name, cost, vendor, location, quantity, low_stock_threshold)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (company_id, part_number, name, cost, vendor, location, quantity, threshold),
        )

    return passwords


@click.command("seed-demo")
@with_appcontext
def seed_demo_command():
    """Create the "Demo Fleet Co" company with sample users, vehicles and records."""
    if not current_app.config["DATABASE_URL"]:
        raise click.ClickException(NOT_CONFIGURED_MESSAGE)
    with psycopg.connect(current_app.config["DATABASE_URL"], row_factory=dict_row) as conn:
        exists = conn.execute("SELECT 1 FROM companies WHERE name = %s", (DEMO_COMPANY_NAME,)).fetchone()
        if exists:
            raise click.ClickException(f'"{DEMO_COMPANY_NAME}" already exists; not seeding again.')
        try:
            passwords = seed_demo(conn)
        except psycopg.errors.UniqueViolation as e:
            raise click.ClickException(f"Could not seed demo data: {e.diag.message_primary}")
        # leaving the `with` block commits

    click.echo(f'Created "{DEMO_COMPANY_NAME}". Log in with:')
    for email, full_name, role in DEMO_USERS:
        click.echo(f"  {role:<9} {email:<34} password: {passwords[email]}")


def init_app(app):
    app.cli.add_command(seed_demo_command)
