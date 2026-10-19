"""Role-specific behavior on top of the permission matrix (test_permissions.py):
what drivers get, what mechanics can't do, and the manager guardrails on users."""
import io

import pytest
from psycopg import sql

from app.constants import PRE_TRIP_CHECKLIST
from conftest import (add_inspection_report, add_reminder, add_vehicle, add_work_order, current_user_id, email_link,
                      flashes, link_path, make_user)

# ---------- drivers ----------


@pytest.fixture
def shop_data(client, query, users):
    """A DOT unit with everything a driver must NOT see: DOT details, a work order,
    a maintenance log, a DTC and a reminder. Plus one inspection they may see."""
    vehicle_id = add_vehicle(client, unit_number="77", is_dot_regulated="on")
    query("UPDATE dot_details SET dot_number = 'USDOT-SECRET-123' WHERE vehicle_id = %s", (vehicle_id,))
    add_work_order(client, query, vehicle_id, description="WO-SECRET brake job")
    client.post(f"/vehicles/{vehicle_id}/maintenance",
                data={"service_type": "MAINT-SECRET oil change", "performed_by_user_id": str(users["a_owner"])})
    client.post(f"/vehicles/{vehicle_id}/dtc", data={"code": "DTC-SECRET", "description": "DTC-SECRET desc"})
    add_reminder(client, query, vehicle_id, reminder_type="quarterly", assigned_to_user_id=str(users["a_mechanic"]))
    report_id = add_inspection_report(client, query, vehicle_id, inspection_type="annual")
    assert query("SELECT count(*) AS n FROM maintenance_logs")[0]["n"] == 1
    assert query("SELECT count(*) AS n FROM dtc_codes")[0]["n"] == 1
    return {"vehicle_id": vehicle_id, "report_id": report_id}


def test_driver_vehicle_detail_has_basic_info_and_inspections_only(driver_client, shop_data, captured):
    resp = driver_client.get(f"/vehicles/{shop_data['vehicle_id']}")
    assert resp.status_code == 200
    name, ctx = captured[-1]
    assert name == "vehicle_detail.html"
    # Nothing beyond the vehicle and its inspections is even passed to the template
    assert {"dot_details", "reminders", "work_orders", "maintenance_logs", "dtc_codes", "people"} & set(ctx) == set()
    assert [r["id"] for r in ctx["inspection_reports"]] == [shop_data["report_id"]]
    assert ctx["vehicle"]["unit_number"] == "77"

    html = resp.get_data(as_text=True)
    assert "Unit 77" in html and f'/inspections/reports/{shop_data["report_id"]}' in html
    for secret in ("USDOT-SECRET", "DOT Details", "WO-SECRET", "W/O", "/work-orders", "MAINT-SECRET", "DTC-SECRET",
                   "Quarterly", "Mechanic A", "/reminders", "reminder-form"):
        assert secret not in html, secret


def test_shop_roles_still_get_full_vehicle_detail(mechanic_client, shop_data, captured):
    html = mechanic_client.get(f"/vehicles/{shop_data['vehicle_id']}").get_data(as_text=True)
    _, ctx = captured[-1]
    assert ctx["shop_view"] is True and len(ctx["work_orders"]) == 1 and len(ctx["reminders"]) == 1
    assert "USDOT-SECRET" in html and "WO-SECRET" in html


def test_driver_inspections_page(driver_client, shop_data, captured):
    resp = driver_client.get("/inspections")
    _, ctx = captured[-1]
    assert ctx["inspection_types"] == ["pre_trip"]
    assert ctx["checklist_groups"] == [("pre_trip", ["pre_trip"], PRE_TRIP_CHECKLIST)]
    assert ctx["show_reminders"] is False and ctx["upcoming"] == []  # reminders not queried
    html = resp.get_data(as_text=True)
    assert '<option value="pre_trip" selected>Pre-trip</option>' in html
    assert 'value="general"' not in html and "Mark Done" not in html and "Quarterly" not in html


def test_driver_can_submit_pre_trip(driver_client, roles, shop_data, query):
    resp = driver_client.post("/inspections/reports", data={
        "vehicle_id": str(shop_data["vehicle_id"]), "inspection_type": "pre_trip",
        "performed_by_user_id": str(roles["a_driver"]), "status_0": "out_of_spec", "comments_0": "Soft pedal"})
    report_id = query("SELECT max(id) AS id FROM inspection_reports")[0]["id"]
    assert resp.headers["Location"] == f"/inspections/reports/{report_id}?new=1"
    report = query("SELECT * FROM inspection_reports WHERE id = %s", (report_id,))[0]
    assert (report["inspection_type"], report["performed_by_user_id"]) == ("pre_trip", roles["a_driver"])
    # Drivers see their report but never the work order prompt
    html = driver_client.get(f"/inspections/reports/{report_id}?new=1").get_data(as_text=True)
    assert "Soft pedal" in html and "Create a work order" not in html


@pytest.mark.parametrize("inspection_type", ["monthly", "annual", "general"])
def test_driver_gets_403_for_other_types(driver_client, roles, shop_data, query, inspection_type):
    before = query("SELECT count(*) AS n FROM inspection_reports")[0]["n"]
    resp = driver_client.post("/inspections/reports", data={
        "vehicle_id": str(shop_data["vehicle_id"]), "inspection_type": inspection_type,
        "performed_by_user_id": str(roles["a_driver"])})
    assert resp.status_code == 403
    assert query("SELECT count(*) AS n FROM inspection_reports")[0]["n"] == before


def test_driver_tampered_checklist_items_ignored(driver_client, roles, shop_data, query):
    driver_client.post("/inspections/reports", data={
        "vehicle_id": str(shop_data["vehicle_id"]), "inspection_type": "pre_trip",
        "performed_by_user_id": str(roles["a_driver"]),
        "item_0": "Everything is fine", "checklist": "annual",
        "status_20": "out_of_spec", "comments_20": "Injected 21st item", "status_3": "needs_attention"})
    report_id = query("SELECT max(id) AS id FROM inspection_reports")[0]["id"]
    items = query("SELECT item, status, comments FROM inspection_report_items WHERE inspection_report_id = %s "
                  "ORDER BY id", (report_id,))
    assert [i["item"] for i in items] == PRE_TRIP_CHECKLIST
    assert [i["status"] for i in items].count("needs_attention") == 1
    assert "Injected 21st item" not in [i["comments"] for i in items]


def test_driver_lands_on_inspections_after_invite(app, client, roles, outbox):
    client.post("/users/invites", data={"email": "newdriver@example.com", "role": "driver"})
    path = link_path(email_link(outbox[-1]))
    resp = app.test_client().post(path, data={"full_name": "New Driver", "password": "long-haul-road",
                                              "confirmation": "long-haul-road"})
    assert resp.headers["Location"] == "/inspections"


# ---------- mechanics ----------

@pytest.fixture
def a_work_order(client, query, users):
    vehicle_id = add_vehicle(client)
    wo_id = add_work_order(client, query, vehicle_id, attachments=(io.BytesIO(b"invoice"), "invoice.pdf"))
    attachment_id = query("SELECT id FROM work_order_attachments")[0]["id"]
    return {"vehicle_id": vehicle_id, "wo_id": wo_id, "attachment_id": attachment_id}


def snapshot(query, tables=("vehicles", "work_orders", "work_order_attachments", "users", "invites")):
    return {t: query(sql.SQL("SELECT * FROM {} ORDER BY id").format(sql.Identifier(t))) for t in tables}


def test_mechanic_403s(mechanic_client, a_work_order, query, users):
    wo, att = a_work_order["wo_id"], a_work_order["attachment_id"]
    before = snapshot(query)
    for method, path, data in [
        ("POST", f"/work-orders/{wo}/delete", {}),
        ("POST", f"/work-orders/{wo}/attachments/{att}/delete", {}),
        ("POST", "/vehicles", {"make": "Mack", "model": "Anthem", "year": "2022"}),
        ("GET", "/users", {}),
        ("POST", "/users/invites", {"email": "x@example.com", "role": "driver"}),
        ("POST", f"/users/{users['a_owner']}/role", {"role": "driver"}),
    ]:
        assert mechanic_client.open(path, method=method, data=data).status_code == 403, path
    assert snapshot(query) == before


def test_mechanic_ui_hides_what_they_cannot_do(mechanic_client, a_work_order):
    html = mechanic_client.get(f"/work-orders/{a_work_order['wo_id']}").get_data(as_text=True)
    assert "/delete" not in html
    assert f"/attachments/{a_work_order['attachment_id']}" in html  # download link is still there
    assert "+ Add Vehicle" not in mechanic_client.get("/vehicles").get_data(as_text=True)


def test_mechanic_can_still_do_shop_work(mechanic_client, a_work_order, query):
    wo = a_work_order["wo_id"]
    assert mechanic_client.get(f"/work-orders/{wo}").status_code == 200
    resp = mechanic_client.get(f"/work-orders/{wo}/attachments/{a_work_order['attachment_id']}")
    assert resp.status_code == 200 and resp.data == b"invoice"
    resp.close()
    mechanic_client.post(f"/work-orders/{wo}/close", data={"performed_by_user_id": str(current_user_id(mechanic_client))})
    assert query("SELECT status FROM work_orders WHERE id = %s", (wo,))[0]["status"] == "closed"


# ---------- managers ----------

def test_manager_can_add_vehicles_and_delete_work_orders(manager_client, a_work_order, query):
    add_vehicle(manager_client, unit_number="M1")
    manager_client.post(f"/work-orders/{a_work_order['wo_id']}/attachments/{a_work_order['attachment_id']}/delete")
    assert query("SELECT * FROM work_order_attachments") == []
    manager_client.post(f"/work-orders/{a_work_order['wo_id']}/delete")
    assert query("SELECT deleted_at FROM work_orders")[0]["deleted_at"] is not None


@pytest.mark.parametrize("role", ["manager", "mechanic", "driver"])
def test_manager_can_invite(manager_client, roles, query, outbox, role):
    manager_client.post("/users/invites", data={"email": f"new-{role}@example.com", "role": role})
    assert flashes(manager_client) == [f"Invite sent to new-{role}@example.com."]
    invite = query("SELECT * FROM invites")[0]
    assert (invite["role"], invite["invited_by_user_id"]) == (role, roles["a_manager"])
    assert "Manager A invited you" in outbox[-1]["body_text"]

    manager_client.post(f"/users/invites/{invite['id']}/resend")
    assert len(outbox) == 2
    manager_client.post(f"/users/invites/{invite['id']}/revoke")
    assert query("SELECT revoked_at FROM invites")[0]["revoked_at"] is not None


def test_manager_cannot_invite_an_owner(manager_client, roles, query, outbox):
    manager_client.post("/users/invites", data={"email": "boss@example.com", "role": "owner"})
    assert flashes(manager_client) == ["Choose a role: manager, mechanic, or driver."]
    assert query("SELECT * FROM invites") == [] and outbox == []


@pytest.fixture
def other_manager(query, roles):
    return make_user(query, roles["a_company"], "manager2@a.example.com", "Manager Two", "manager")


def test_manager_can_manage_non_owners(manager_client, roles, query, other_manager):
    for target in (roles["a_mechanic"], roles["a_driver"], other_manager):
        manager_client.post(f"/users/{target}/name", data={"full_name": "Renamed"})
        manager_client.post(f"/users/{target}/role", data={"role": "mechanic"})
        manager_client.post(f"/users/{target}/deactivate")
        row = query("SELECT full_name, role, is_active FROM users WHERE id = %s", (target,))[0]
        assert row == {"full_name": "Renamed", "role": "mechanic", "is_active": False}
        manager_client.post(f"/users/{target}/reactivate")
        assert query("SELECT is_active FROM users WHERE id = %s", (target,))[0]["is_active"] is True


def test_manager_cannot_touch_the_owner(manager_client, roles, query):
    owner = roles["a_owner"]
    before = snapshot(query, ("users",))
    for path, data in [(f"/users/{owner}/role", {"role": "driver"}), (f"/users/{owner}/name", {"full_name": "X"}),
                       (f"/users/{owner}/deactivate", {}), (f"/users/{owner}/reactivate", {})]:
        assert manager_client.post(path, data=data).status_code == 404, path
    assert snapshot(query, ("users",)) == before


def test_manager_cannot_change_own_role_or_deactivate_self(manager_client, roles, query):
    me = roles["a_manager"]
    manager_client.post(f"/users/{me}/role", data={"role": "mechanic"})
    assert flashes(manager_client) == [
        "You can't change your own role. (The owner can hand over the company with Transfer ownership.)"]
    manager_client.post(f"/users/{me}/deactivate")
    assert flashes(manager_client) == ["You can't deactivate your own account."]
    row = query("SELECT role, is_active, auth_version FROM users WHERE id = %s", (me,))[0]
    assert row == {"role": "manager", "is_active": True, "auth_version": 1}
    assert manager_client.get("/users").status_code == 200


@pytest.mark.parametrize("role", ["owner", "admin", ""])
def test_manager_cannot_make_anyone_owner(manager_client, roles, query, role):
    manager_client.post(f"/users/{roles['a_mechanic']}/role", data={"role": role})
    assert flashes(manager_client) == ["Choose a role: manager, mechanic, or driver."]
    assert query("SELECT role FROM users WHERE id = %s", (roles["a_mechanic"],))[0]["role"] == "mechanic"
    assert query("SELECT count(*) AS n FROM users WHERE role = 'owner' AND company_id = %s",
                 (roles["a_company"],))[0]["n"] == 1


def test_manager_users_page_has_no_controls_for_owner_or_self(manager_client, roles):
    html = manager_client.get("/users").get_data(as_text=True)
    for user_id in (roles["a_owner"], roles["a_manager"]):
        assert f"/users/{user_id}/role" not in html
        assert f"/users/{user_id}/deactivate" not in html
    assert f"/users/{roles['a_owner']}/name" not in html
    assert f"/users/{roles['a_mechanic']}/role" in html and f"/users/{roles['a_mechanic']}/deactivate" in html
    assert "Owner A" in html and "Manager A" in html
    assert "/transfer-ownership" not in html


def test_manager_on_other_company_users_is_404(manager_client, roles, query):
    before = snapshot(query, ("users",))
    for path in (f"/users/{roles['b_mechanic']}/role", f"/users/{roles['b_mechanic']}/deactivate"):
        assert manager_client.post(path, data={"role": "driver"}).status_code == 404
    assert snapshot(query, ("users",)) == before
