"""A second user must not be able to see or change the first user's data.

These record today's behavior: most routes redirect with a "not found" flash,
the attachment download returns 404. Either way, nothing leaks or changes.
"""
import io

import pytest

from conftest import add_inspection_report, add_inventory_item, add_vehicle, add_work_order, flashes


@pytest.fixture
def alice_data(app, client, query):
    """Alice (the `client` fixture) owns one of everything."""
    vehicle_id = add_vehicle(client, unit_number="A1", make="AliceMake", is_dot_regulated="on")
    wo_id = add_work_order(client, query, vehicle_id, description="Alice work order",
                           attachments=(io.BytesIO(b"alice secret"), "invoice.pdf"))
    attachment_id = query("SELECT id FROM work_order_attachments")[0]["id"]
    report_id = add_inspection_report(client, query, vehicle_id, inspector_name="Alice inspector",
                                      inspection_date="2026-03-15")
    client.post(f"/vehicles/{vehicle_id}/reminders", data={
        "reminder_type": "dot_annual", "due_date": "2000-01-01", "interval": "none",
    })
    reminder_id = query("SELECT id FROM inspection_reminders")[0]["id"]
    item_id = add_inventory_item(client, query, name="Alice part")
    return {
        "vehicle_id": vehicle_id, "wo_id": wo_id, "attachment_id": attachment_id,
        "report_id": report_id, "reminder_id": reminder_id, "item_id": item_id,
    }


def snapshot(query):
    """Everything Alice could lose, so we can confirm Bob changed nothing."""
    tables = ["vehicles", "work_orders", "work_order_parts", "work_order_attachments",
              "inspection_reports", "inspection_reminders", "inventory", "maintenance_logs", "dtc_codes"]
    return {t: [tuple(r) for r in query(f"SELECT * FROM {t} ORDER BY id")] for t in tables}


def assert_redirect(resp, location, client, message):
    assert resp.status_code == 302
    assert resp.headers["Location"] == location
    assert flashes(client) == [message]


def test_cannot_view_vehicle(other_client, alice_data):
    resp = other_client.get(f"/vehicles/{alice_data['vehicle_id']}")
    assert_redirect(resp, "/vehicles", other_client, "Vehicle not found.")


def test_cannot_add_maintenance_dtc_or_reminder(other_client, alice_data, query):
    before = snapshot(query)
    v = alice_data["vehicle_id"]

    resp = other_client.post(f"/vehicles/{v}/maintenance", data={"service_type": "Oil change"})
    assert_redirect(resp, "/vehicles", other_client, "Vehicle not found.")
    resp = other_client.post(f"/vehicles/{v}/dtc", data={"code": "P0420"})
    assert_redirect(resp, "/vehicles", other_client, "Vehicle not found.")
    resp = other_client.post(f"/vehicles/{v}/reminders", data={"reminder_type": "other", "due_date": "2027-01-01"})
    assert_redirect(resp, "/vehicles", other_client, "Vehicle not found.")

    assert snapshot(query) == before


def test_cannot_view_work_order(other_client, alice_data):
    resp = other_client.get(f"/work-orders/{alice_data['wo_id']}")
    assert_redirect(resp, "/work-orders", other_client, "Work order not found.")


def test_cannot_edit_close_or_delete_work_order(other_client, alice_data, query):
    before = snapshot(query)
    wo = alice_data["wo_id"]

    resp = other_client.post(f"/work-orders/{wo}/edit", data={"vehicle_id": str(alice_data["vehicle_id"]),
                                                               "description": "hijacked"},
                             content_type="multipart/form-data")
    assert_redirect(resp, "/work-orders", other_client, "Work order not found.")
    resp = other_client.post(f"/work-orders/{wo}/close")
    assert_redirect(resp, "/work-orders", other_client, "Work order not found.")
    resp = other_client.post(f"/work-orders/{wo}/delete")
    assert_redirect(resp, "/work-orders", other_client, "Work order not found.")

    assert snapshot(query) == before


def test_cannot_create_work_order_on_others_vehicle(other_client, alice_data, query):
    before = snapshot(query)
    resp = other_client.post("/work-orders", data={"vehicle_id": str(alice_data["vehicle_id"])},
                             content_type="multipart/form-data")
    assert_redirect(resp, "/work-orders", other_client, "Select a valid unit number.")
    assert snapshot(query) == before


def test_cannot_move_own_work_order_to_others_vehicle(other_client, alice_data, query):
    bob_vehicle = add_vehicle(other_client, unit_number="B1")
    bob_wo = add_work_order(other_client, query, bob_vehicle)
    resp = other_client.post(f"/work-orders/{bob_wo}/edit", data={"vehicle_id": str(alice_data["vehicle_id"])},
                             content_type="multipart/form-data")
    assert_redirect(resp, "/work-orders", other_client, "Select a valid unit number.")
    assert query("SELECT vehicle_id FROM work_orders WHERE id = ?", (bob_wo,))[0][0] == bob_vehicle


def test_cannot_download_attachment(other_client, alice_data):
    resp = other_client.get(f"/work-orders/{alice_data['wo_id']}/attachments/{alice_data['attachment_id']}")
    assert resp.status_code == 404
    assert b"alice secret" not in resp.data


def test_cannot_delete_attachment(app, other_client, alice_data, query):
    before = snapshot(query)
    resp = other_client.post(f"/work-orders/{alice_data['wo_id']}/attachments/{alice_data['attachment_id']}/delete")
    assert_redirect(resp, "/work-orders", other_client, "Attachment not found.")
    assert snapshot(query) == before


def test_cannot_view_inspection_report(other_client, alice_data):
    resp = other_client.get(f"/inspections/reports/{alice_data['report_id']}")
    assert_redirect(resp, "/inspections", other_client, "Inspection report not found.")


def test_cannot_submit_report_for_others_vehicle(other_client, alice_data, query):
    before = snapshot(query)
    resp = other_client.post("/inspections/reports", data={"vehicle_id": str(alice_data["vehicle_id"])})
    assert_redirect(resp, "/inspections", other_client, "Select a valid unit number.")
    assert snapshot(query) == before


def test_cannot_complete_reminder(other_client, alice_data, query):
    before = snapshot(query)
    resp = other_client.post(f"/reminders/{alice_data['reminder_id']}/complete")
    assert_redirect(resp, "/", other_client, "Reminder not found.")
    assert snapshot(query) == before


def test_cannot_edit_inventory(other_client, alice_data, query):
    before = snapshot(query)
    resp = other_client.post(f"/inventory/{alice_data['item_id']}/edit", data={"name": "hijacked"})
    assert_redirect(resp, "/inventory", other_client, "Inventory item not found.")
    assert snapshot(query) == before


def test_lists_and_dashboard_show_only_own_data(other_client, alice_data, captured):
    other_client.get("/")
    _, ctx = captured[-1]
    assert ctx["vehicles"] == []
    assert ctx["dot_warnings"] == []
    assert ctx["open_work_orders"] == []

    other_client.get("/vehicles")
    assert captured[-1][1]["vehicles"] == []

    other_client.get("/work-orders")
    _, ctx = captured[-1]
    assert ctx["vehicles"] == [] and ctx["open_work_orders"] == [] and ctx["closed_work_orders"] == []

    other_client.get("/inspections")
    _, ctx = captured[-1]
    assert ctx["vehicles"] == [] and ctx["reports"] == [] and ctx["upcoming"] == []

    resp = other_client.get("/inventory")
    assert captured[-1][1]["items"] == []
    assert b"Alice part" not in resp.data


def test_audit_report_shows_only_own_data(app, other_client, alice_data, captured):
    import sqlite3
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.execute("UPDATE work_orders SET status = 'closed', closed_at = '2026-03-10 12:00:00'")
    conn.commit()
    conn.close()

    # Even naming Alice's vehicle explicitly returns nothing of hers
    resp = other_client.get(f"/audit?start_date=2026-01-01&end_date=2026-12-31&vehicle_id={alice_data['vehicle_id']}")
    assert resp.status_code == 200
    _, ctx = captured[-1]
    assert ctx["selected_vehicle"] is None
    assert ctx["work_orders"] == []
    assert ctx["inspection_reports"] == []
    assert b"Alice work order" not in resp.data
    assert b"Alice inspector" not in resp.data
