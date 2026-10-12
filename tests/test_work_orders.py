from decimal import Decimal

from conftest import add_vehicle, add_work_order, flashes, select_options


def test_create_work_order_with_parts(client, query, users):
    vehicle_id = add_vehicle(client)
    resp = client.post("/work-orders", content_type="multipart/form-data", data={
        "vehicle_id": str(vehicle_id),
        "status": "open",
        "created_by_user_id": str(users["a_owner"]),
        "assigned_to_user_id": str(users["a_mechanic"]),
        "performed_by_user_id": str(users["a_owner"]),  # ignored while open
        "description": "Replace brake chamber",
        "notes": "Right rear",
        "hours": "1.5",
        "scheduled_completion_date": "2027-02-01",
        "part_name[]": ["Brake chamber", "", "Clevis pin"],
        "part_quantity[]": ["1", "1", "2"],
    })
    assert resp.headers["Location"] == "/work-orders"

    wo = query("SELECT * FROM work_orders")[0]
    assert wo["company_id"] == users["a_company"]
    assert wo["vehicle_id"] == vehicle_id
    assert wo["status"] == "open"
    assert wo["created_by_user_id"] == users["a_owner"]
    assert wo["assigned_to_user_id"] == users["a_mechanic"]
    assert wo["performed_by_user_id"] is None
    assert wo["hours"] == Decimal("1.50")
    assert str(wo["scheduled_completion_date"]) == "2027-02-01"
    assert wo["closed_at"] is None
    assert wo["deleted_at"] is None

    parts = query("SELECT part_name, quantity, company_id FROM work_order_parts ORDER BY id")
    assert [(p["part_name"], p["quantity"]) for p in parts] == [("Brake chamber", 1), ("Clevis pin", 2)]
    assert {p["company_id"] for p in parts} == {users["a_company"]}


def test_created_by_defaults_to_logged_in_user(mechanic_client, users):
    resp = mechanic_client.get("/work-orders")
    values, selected = select_options(resp.get_data(as_text=True), "created_by_user_id")
    assert selected == str(users["a_mechanic"])
    assert set(values) == {str(users["a_owner"]), str(users["a_mechanic"])}
    # Completed by is also pre-selected to the logged-in user; Assigned to starts blank
    assert select_options(resp.get_data(as_text=True), "performed_by_user_id")[1] == str(users["a_mechanic"])
    assert select_options(resp.get_data(as_text=True), "assigned_to_user_id")[1] == ""


def test_created_by_can_be_another_active_user(client, query, users):
    vehicle_id = add_vehicle(client)
    add_work_order(client, query, vehicle_id, created_by_user_id=str(users["a_mechanic"]))
    assert query("SELECT created_by_user_id FROM work_orders")[0]["created_by_user_id"] == users["a_mechanic"]


def test_created_by_is_required(client, query):
    vehicle_id = add_vehicle(client)
    resp = client.post("/work-orders", content_type="multipart/form-data",
                       data={"vehicle_id": str(vehicle_id), "created_by_user_id": ""})
    assert resp.headers["Location"] == "/work-orders"
    assert flashes(client) == ["Created by is required."]
    assert query("SELECT * FROM work_orders") == []


def test_created_by_can_be_changed_on_edit(client, query, users):
    vehicle_id = add_vehicle(client)
    wo_id = add_work_order(client, query, vehicle_id)
    client.post(f"/work-orders/{wo_id}/edit", content_type="multipart/form-data", data={
        "vehicle_id": str(vehicle_id), "created_by_user_id": str(users["a_mechanic"]),
    })
    assert query("SELECT created_by_user_id FROM work_orders")[0]["created_by_user_id"] == users["a_mechanic"]


def test_create_work_order_as_closed_requires_completed_by(client, query, users):
    vehicle_id = add_vehicle(client)
    resp = client.post("/work-orders", content_type="multipart/form-data", data={
        "vehicle_id": str(vehicle_id), "status": "closed",
        "created_by_user_id": str(users["a_owner"]), "performed_by_user_id": "",
    })
    assert resp.headers["Location"] == "/work-orders"
    assert flashes(client) == ["Completed by is required."]
    assert query("SELECT * FROM work_orders") == []


def test_create_work_order_already_closed(client, query, users):
    vehicle_id = add_vehicle(client)
    add_work_order(client, query, vehicle_id, status="closed", performed_by_user_id=str(users["a_mechanic"]))
    row = query("SELECT * FROM work_orders")[0]
    assert row["status"] == "closed"
    assert row["closed_at"] is not None
    assert row["performed_by_user_id"] == users["a_mechanic"]


def test_create_work_order_requires_valid_vehicle(client, query, users):
    resp = client.post("/work-orders", data={"vehicle_id": "999", "created_by_user_id": str(users["a_owner"])},
                       content_type="multipart/form-data")
    assert resp.headers["Location"] == "/work-orders"
    assert flashes(client) == ["Select a valid unit number."]
    assert query("SELECT * FROM work_orders") == []


def test_work_orders_list(client, query, captured):
    vehicle_id = add_vehicle(client)
    open_id = add_work_order(client, query, vehicle_id)
    closed_id = add_work_order(client, query, vehicle_id, status="closed")
    resp = client.get("/work-orders?vehicle_id=5&description=Brakes")
    assert resp.status_code == 200
    name, ctx = captured[-1]
    assert name == "work_orders.html"
    assert [w["id"] for w in ctx["open_work_orders"]] == [open_id]
    assert [w["id"] for w in ctx["closed_work_orders"]] == [closed_id]
    assert ctx["prefill_vehicle_id"] == "5"
    assert ctx["prefill_description"] == "Brakes"


def test_view_work_order(client, query, captured, users):
    vehicle_id = add_vehicle(client)
    wo_id = add_work_order(client, query, vehicle_id, assigned_to_user_id=str(users["a_mechanic"]), **{
        "part_name[]": ["Brake chamber", "Clevis pin"], "part_quantity[]": ["1", "2"],
    })
    resp = client.get(f"/work-orders/{wo_id}")
    assert resp.status_code == 200
    name, ctx = captured[-1]
    assert name == "work_order_detail.html"
    assert ctx["w"]["id"] == wo_id
    assert ctx["w"]["parts_lines"] == "Brake chamber\nClevis pin (x2)"
    assert ctx["w"]["created_by_name"] == "Owner A"
    assert ctx["w"]["assigned_to_name"] == "Mechanic A"
    assert b"Replace brake chamber" in resp.data
    # The Close form's Completed by is pre-selected to the logged-in user
    assert select_options(resp.get_data(as_text=True), "performed_by_user_id")[1] == str(users["a_owner"])


def test_view_missing_work_order_is_404(client):
    assert client.get("/work-orders/999").status_code == 404


def test_edit_work_order(client, query, users):
    vehicle_id = add_vehicle(client, unit_number="1")
    other_vehicle_id = add_vehicle(client, unit_number="2")
    wo_id = add_work_order(client, query, vehicle_id, **{
        "part_name[]": ["Old part"], "part_quantity[]": ["1"],
    })

    resp = client.post(f"/work-orders/{wo_id}/edit", content_type="multipart/form-data", data={
        "vehicle_id": str(other_vehicle_id),
        "status": "open",
        "created_by_user_id": str(users["a_owner"]),
        "assigned_to_user_id": str(users["a_mechanic"]),
        "description": "Updated description",
        "notes": "",
        "hours": "3",
        "scheduled_completion_date": "",
        "part_name[]": ["New part"],
        "part_quantity[]": ["4"],
    })
    assert resp.headers["Location"] == f"/work-orders/{wo_id}"

    wo = query("SELECT * FROM work_orders WHERE id = %s", (wo_id,))[0]
    assert wo["vehicle_id"] == other_vehicle_id
    assert wo["assigned_to_user_id"] == users["a_mechanic"]
    assert wo["description"] == "Updated description"
    assert wo["notes"] is None
    # Parts list is replaced as a whole
    parts = query("SELECT part_name, quantity FROM work_order_parts")
    assert [(p["part_name"], p["quantity"]) for p in parts] == [("New part", 4)]


def test_edit_to_closed_requires_completed_by_and_reopen_clears_it(client, query, users):
    vehicle_id = add_vehicle(client)
    wo_id = add_work_order(client, query, vehicle_id)
    form = {"vehicle_id": str(vehicle_id), "created_by_user_id": str(users["a_owner"]), "description": "x"}

    resp = client.post(f"/work-orders/{wo_id}/edit", content_type="multipart/form-data",
                       data={**form, "status": "closed", "performed_by_user_id": ""})
    assert resp.headers["Location"] == f"/work-orders/{wo_id}"
    assert flashes(client) == ["Completed by is required."]
    assert query("SELECT status FROM work_orders")[0]["status"] == "open"

    client.post(f"/work-orders/{wo_id}/edit", content_type="multipart/form-data",
                data={**form, "status": "closed", "performed_by_user_id": str(users["a_mechanic"])})
    row = query("SELECT status, closed_at, performed_by_user_id FROM work_orders")[0]
    assert row["status"] == "closed" and row["closed_at"] is not None
    assert row["performed_by_user_id"] == users["a_mechanic"]

    client.post(f"/work-orders/{wo_id}/edit", content_type="multipart/form-data",
                data={**form, "status": "open", "performed_by_user_id": str(users["a_mechanic"])})
    row = query("SELECT status, closed_at, performed_by_user_id FROM work_orders")[0]
    assert row["status"] == "open"
    assert row["closed_at"] is None
    assert row["performed_by_user_id"] is None


def test_edit_closed_work_order_keeps_closed_at(client, query, users):
    vehicle_id = add_vehicle(client)
    wo_id = add_work_order(client, query, vehicle_id, status="closed")
    closed_at = query("SELECT closed_at FROM work_orders")[0]["closed_at"]
    client.post(f"/work-orders/{wo_id}/edit", content_type="multipart/form-data", data={
        "vehicle_id": str(vehicle_id), "status": "closed", "created_by_user_id": str(users["a_owner"]),
        "performed_by_user_id": str(users["a_owner"]), "description": "edited later",
    })
    row = query("SELECT closed_at, description FROM work_orders")[0]
    assert row["description"] == "edited later"
    assert row["closed_at"] == closed_at


def test_edit_work_order_rejects_invalid_vehicle(client, query, users):
    vehicle_id = add_vehicle(client)
    wo_id = add_work_order(client, query, vehicle_id)
    resp = client.post(f"/work-orders/{wo_id}/edit", content_type="multipart/form-data",
                       data={"vehicle_id": "999", "created_by_user_id": str(users["a_owner"]), "description": "changed"})
    assert resp.headers["Location"] == f"/work-orders/{wo_id}"
    assert flashes(client) == ["Select a valid unit number."]
    assert query("SELECT description FROM work_orders")[0]["description"] == "Replace brake chamber"


def test_edit_keeps_archived_vehicle(client, query, users):
    vehicle_id = add_vehicle(client)
    wo_id = add_work_order(client, query, vehicle_id)
    query("UPDATE vehicles SET archived_at = now() WHERE id = %s", (vehicle_id,))
    # The edit form still lists the work order's own (now archived) vehicle...
    resp = client.get(f"/work-orders/{wo_id}")
    values, selected = select_options(resp.get_data(as_text=True), "vehicle_id")
    assert selected == str(vehicle_id)
    # ...and saving with it is accepted
    resp = client.post(f"/work-orders/{wo_id}/edit", content_type="multipart/form-data", data={
        "vehicle_id": str(vehicle_id), "created_by_user_id": str(users["a_owner"]), "description": "still here",
    })
    assert flashes(client) == []
    assert query("SELECT description FROM work_orders")[0]["description"] == "still here"


def test_close_work_order(client, query, users):
    vehicle_id = add_vehicle(client)
    wo_id = add_work_order(client, query, vehicle_id)
    resp = client.post(f"/work-orders/{wo_id}/close", data={"performed_by_user_id": str(users["a_mechanic"])})
    assert resp.headers["Location"] == f"/work-orders/{wo_id}"
    row = query("SELECT status, closed_at, performed_by_user_id FROM work_orders")[0]
    assert row["status"] == "closed"
    assert row["closed_at"] is not None
    assert row["performed_by_user_id"] == users["a_mechanic"]


def test_close_without_completed_by_is_rejected(client, query):
    vehicle_id = add_vehicle(client)
    wo_id = add_work_order(client, query, vehicle_id)
    resp = client.post(f"/work-orders/{wo_id}/close", data={})
    assert resp.headers["Location"] == f"/work-orders/{wo_id}"
    assert flashes(client) == ["Completed by is required."]
    row = query("SELECT status, closed_at FROM work_orders")[0]
    assert row["status"] == "open" and row["closed_at"] is None


def test_delete_work_order_is_soft(app, client, query):
    vehicle_id = add_vehicle(client)
    wo_id = add_work_order(client, query, vehicle_id, **{
        "part_name[]": ["Brake chamber"], "part_quantity[]": ["1"],
    })
    resp = client.post(f"/work-orders/{wo_id}/delete")
    assert resp.headers["Location"] == "/work-orders"
    assert flashes(client) == ["Work order deleted."]
    # Kept in the database (with its parts), just marked deleted
    row = query("SELECT deleted_at FROM work_orders WHERE id = %s", (wo_id,))[0]
    assert row["deleted_at"] is not None
    assert len(query("SELECT * FROM work_order_parts")) == 1
    # Deleting again is a 404: it's gone as far as the app is concerned
    assert client.post(f"/work-orders/{wo_id}/delete").status_code == 404


def test_dashboard_lists_open_work_orders(client, query, captured, users):
    vehicle_id = add_vehicle(client)
    overdue_id = add_work_order(client, query, vehicle_id, scheduled_completion_date="2000-01-01",
                                assigned_to_user_id=str(users["a_mechanic"]))
    add_work_order(client, query, vehicle_id, status="closed")
    resp = client.get("/")
    _, ctx = captured[-1]
    assert [(w["id"], w["overdue"]) for w in ctx["open_work_orders"]] == [(overdue_id, True)]
    assert b"Assigned to: Mechanic A" in resp.data


def test_dashboard_calendar_month_navigation(client, query, captured):
    vehicle_id = add_vehicle(client)
    wo_id = add_work_order(client, query, vehicle_id, scheduled_completion_date="2027-03-10")
    client.get("/?year=2027&month=3")
    _, ctx = captured[-1]
    assert ctx["month_label"] == "March 2027"
    assert [e["url"] for e in ctx["events_by_day"][10]] == [f"/work-orders/{wo_id}"]

    # Out-of-range months are normalized rather than erroring
    client.get("/?year=2027&month=13")
    _, ctx = captured[-1]
    assert ctx["month_label"] == "January 2028"
