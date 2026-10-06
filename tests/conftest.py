"""Shared pytest fixtures.

Every test gets a fresh SQLite database built from schema.sql and an empty
upload directory, both inside pytest's tmp_path, so tests never touch the
real dot_tracker.db or uploads/ folder.
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest
from flask import template_rendered

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Point the app at throwaway locations *before* it is imported, so importing it
# doesn't create folders inside the repo. Each test then gets its own paths below.
_import_dir = tempfile.mkdtemp(prefix="dot-tracker-import-")
os.environ["DATABASE"] = os.path.join(_import_dir, "unused.db")
os.environ["UPLOAD_DIR"] = os.path.join(_import_dir, "uploads")

from app import create_app  # noqa: E402


@pytest.fixture
def app(tmp_path):
    db_path = tmp_path / "test.db"
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()

    conn = sqlite3.connect(db_path)
    conn.executescript((ROOT / "schema.sql").read_text())
    conn.close()

    return create_app({
        "TESTING": True,
        "DATABASE": str(db_path),
        "UPLOAD_DIR": str(upload_dir),
    })


@pytest.fixture
def query(app):
    """Run a read query directly against the test database: query(sql, params) -> list of rows."""
    def run(sql, params=()):
        conn = sqlite3.connect(app.config["DATABASE"])
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return rows
    return run


@pytest.fixture
def captured(app):
    """Record (template_name, context) for every template rendered during a test."""
    recorded = []

    def record(sender, template, context, **extra):
        recorded.append((template.name, context))

    template_rendered.connect(record, app)
    yield recorded
    template_rendered.disconnect(record, app)


def register(client, username, password="pw123"):
    return client.post(
        "/register",
        data={"username": username, "password": password, "confirmation": password},
    )


def login(client, username, password="pw123"):
    return client.post("/login", data={"username": username, "password": password})


def make_user_client(app, username):
    client = app.test_client()
    register(client, username)
    login(client, username)
    return client


@pytest.fixture
def client(app):
    """A client logged in as 'alice'."""
    return make_user_client(app, "alice")


@pytest.fixture
def other_client(app):
    """A second, separate client logged in as 'bob'."""
    return make_user_client(app, "bob")


@pytest.fixture
def anon(app):
    """A client that is not logged in."""
    return app.test_client()


def add_vehicle(client, **overrides):
    """Create a vehicle through the UI route and return its id."""
    data = {"unit_number": "42", "make": "Freightliner", "model": "Cascadia", "year": "2019",
            "vin": "", "mileage": "350000", "gvwr": "80000"}
    data.update(overrides)
    resp = client.post("/vehicles", data=data)
    assert resp.status_code == 302
    return int(resp.headers["Location"].rsplit("/", 1)[-1])


def add_work_order(client, query, vehicle_id, **overrides):
    """Create a work order through the UI route and return its id."""
    data = {"vehicle_id": str(vehicle_id), "status": "open", "created_by": "Alice",
            "assigned_to": "Mike", "description": "Replace brake chamber", "notes": "",
            "hours": "1.5", "scheduled_completion_date": ""}
    data.update(overrides)
    resp = client.post("/work-orders", data=data, content_type="multipart/form-data")
    assert resp.status_code == 302
    return query("SELECT MAX(id) AS id FROM work_orders")[0]["id"]


def add_inspection_report(client, query, vehicle_id, **overrides):
    data = {"vehicle_id": str(vehicle_id), "inspector_name": "Alice",
            "inspection_date": "2026-03-15", "mileage": "351000", "notes": ""}
    data.update(overrides)
    resp = client.post("/inspections/reports", data=data)
    assert resp.status_code == 302
    return query("SELECT MAX(id) AS id FROM inspection_reports")[0]["id"]


def add_inventory_item(client, query, **overrides):
    data = {"part_number": "K-123", "name": "Brake chamber", "cost": "54.99", "vendor": "FleetPride",
            "location": "Bay 2", "quantity": "5", "low_stock_threshold": "2"}
    data.update(overrides)
    resp = client.post("/inventory", data=data)
    assert resp.status_code == 302
    return query("SELECT MAX(id) AS id FROM inventory")[0]["id"]


def flashes(client):
    """Return and clear the flash messages queued in the client's session
    (the same thing rendering the next page would do)."""
    with client.session_transaction() as sess:
        return [msg for _category, msg in sess.pop("_flashes", [])]
