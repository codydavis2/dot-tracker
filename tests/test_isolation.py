"""Tenant isolation (CLAUDE.md rule 1).

A user of company B must get a 404 on every company-A resource, on every route
(view, edit, close, delete, download, report), and company A's data must be
unchanged afterward. Ids submitted in a form (not the URL) are rejected with a
validation message instead.
"""
import io

import pytest
from psycopg import sql

from conftest import (add_inspection_report, add_inventory_item, add_reminder, add_vehicle, add_work_order,
                      flashes)

# Every table that holds customer data (everything except dtc_reference)
CUSTOMER_TABLES = [
    "companies", "users", "vehicles", "dot_details", "maintenance_logs", "maintenance_parts", "dtc_codes",
    "work_orders", "work_order_parts", "work_order_attachments", "inventory", "inspection_reminders",
    "inspection_reports", "inspection_report_items",
]


@pytest.fixture
def a(client, query, users):
    """Company A (the `client` fixture's company) owns one of everything."""
    vehicle_id = add_vehicle(client, unit_number="A1", make="AliceMake", is_dot_regulated="on")
    wo_id = add_work_order(client, query, vehicle_id, description="Company A work order",
                           attachments=(io.BytesIO(b"company A secret"), "invoice.pdf"))
    closed_wo_id = add_work_order(client, query, vehicle_id, status="closed", description="Company A closed")
    attachment_id = query("SELECT id FROM work_order_attachments")[0]["id"]
    report_id = add_inspection_report(client, query, vehicle_id, inspection_date="2026-03-15")
    reminder_id = add_reminder(client, query, vehicle_id, due_date="2000-01-01")
    item_id = add_inventory_item(client, query, name="Company A part")
    client.post(f"/vehicles/{vehicle_id}/maintenance",
                data={"service_type": "Oil change", "performed_by_user_id": str(users["a_owner"])})
    query("UPDATE work_orders SET closed_at = '2026-03-10 12:00:00+00' WHERE id = %s", (closed_wo_id,))
    return {
        "vehicle_id": vehicle_id, "wo_id": wo_id, "closed_wo_id": closed_wo_id, "attachment_id": attachment_id,
        "report_id": report_id, "reminder_id": reminder_id, "item_id": item_id,
    }


def snapshot(query):
    return {t: query(sql.SQL("SELECT * FROM {} ORDER BY id").format(sql.Identifier(t))) for t in CUSTOMER_TABLES}


def a_resource_routes(a, users):
    """(method, url, form data) for every route that takes a company-A id in the URL."""
    v, wo, att = a["vehicle_id"], a["wo_id"], a["attachment_id"]
    b_owner = str(users["b_owner"])
    return [
        ("GET", f"/vehicles/{v}", None),
        ("POST", f"/vehicles/{v}/maintenance", {"service_type": "Oil change", "performed_by_user_id": b_owner}),
        ("POST", f"/vehicles/{v}/dtc", {"code": "P0420"}),
        ("POST", f"/vehicles/{v}/reminders", {"reminder_type": "other", "due_date": "2027-01-01"}),
        ("GET", f"/work-orders/{wo}", None),
        ("POST", f"/work-orders/{wo}/edit", {"vehicle_id": str(v), "created_by_user_id": b_owner,
                                             "description": "hijacked"}),
        ("POST", f"/work-orders/{wo}/close", {"performed_by_user_id": b_owner}),
        ("POST", f"/work-orders/{wo}/delete", {}),
        ("GET", f"/work-orders/{wo}/attachments/{att}", None),
        ("POST", f"/work-orders/{wo}/attachments/{att}/delete", {}),
        ("GET", f"/inspections/reports/{a['report_id']}", None),
        ("POST", f"/reminders/{a['reminder_id']}/complete", {}),
        ("POST", f"/inventory/{a['item_id']}/edit", {"name": "hijacked"}),
    ]


ROUTE_COUNT = 13


@pytest.mark.parametrize("index", range(ROUTE_COUNT))
def test_other_company_gets_404_and_changes_nothing(other_client, a, users, query, index):
    routes = a_resource_routes(a, users)
    assert len(routes) == ROUTE_COUNT
    method, url, data = routes[index]
    before = snapshot(query)

    if method == "GET":
        resp = other_client.get(url)
    else:
        resp = other_client.post(url, data=data, content_type="multipart/form-data")

    assert resp.status_code == 404, f"{method} {url}"
    assert b"company A secret" not in resp.data
    assert snapshot(query) == before


def test_same_urls_work_for_the_owning_company(client, a):
    """Guards against the 404s above passing only because the routes are broken."""
    for url in [f"/vehicles/{a['vehicle_id']}", f"/work-orders/{a['wo_id']}",
                f"/work-orders/{a['wo_id']}/attachments/{a['attachment_id']}",
                f"/inspections/reports/{a['report_id']}"]:
        resp = client.get(url)
        assert resp.status_code == 200, url
        resp.close()


def test_cannot_create_records_on_other_companys_vehicle(other_client, a, users, query):
    before = snapshot(query)
    b_owner = str(users["b_owner"])
    resp = other_client.post("/work-orders", content_type="multipart/form-data",
                             data={"vehicle_id": str(a["vehicle_id"]), "created_by_user_id": b_owner})
    assert resp.headers["Location"] == "/work-orders"
    assert flashes(other_client) == ["Select a valid unit number."]

    resp = other_client.post("/inspections/reports",
                             data={"vehicle_id": str(a["vehicle_id"]), "performed_by_user_id": b_owner})
    assert resp.headers["Location"] == "/inspections"
    assert flashes(other_client) == ["Select a valid unit number."]
    assert snapshot(query) == before


def test_cannot_move_own_work_order_to_other_companys_vehicle(other_client, a, users, query):
    b_vehicle = add_vehicle(other_client, unit_number="B1")
    b_wo = add_work_order(other_client, query, b_vehicle)
    resp = other_client.post(f"/work-orders/{b_wo}/edit", content_type="multipart/form-data",
                             data={"vehicle_id": str(a["vehicle_id"]), "created_by_user_id": str(users["b_owner"])})
    assert resp.headers["Location"] == f"/work-orders/{b_wo}"
    assert flashes(other_client) == ["Select a valid unit number."]
    assert query("SELECT vehicle_id FROM work_orders WHERE id = %s", (b_wo,))[0]["vehicle_id"] == b_vehicle


def test_lists_and_dashboard_show_only_own_data(other_client, a, captured):
    resp = other_client.get("/")
    _, ctx = captured[-1]
    assert ctx["vehicles"] == []
    assert ctx["dot_warnings"] == []
    assert ctx["open_work_orders"] == []
    assert ctx["events_by_day"] == {}

    other_client.get("/vehicles")
    assert captured[-1][1]["vehicles"] == []

    resp = other_client.get("/work-orders")
    _, ctx = captured[-1]
    assert ctx["vehicles"] == [] and ctx["open_work_orders"] == [] and ctx["closed_work_orders"] == []
    assert [p["full_name"] for p in ctx["people"]] == ["Mechanic B", "Owner B"]

    other_client.get("/inspections")
    _, ctx = captured[-1]
    assert ctx["vehicles"] == [] and ctx["reports"] == [] and ctx["upcoming"] == []

    resp = other_client.get("/inventory")
    assert captured[-1][1]["items"] == []
    assert b"Company A part" not in resp.data


def test_audit_report_shows_only_own_data(other_client, a, captured):
    # Even naming company A's vehicle explicitly returns nothing of company A's
    resp = other_client.get(f"/audit?start_date=2000-01-01&end_date=2100-12-31&vehicle_id={a['vehicle_id']}")
    assert resp.status_code == 200
    _, ctx = captured[-1]
    assert ctx["selected_vehicle"] is None
    assert ctx["work_orders"] == []
    assert ctx["inspection_reports"] == []
    assert b"Company A" not in resp.data
