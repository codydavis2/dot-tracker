from conftest import add_vehicle, add_work_order, flashes


def test_create_work_order_with_parts(client, query):
    vehicle_id = add_vehicle(client)
    resp = client.post("/work-orders", content_type="multipart/form-data", data={
        "vehicle_id": str(vehicle_id),
        "status": "open",
        "created_by": "Alice",
        "assigned_to": "Mike",
        "description": "Replace brake chamber",
        "notes": "Right rear",
        "hours": "1.5",
        "scheduled_completion_date": "2027-02-01",
        "part_name[]": ["Brake chamber", "", "Clevis pin"],
        "part_quantity[]": ["1", "1", "2"],
    })
    assert resp.headers["Location"] == "/work-orders"

    wo = query("SELECT * FROM work_orders")[0]
    assert wo["vehicle_id"] == vehicle_id
    assert wo["status"] == "open"
    assert wo["assigned_to"] == "Mike"
    assert wo["hours"] == 1.5
    assert wo["scheduled_completion_date"] == "2027-02-01"
    assert wo["closed_at"] is None

    parts = query("SELECT part_name, quantity FROM work_order_parts ORDER BY id")
    assert [(p["part_name"], p["quantity"]) for p in parts] == [("Brake chamber", 1), ("Clevis pin", 2)]


def test_create_work_order_already_closed_sets_closed_at(client, query):
    vehicle_id = add_vehicle(client)
    add_work_order(client, query, vehicle_id, status="closed")
    assert query("SELECT closed_at FROM work_orders")[0][0] is not None


def test_create_work_order_requires_valid_vehicle(client, query):
    resp = client.post("/work-orders", data={"vehicle_id": "999"}, content_type="multipart/form-data")
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


def test_view_work_order(client, query, captured):
    vehicle_id = add_vehicle(client)
    wo_id = add_work_order(client, query, vehicle_id, **{
        "part_name[]": ["Brake chamber", "Clevis pin"], "part_quantity[]": ["1", "2"],
    })
    resp = client.get(f"/work-orders/{wo_id}")
    assert resp.status_code == 200
    name, ctx = captured[-1]
    assert name == "work_order_detail.html"
    assert ctx["w"]["id"] == wo_id
    assert ctx["w"]["parts_lines"] == "Brake chamber\nClevis pin (x2)"
    assert b"Replace brake chamber" in resp.data


def test_view_missing_work_order(client):
    resp = client.get("/work-orders/999")
    assert resp.headers["Location"] == "/work-orders"
    assert flashes(client) == ["Work order not found."]


def test_edit_work_order(client, query):
    vehicle_id = add_vehicle(client, unit_number="1")
    other_vehicle_id = add_vehicle(client, unit_number="2")
    wo_id = add_work_order(client, query, vehicle_id, **{
        "part_name[]": ["Old part"], "part_quantity[]": ["1"],
    })

    resp = client.post(f"/work-orders/{wo_id}/edit", content_type="multipart/form-data", data={
        "vehicle_id": str(other_vehicle_id),
        "status": "open",
        "created_by": "Alice",
        "assigned_to": "Dave",
        "description": "Updated description",
        "notes": "",
        "hours": "3",
        "scheduled_completion_date": "",
        "part_name[]": ["New part"],
        "part_quantity[]": ["4"],
    })
    assert resp.headers["Location"] == f"/work-orders/{wo_id}"

    wo = query("SELECT * FROM work_orders WHERE id = ?", (wo_id,))[0]
    assert wo["vehicle_id"] == other_vehicle_id
    assert wo["assigned_to"] == "Dave"
    assert wo["description"] == "Updated description"
    assert wo["notes"] is None
    # Parts list is replaced as a whole
    parts = query("SELECT part_name, quantity FROM work_order_parts")
    assert [(p["part_name"], p["quantity"]) for p in parts] == [("New part", 4)]


def test_edit_work_order_close_and_reopen(client, query):
    vehicle_id = add_vehicle(client)
    wo_id = add_work_order(client, query, vehicle_id)
    form = {"vehicle_id": str(vehicle_id), "description": "x"}

    client.post(f"/work-orders/{wo_id}/edit", data={**form, "status": "closed"}, content_type="multipart/form-data")
    row = query("SELECT status, closed_at FROM work_orders")[0]
    assert row["status"] == "closed" and row["closed_at"] is not None

    client.post(f"/work-orders/{wo_id}/edit", data={**form, "status": "open"}, content_type="multipart/form-data")
    row = query("SELECT status, closed_at FROM work_orders")[0]
    assert row["status"] == "open" and row["closed_at"] is None


def test_edit_work_order_rejects_invalid_vehicle(client, query):
    vehicle_id = add_vehicle(client)
    wo_id = add_work_order(client, query, vehicle_id)
    resp = client.post(f"/work-orders/{wo_id}/edit", data={"vehicle_id": "999", "description": "changed"},
                       content_type="multipart/form-data")
    assert resp.headers["Location"] == "/work-orders"
    assert flashes(client) == ["Select a valid unit number."]
    assert query("SELECT description FROM work_orders")[0][0] == "Replace brake chamber"


def test_close_work_order(client, query):
    vehicle_id = add_vehicle(client)
    wo_id = add_work_order(client, query, vehicle_id)
    resp = client.post(f"/work-orders/{wo_id}/close")
    assert resp.headers["Location"] == f"/work-orders/{wo_id}"
    row = query("SELECT status, closed_at FROM work_orders")[0]
    assert row["status"] == "closed"
    assert row["closed_at"] is not None


def test_delete_work_order(client, query):
    vehicle_id = add_vehicle(client)
    wo_id = add_work_order(client, query, vehicle_id, **{
        "part_name[]": ["Brake chamber"], "part_quantity[]": ["1"],
    })
    resp = client.post(f"/work-orders/{wo_id}/delete")
    assert resp.headers["Location"] == "/work-orders"
    assert flashes(client) == ["Work order deleted."]
    # Current behavior is a hard delete of the work order and its parts
    assert query("SELECT * FROM work_orders") == []
    assert query("SELECT * FROM work_order_parts") == []


def test_dashboard_lists_open_work_orders(client, query, captured):
    vehicle_id = add_vehicle(client)
    overdue_id = add_work_order(client, query, vehicle_id, scheduled_completion_date="2000-01-01")
    add_work_order(client, query, vehicle_id, status="closed")
    client.get("/")
    _, ctx = captured[-1]
    assert [(w["id"], w["overdue"]) for w in ctx["open_work_orders"]] == [(overdue_id, True)]


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
