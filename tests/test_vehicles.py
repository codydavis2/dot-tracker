from datetime import date

import pytest

from conftest import add_vehicle, flashes, select_options


def test_add_vehicle(client, query, users):
    vehicle_id = add_vehicle(client, unit_number="42", vin="1FUJGLDR5KLAB1234")
    row = query("SELECT * FROM vehicles WHERE id = %s", (vehicle_id,))[0]
    assert row["company_id"] == users["a_company"]
    assert row["unit_number"] == "42"
    assert row["make"] == "Freightliner"
    assert row["year"] == 2019
    assert row["vin"] == "1FUJGLDR5KLAB1234"
    assert row["mileage"] == 350000
    assert row["is_dot_regulated"] is False
    assert row["archived_at"] is None
    # Not DOT-regulated, so no dot_details row
    assert query("SELECT * FROM dot_details") == []


def test_add_dot_vehicle_creates_dot_details_row(client, query, users):
    vehicle_id = add_vehicle(client, is_dot_regulated="on")
    assert query("SELECT is_dot_regulated FROM vehicles WHERE id = %s", (vehicle_id,))[0]["is_dot_regulated"] is True
    details = query("SELECT * FROM dot_details WHERE vehicle_id = %s", (vehicle_id,))
    assert [d["company_id"] for d in details] == [users["a_company"]]


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


def test_add_vehicle_duplicate_vin_reports_vin_problem(client, query):
    add_vehicle(client, unit_number="1", vin="1FUJGLDR5KLAB1234")
    resp = client.post("/vehicles", data={"unit_number": "2", "make": "Mack", "model": "Anthem",
                                          "year": "2021", "vin": "1FUJGLDR5KLAB1234"})
    assert resp.headers["Location"] == "/vehicles"
    assert flashes(client) == ['VIN "1FUJGLDR5KLAB1234" is already in use.']
    assert len(query("SELECT * FROM vehicles")) == 1


def test_same_vin_and_unit_number_allowed_in_two_companies(client, other_client, query):
    add_vehicle(client, unit_number="42", vin="1FUJGLDR5KLAB1234")
    add_vehicle(other_client, unit_number="42", vin="1FUJGLDR5KLAB1234")
    assert len(query("SELECT * FROM vehicles WHERE vin = '1FUJGLDR5KLAB1234'")) == 2


def test_list_vehicles(client, captured):
    add_vehicle(client, unit_number="42")
    add_vehicle(client, unit_number="7", make="Peterbilt", model="579")
    resp = client.get("/vehicles")
    assert resp.status_code == 200
    name, ctx = captured[-1]
    assert name == "vehicles.html"
    assert sorted(v["unit_number"] for v in ctx["vehicles"]) == ["42", "7"]
    assert b"Peterbilt" in resp.data


def test_archived_vehicles_hidden_from_lists_and_dropdowns(client, query, captured):
    active_id = add_vehicle(client, unit_number="1")
    archived_id = add_vehicle(client, unit_number="2")
    query("UPDATE vehicles SET archived_at = now() WHERE id = %s", (archived_id,))

    client.get("/vehicles")
    assert [v["id"] for v in captured[-1][1]["vehicles"]] == [active_id]
    client.get("/")
    assert [v["id"] for v in captured[-1][1]["vehicles"]] == [active_id]

    for page in ("/work-orders", "/inspections", "/audit"):
        resp = client.get(page)
        assert [v["id"] for v in captured[-1][1]["vehicles"]] == [active_id], page
        values, _ = select_options(resp.get_data(as_text=True), "vehicle_id")
        assert str(archived_id) not in values, page

    # ...and can't be picked by submitting its id anyway
    resp = client.post("/inspections/reports", data={"vehicle_id": str(archived_id), "performed_by_user_id": "1"})
    assert flashes(client) == ["Select a valid unit number."]
    assert query("SELECT * FROM inspection_reports") == []


def test_archived_vehicle_detail_still_viewable(client, query):
    vehicle_id = add_vehicle(client)
    query("UPDATE vehicles SET archived_at = now() WHERE id = %s", (vehicle_id,))
    resp = client.get(f"/vehicles/{vehicle_id}")
    assert resp.status_code == 200
    assert b"Archived" in resp.data


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


def test_vehicle_detail_missing_vehicle_is_404(client):
    assert client.get("/vehicles/999").status_code == 404


def test_add_maintenance_log_with_parts(client, query, users):
    vehicle_id = add_vehicle(client)
    resp = client.post(f"/vehicles/{vehicle_id}/maintenance", data={
        "service_type": "Oil change",
        "log_date": "2026-02-01",
        "mileage_at_service": "352000",
        "cost": "189.50",
        "notes": "15W-40",
        "performed_by_user_id": str(users["a_mechanic"]),
        "part_name[]": ["Oil filter", "  ", "Fuel filter"],
        "part_quantity[]": ["1", "1", "abc"],
    })
    assert resp.headers["Location"] == f"/vehicles/{vehicle_id}"

    log = query("SELECT * FROM maintenance_logs")[0]
    assert log["company_id"] == users["a_company"]
    assert log["vehicle_id"] == vehicle_id
    assert log["service_type"] == "Oil change"
    assert log["log_date"] == date(2026, 2, 1)
    assert log["performed_by_user_id"] == users["a_mechanic"]

    parts = query("SELECT part_name, quantity, company_id FROM maintenance_parts ORDER BY id")
    # Blank part names are skipped; a non-numeric quantity becomes 1
    assert [(p["part_name"], p["quantity"]) for p in parts] == [("Oil filter", 1), ("Fuel filter", 1)]
    assert {p["company_id"] for p in parts} == {users["a_company"]}


def test_add_maintenance_log_requires_performed_by(client, query):
    vehicle_id = add_vehicle(client)
    resp = client.post(f"/vehicles/{vehicle_id}/maintenance", data={"service_type": "Oil change"})
    assert resp.headers["Location"] == f"/vehicles/{vehicle_id}"
    assert flashes(client) == ["Performed by is required."]
    assert query("SELECT * FROM maintenance_logs") == []


@pytest.mark.xfail(
    strict=True,
    reason="BUG: a maintenance log without service_type fails the NOT NULL constraint -> 500 "
           "(no validation). Maintenance logs have no UI form today.",
)
def test_add_maintenance_requires_service_type(app, client, users, monkeypatch):
    monkeypatch.setitem(app.config, "PROPAGATE_EXCEPTIONS", False)
    vehicle_id = add_vehicle(client)
    resp = client.post(f"/vehicles/{vehicle_id}/maintenance", data={
        "log_date": "2026-02-01", "performed_by_user_id": str(users["a_owner"]),
    })
    assert resp.status_code == 302


def test_add_dtc_uses_reference_description(client, query, users):
    vehicle_id = add_vehicle(client)
    resp = client.post(f"/vehicles/{vehicle_id}/dtc", data={"code": " p0420 ", "description": "ignored"})
    assert resp.headers["Location"] == f"/vehicles/{vehicle_id}"
    row = query("SELECT * FROM dtc_codes")[0]
    assert row["company_id"] == users["a_company"]
    assert row["code"] == "P0420"
    assert row["description"] == "Catalyst system efficiency below threshold (Bank 1)"


def test_add_dtc_unknown_code_uses_form_description(client, query):
    vehicle_id = add_vehicle(client)
    client.post(f"/vehicles/{vehicle_id}/dtc", data={"code": "spn 3226 fmi 2", "description": "NOx sensor"})
    row = query("SELECT * FROM dtc_codes")[0]
    assert row["code"] == "SPN 3226 FMI 2"
    assert row["description"] == "NOx sensor"
