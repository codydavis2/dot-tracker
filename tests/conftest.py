"""Shared pytest fixtures.

Tests run against a real PostgreSQL database (TEST_DATABASE_URL, by default
<POSTGRES_DB>_test on the docker-compose server). Once per test session the
database is wiped and rebuilt by running every migration; between tests every
table is truncated. Attachments go to a temporary folder per test.

Most tests use two companies, A and B, each with an owner and a mechanic.
"""
import re
import sys
from pathlib import Path

import psycopg
import pytest
from flask import template_rendered
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from app.config import NOT_CONFIGURED_MESSAGE, database_url, test_database_url  # noqa: E402
from app.migrate import apply_migrations  # noqa: E402

TEST_DATABASE_URL = test_database_url()
PASSWORD = "pw123"
PASSWORD_HASH = generate_password_hash(PASSWORD)  # hashed once; hashing is slow on purpose

# Tables filled by migrations rather than by tests — never truncated.
KEEP_TABLES = ("schema_migrations", "dtc_reference")


def _connect_or_create_test_database():
    try:
        return psycopg.connect(TEST_DATABASE_URL, autocommit=True, connect_timeout=5)
    except psycopg.OperationalError as e:
        dbname = conninfo_to_dict(TEST_DATABASE_URL).get("dbname")
        if f'database "{dbname}" does not exist' not in str(e):
            pytest.exit(
                f"Can't connect to the test database: {e}\n"
                "Is Postgres running? Start it with:  docker compose up -d",
                returncode=1,
            )
    # The server is up but the test database doesn't exist yet: create it.
    with psycopg.connect(make_conninfo(TEST_DATABASE_URL, dbname="postgres"), autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    return psycopg.connect(TEST_DATABASE_URL, autocommit=True, connect_timeout=5)


@pytest.fixture(scope="session", autouse=True)
def test_database():
    """Rebuild the test database from migrations once per test session."""
    if not TEST_DATABASE_URL:
        pytest.exit(NOT_CONFIGURED_MESSAGE, returncode=1)
    test_params = conninfo_to_dict(TEST_DATABASE_URL)
    dev_params = conninfo_to_dict(database_url() or "")
    if all(test_params.get(k) == dev_params.get(k) for k in ("host", "port", "dbname")):
        pytest.exit("TEST_DATABASE_URL points at the app's own database; refusing to wipe it.", returncode=1)

    with _connect_or_create_test_database() as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    apply_migrations(TEST_DATABASE_URL, echo=lambda *args: None)


@pytest.fixture
def db():
    """Direct autocommit connection to the test database, for setup and assertions."""
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True, row_factory=dict_row) as conn:
        yield conn


@pytest.fixture(autouse=True)
def clean_tables(db):
    tables = [r["tablename"] for r in db.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND NOT (tablename = ANY(%s))",
        (list(KEEP_TABLES),),
    )]
    db.execute(
        sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(sql.SQL(", ").join(map(sql.Identifier, tables)))
    )


@pytest.fixture
def query(db):
    """Run SQL directly: query(sql, params) -> list of dict rows ([] for statements without results)."""
    def run(statement, params=()):
        cur = db.execute(statement, params)
        return cur.fetchall() if cur.description else []
    return run


@pytest.fixture
def make_app(tmp_path):
    """Build an app with the test config plus overrides, e.g. make_app(WTF_CSRF_ENABLED=True)."""
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(exist_ok=True)

    def build(**overrides):
        config = {
            "TESTING": True,
            "DATABASE_URL": TEST_DATABASE_URL,
            "UPLOAD_DIR": str(upload_dir),
            # Off for the general suite; tests that cover them turn them back on.
            "WTF_CSRF_ENABLED": False,
            "RATELIMIT_ENABLED": False,
        }
        config.update(overrides)
        return create_app(config)
    return build


@pytest.fixture
def app(make_app):
    return make_app()


@pytest.fixture
def outbox(monkeypatch):
    """Capture outgoing email instead of printing it. Returns the list of sent messages
    (dicts with from / to / subject / body_text)."""
    sent = []
    monkeypatch.setattr("app.email._deliver", sent.append)
    return sent


def email_link(message):
    """The http(s) link in an email body."""
    match = re.search(r"https?://\S+", message["body_text"])
    assert match, f"no link in email: {message['body_text']!r}"
    return match.group(0)


def link_path(link):
    """/reset-password/abc from http://localhost/reset-password/abc"""
    return "/" + link.split("://", 1)[1].split("/", 1)[1]


@pytest.fixture
def captured(app):
    """Record (template_name, context) for every template rendered during a test."""
    recorded = []

    def record(sender, template, context, **extra):
        recorded.append((template.name, context))

    template_rendered.connect(record, app)
    yield recorded
    template_rendered.disconnect(record, app)


# ---------- companies and users ----------

def make_company(query, name):
    return query("INSERT INTO companies (name) VALUES (%s) RETURNING id", (name,))[0]["id"]


def make_user(query, company_id, email, full_name, role="mechanic", is_active=True):
    return query(
        """
        INSERT INTO users (company_id, email, password_hash, full_name, role, is_active)
        VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (company_id, email, PASSWORD_HASH, full_name, role, is_active),
    )[0]["id"]


@pytest.fixture
def users(query):
    """Company A and company B, each with an owner and a mechanic. Returns their ids."""
    ids = {}
    for key in ("a", "b"):
        company_id = make_company(query, f"Company {key.upper()}")
        ids[f"{key}_company"] = company_id
        ids[f"{key}_owner"] = make_user(query, company_id, f"owner@{key}.example.com", f"Owner {key.upper()}", "owner")
        ids[f"{key}_mechanic"] = make_user(query, company_id, f"mechanic@{key}.example.com", f"Mechanic {key.upper()}")
    return ids


def login(client, email, password=PASSWORD):
    return client.post("/login", data={"email": email, "password": password})


def logged_in_client(app, email):
    client = app.test_client()
    resp = login(client, email)
    # Drivers land on /inspections, everyone else on the dashboard
    assert resp.headers["Location"] in ("/", "/inspections"), f"login failed for {email}"
    return client


@pytest.fixture
def roles(users, query):
    """`users` plus a manager and a driver in company A (a_manager, a_driver)."""
    ids = dict(users)
    ids["a_manager"] = make_user(query, users["a_company"], "manager@a.example.com", "Manager A", "manager")
    ids["a_driver"] = make_user(query, users["a_company"], "driver@a.example.com", "Driver A", "driver")
    return ids


@pytest.fixture
def client(app, users):
    """Logged in as company A's owner."""
    return logged_in_client(app, "owner@a.example.com")


@pytest.fixture
def mechanic_client(app, users):
    """Logged in as company A's mechanic."""
    return logged_in_client(app, "mechanic@a.example.com")


@pytest.fixture
def manager_client(app, roles):
    """Logged in as company A's manager."""
    return logged_in_client(app, "manager@a.example.com")


@pytest.fixture
def driver_client(app, roles):
    """Logged in as company A's driver."""
    return logged_in_client(app, "driver@a.example.com")


@pytest.fixture
def other_client(app, users):
    """Logged in as company B's owner."""
    return logged_in_client(app, "owner@b.example.com")


@pytest.fixture
def anon(app):
    """A client that is not logged in."""
    return app.test_client()


def force_login(client, query, user_id):
    """Log a client in by writing its session directly (for apps where posting the
    login form would need a CSRF token or count against the rate limit)."""
    auth_version = query("SELECT auth_version FROM users WHERE id = %s", (user_id,))[0]["auth_version"]
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["auth_version"] = auth_version
    return client


def current_user_id(client):
    with client.session_transaction() as sess:
        return sess["user_id"]


# ---------- creating records through the UI routes ----------

def add_vehicle(client, **overrides):
    """Create a vehicle through the UI route and return its id."""
    data = {"unit_number": "42", "make": "Freightliner", "model": "Cascadia", "year": "2019",
            "vin": "", "mileage": "350000", "gvwr": "52000"}
    data.update(overrides)
    resp = client.post("/vehicles", data=data)
    assert resp.status_code == 302 and resp.headers["Location"].startswith("/vehicles/"), flashes(client)
    return int(resp.headers["Location"].rsplit("/", 1)[-1])


def add_work_order(client, query, vehicle_id, **overrides):
    """Create a work order through the UI route and return its id."""
    me = str(current_user_id(client))
    data = {"vehicle_id": str(vehicle_id), "status": "open", "created_by_user_id": me,
            "assigned_to_user_id": "", "performed_by_user_id": me, "description": "Replace brake chamber",
            "notes": "", "hours": "1.5", "scheduled_completion_date": ""}
    data.update(overrides)
    resp = client.post("/work-orders", data=data, content_type="multipart/form-data")
    assert resp.status_code == 302
    rows = query("SELECT max(id) AS id FROM work_orders")
    assert rows[0]["id"] is not None, flashes(client)
    return rows[0]["id"]


def add_inspection_report(client, query, vehicle_id, **overrides):
    data = {"vehicle_id": str(vehicle_id), "performed_by_user_id": str(current_user_id(client)),
            "inspection_type": "general", "inspection_date": "2026-03-15", "mileage": "351000", "notes": ""}
    data.update(overrides)
    resp = client.post("/inspections/reports", data=data)
    assert resp.status_code == 302 and "/inspections/reports/" in resp.headers["Location"], flashes(client)
    return query("SELECT max(id) AS id FROM inspection_reports")[0]["id"]


def add_inventory_item(client, query, **overrides):
    data = {"part_number": "K-123", "name": "Brake chamber", "cost": "54.99", "vendor": "FleetPride",
            "location": "Bay 2", "quantity": "5", "low_stock_threshold": "2"}
    data.update(overrides)
    resp = client.post("/inventory", data=data)
    assert resp.status_code == 302
    return query("SELECT max(id) AS id FROM inventory")[0]["id"]


def add_reminder(client, query, vehicle_id, **overrides):
    data = {"reminder_type": "dot_annual", "due_date": "2027-01-15", "interval": "none", "assigned_to_user_id": ""}
    data.update(overrides)
    resp = client.post(f"/vehicles/{vehicle_id}/reminders", data=data)
    assert resp.status_code == 302
    return query("SELECT max(id) AS id FROM inspection_reminders")[0]["id"]


def flashes(client):
    """Return and clear the flash messages queued in the client's session
    (the same thing rendering the next page would do). Skips the console-mode
    "dev_link" notices that show emailed links on screen (covered in test_email.py)."""
    with client.session_transaction() as sess:
        return [msg for category, msg in sess.pop("_flashes", []) if category != "dev_link"]


def select_options(html, name):
    """Parse a rendered <select name=...>: returns (list of option values, the selected value or None)."""
    match = re.search(rf'<select[^>]*name="{re.escape(name)}"[^>]*>(.*?)</select>', html, re.S)
    assert match, f"no <select name={name!r}> on the page"
    options = re.findall(r'<option value="([^"]*)"([^>]*)>', match.group(1))
    values = [value for value, _ in options]
    selected = next((value for value, attrs in options if "selected" in attrs.split()), None)
    return values, selected
