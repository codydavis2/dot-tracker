import pytest

from conftest import add_vehicle, flashes


def test_add_vehicle(client, query):
    vehicle_id = add_vehicle(client, unit_number="42", vin="1FUJGLDR5KLAB1234")
    row = query("SELECT * FROM vehicles WHERE id = ?", (vehicle_id,))[0]
    assert row["unit_number"] == "42"
    assert row["make"] == "Freightliner"
    assert row["year"] == 2019
    assert row["vin"] == "1FUJGLDR5KLAB1234"
    assert row["mileage"] == 350000
    assert row["is_dot_regulated"] == 0
    # Not DOT-regulated, so no dot_details row
    assert query("SELECT * FROM dot_details") == []


def test_add_dot_vehicle_creates_dot_details_row(client, query):
    vehicle_id = add_vehicle(client, is_dot_regulated="on")
    assert query("SELECT is_dot_regulated FROM vehicles WHERE id = ?", (vehicle_id,))[0][0] == 1
    assert len(query("SELECT * FROM dot_details WHERE vehicle_id = ?", (vehicle_id,))) == 1


def test_add_vehicle_requires_make_model_year(client, query):
    resp = client.post("/vehicles", data={"make": "", "model": "Cascadia", "year": "2019"})
    assert resp.headers["Location"] == "/vehicles"
    assert flashes(client) == ["Make, model, and year are required."]
    assert query("SELECT * FROM vehicles") == []


def test_add_vehicle_duplicate_unit_number(client, query):
    add_vehicle(client, unit_number="42")
    resp = client.post("/vehicles", data={"unit_number": "42", "make": "Mack", "model": "Anthem", "year": "2021"})
    assert resp.headers["Location"] == "/vehicles"
    assert flashes(client) == ['Unit number "42" is already in use.']
    assert len(query("SELECT * FROM vehicles")) == 1


@pytest.mark.xfail(
    strict=True,
    reason="BUG: a duplicate VIN hits the global UNIQUE(vin) constraint and the route reports it as "
           "a unit-number conflict ('Unit number \"None\" is already in use.').",
)
def test_add_vehicle_duplicate_vin_reports_vin_problem(client):
    add_vehicle(client, unit_number="1", vin="1FUJGLDR5KLAB1234")
    client.post("/vehicles", data={"unit_number": "", "make": "Mack", "model": "Anthem",
                                   "year": "2021", "vin": "1FUJGLDR5KLAB1234"})
    messages = flashes(client)
    assert len(messages) == 1
    assert "Unit number" not in messages[0]


def test_list_vehicles(client, captured):
    add_vehicle(client, unit_number="42")
    add_vehicle(client, unit_number="7", make="Peterbilt", model="579")
    resp = client.get("/vehicles")
    assert resp.status_code == 200
    name, ctx = captured[-1]
    assert name == "vehicles.html"
    assert sorted(v["unit_number"] for v in ctx["vehicles"]) == ["42", "7"]
    assert b"Peterbilt" in resp.data


def test_vehicle_detail(client, captured):
    vehicle_id = add_vehicle(client, unit_number="42", is_dot_regulated="on")
    resp = client.get(f"/vehicles/{vehicle_id}")
    assert resp.status_code == 200
    name, ctx = captured[-1]
    assert name == "vehicle_detail.html"
    assert ctx["vehicle"]["id"] == vehicle_id
    assert ctx["dot_details"] is not None
    assert ctx["reminders"] == []
    assert ctx["work_orders"] == []


def test_vehicle_detail_missing_vehicle(client):
    resp = client.get("/vehicles/999")
    assert resp.headers["Location"] == "/vehicles"
    assert flashes(client) == ["Vehicle not found."]


def test_add_maintenance_log_with_parts(client, query):
    vehicle_id = add_vehicle(client)
    resp = client.post(f"/vehicles/{vehicle_id}/maintenance", data={
        "service_type": "Oil change",
        "log_date": "2026-02-01",
        "mileage_at_service": "352000",
        "cost": "189.50",
        "notes": "15W-40",
        "mechanic_name": "Mike",
        "part_name[]": ["Oil filter", "  ", "Fuel filter"],
        "part_quantity[]": ["1", "1", "abc"],
    })
    assert resp.headers["Location"] == f"/vehicles/{vehicle_id}"

    log = query("SELECT * FROM maintenance_logs")[0]
    assert log["vehicle_id"] == vehicle_id
    assert log["service_type"] == "Oil change"
    assert log["log_date"] == "2026-02-01"
    assert log["mechanic_name"] == "Mike"

    parts = query("SELECT part_name, quantity FROM maintenance_parts ORDER BY id")
    # Blank part names are skipped; a non-numeric quantity becomes 1
    assert [(p["part_name"], p["quantity"]) for p in parts] == [("Oil filter", 1), ("Fuel filter", 1)]


def test_add_dtc_uses_reference_description(client, query):
    vehicle_id = add_vehicle(client)
    resp = client.post(f"/vehicles/{vehicle_id}/dtc", data={"code": " p0420 ", "description": "ignored"})
    assert resp.headers["Location"] == f"/vehicles/{vehicle_id}"
    row = query("SELECT * FROM dtc_codes")[0]
    assert row["code"] == "P0420"
    assert row["description"] == "Catalyst system efficiency below threshold (Bank 1)"


def test_add_dtc_unknown_code_uses_form_description(client, query):
    vehicle_id = add_vehicle(client)
    client.post(f"/vehicles/{vehicle_id}/dtc", data={"code": "spn 3226 fmi 2", "description": "NOx sensor"})
    row = query("SELECT * FROM dtc_codes")[0]
    assert row["code"] == "SPN 3226 FMI 2"
    assert row["description"] == "NOx sensor"


@pytest.mark.xfail(
    strict=True,
    reason="BUG: a maintenance log without service_type fails the NOT NULL constraint -> 500 "
           "(no validation). Maintenance logs have no UI form today.",
)
def test_add_maintenance_requires_service_type(app, client, monkeypatch):
    monkeypatch.setitem(app.config, "PROPAGATE_EXCEPTIONS", False)
    vehicle_id = add_vehicle(client)
    resp = client.post(f"/vehicles/{vehicle_id}/maintenance", data={"log_date": "2026-02-01"})
    assert resp.status_code == 302
