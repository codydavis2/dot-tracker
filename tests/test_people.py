"""People dropdowns (CLAUDE.md rule 4).

Every people field — Created by, Assigned to, Completed by, Inspector, Performed by —
must only accept an active user from the logged-in user's company, checked on the
server no matter what the form offered.
"""
import pytest
from psycopg import sql

from conftest import add_vehicle, add_work_order, flashes, make_user, select_options

RECORD_TABLES = ["work_orders", "inspection_reports", "maintenance_logs", "inspection_reminders"]


@pytest.fixture
def setup(client, query, users):
    vehicle_id = add_vehicle(client)
    wo_id = add_work_order(client, query, vehicle_id)
    inactive_id = make_user(query, users["a_company"], "former@a.example.com", "Former Tech", is_active=False)
    return {"vehicle_id": vehicle_id, "wo_id": wo_id, "inactive_id": inactive_id}


def people_fields(setup, users):
    """(description, url, valid base form, field under test, label used in the error message)"""
    me = str(users["a_owner"])
    v, wo = str(setup["vehicle_id"]), setup["wo_id"]
    wo_open = {"vehicle_id": v, "status": "open", "created_by_user_id": me}
    wo_closed = {**wo_open, "status": "closed", "performed_by_user_id": me}
    return [
        ("work order create: created by", "/work-orders", wo_open, "created_by_user_id", "Created by"),
        ("work order create: assigned to", "/work-orders", wo_open, "assigned_to_user_id", "Assigned to"),
        ("work order create: completed by", "/work-orders", wo_closed, "performed_by_user_id", "Completed by"),
        ("work order edit: created by", f"/work-orders/{wo}/edit", wo_open, "created_by_user_id", "Created by"),
        ("work order edit: assigned to", f"/work-orders/{wo}/edit", wo_open, "assigned_to_user_id", "Assigned to"),
        ("work order edit: completed by", f"/work-orders/{wo}/edit", wo_closed, "performed_by_user_id", "Completed by"),
        ("work order close: completed by", f"/work-orders/{wo}/close", {}, "performed_by_user_id", "Completed by"),
        ("inspection report: inspector", "/inspections/reports", {"vehicle_id": v, "inspection_type": "general"},
         "performed_by_user_id", "Inspector"),
        ("maintenance log: performed by", f"/vehicles/{v}/maintenance", {"service_type": "Oil change"},
         "performed_by_user_id", "Performed by"),
        ("reminder: assigned to", f"/vehicles/{v}/reminders", {"reminder_type": "other", "due_date": "2027-01-01"},
         "assigned_to_user_id", "Assigned to"),
    ]


FIELD_COUNT = 10


def snapshot(query):
    return {t: query(sql.SQL("SELECT * FROM {} ORDER BY id").format(sql.Identifier(t))) for t in RECORD_TABLES}


@pytest.mark.parametrize("bad_user", ["other_company", "inactive", "not_a_number", "nonexistent"])
@pytest.mark.parametrize("index", range(FIELD_COUNT))
def test_people_field_rejects_invalid_user(client, query, users, setup, index, bad_user):
    fields = people_fields(setup, users)
    assert len(fields) == FIELD_COUNT
    description, url, base_form, field, label = fields[index]
    bad_value = {
        "other_company": str(users["b_mechanic"]),
        "inactive": str(setup["inactive_id"]),
        "not_a_number": "abc",
        "nonexistent": "999999",
    }[bad_user]
    before = snapshot(query)

    resp = client.post(url, data={**base_form, field: bad_value}, content_type="multipart/form-data")

    assert resp.status_code == 302, description
    assert flashes(client) == [f"{label}: select an active user from your company."], description
    assert snapshot(query) == before, description


@pytest.mark.parametrize("index", range(FIELD_COUNT))
def test_people_field_accepts_another_active_user_in_company(client, query, users, setup, index):
    description, url, base_form, field, label = people_fields(setup, users)[index]
    resp = client.post(url, data={**base_form, field: str(users["a_mechanic"])}, content_type="multipart/form-data")
    assert resp.status_code == 302
    assert flashes(client) == [], description


@pytest.mark.parametrize("page, field", [
    ("/work-orders", "created_by_user_id"),
    ("/work-orders", "assigned_to_user_id"),
    ("/work-orders", "performed_by_user_id"),
    ("/inspections", "performed_by_user_id"),
    ("vehicle", "assigned_to_user_id"),
])
def test_dropdowns_list_only_active_users_of_own_company(client, users, setup, page, field):
    if page == "vehicle":
        page = f"/vehicles/{setup['vehicle_id']}"
    values, _ = select_options(client.get(page).get_data(as_text=True), field)
    listed = {v for v in values if v}
    assert listed == {str(users["a_owner"]), str(users["a_mechanic"])}


def test_edit_form_keeps_a_since_deactivated_signer(client, query, users, setup):
    """Deactivating a user must not silently rewrite who signed an existing record."""
    vehicle_id = setup["vehicle_id"]
    wo_id = add_work_order(client, query, vehicle_id, status="closed",
                           created_by_user_id=str(users["a_mechanic"]),
                           performed_by_user_id=str(users["a_mechanic"]))
    query("UPDATE users SET is_active = false WHERE id = %s", (users["a_mechanic"],))

    page = client.get(f"/work-orders/{wo_id}").get_data(as_text=True)
    assert "Mechanic A (inactive)" in page
    # (the first performed_by select on the page is the Close form; the work order is closed, so it isn't shown)
    values, selected = select_options(page, "created_by_user_id")
    assert selected == str(users["a_mechanic"])

    resp = client.post(f"/work-orders/{wo_id}/edit", content_type="multipart/form-data", data={
        "vehicle_id": str(vehicle_id), "status": "closed", "description": "notes added later",
        "created_by_user_id": str(users["a_mechanic"]), "performed_by_user_id": str(users["a_mechanic"]),
    })
    assert resp.headers["Location"] == f"/work-orders/{wo_id}"
    assert flashes(client) == []
    row = query("SELECT * FROM work_orders WHERE id = %s", (wo_id,))[0]
    assert row["description"] == "notes added later"
    assert row["created_by_user_id"] == users["a_mechanic"]
    assert row["performed_by_user_id"] == users["a_mechanic"]

    # ...but the inactive user can't be newly picked for a different field
    client.post(f"/work-orders/{wo_id}/edit", content_type="multipart/form-data", data={
        "vehicle_id": str(vehicle_id), "status": "closed", "created_by_user_id": str(users["a_mechanic"]),
        "performed_by_user_id": str(users["a_mechanic"]), "assigned_to_user_id": str(users["a_mechanic"]),
    })
    assert flashes(client) == ["Assigned to: select an active user from your company."]
