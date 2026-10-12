from datetime import date

import pytest

from app.constants import INSPECTION_CHECKLIST as CHECKLIST
from conftest import add_inspection_report, add_reminder, add_vehicle, flashes, select_options


def test_inspections_page(client, captured, users):
    add_vehicle(client)
    resp = client.get("/inspections")
    assert resp.status_code == 200
    name, ctx = captured[-1]
    assert name == "inspections.html"
    assert ctx["checklist"] == CHECKLIST
    assert len(ctx["vehicles"]) == 1
    assert ctx["reports"] == []
    assert ctx["upcoming"] == []
    # Inspector dropdown: required, pre-selected to the logged-in user
    values, selected = select_options(resp.get_data(as_text=True), "performed_by_user_id")
    assert selected == str(users["a_owner"])
    assert set(values) == {str(users["a_owner"]), str(users["a_mechanic"])}


def test_submit_report_with_out_of_spec_item(client, query, users):
    vehicle_id = add_vehicle(client)
    brakes = CHECKLIST.index("Brakes")
    tires = CHECKLIST.index("Tires")
    resp = client.post("/inspections/reports", data={
        "vehicle_id": str(vehicle_id),
        "performed_by_user_id": str(users["a_mechanic"]),
        "inspection_date": "2026-03-15",
        "mileage": "351000",
        "notes": "Left steer tire low",
        f"status_{brakes}": "out_of_spec",
        f"comments_{brakes}": "Pushrod out of adjustment",
        f"status_{tires}": "needs_attention",
        "status_0": "bogus",  # invalid statuses are stored as 'good'
    })
    report_id = query("SELECT id FROM inspection_reports")[0]["id"]
    assert resp.headers["Location"] == f"/inspections/reports/{report_id}?new=1"

    report = query("SELECT * FROM inspection_reports WHERE id = %s", (report_id,))[0]
    assert report["company_id"] == users["a_company"]
    assert report["vehicle_id"] == vehicle_id
    assert report["performed_by_user_id"] == users["a_mechanic"]
    assert report["inspection_date"] == date(2026, 3, 15)
    assert report["mileage"] == 351000

    items = query("SELECT item, status, comments, company_id FROM inspection_report_items ORDER BY id")
    assert [i["item"] for i in items] == CHECKLIST
    assert {i["company_id"] for i in items} == {users["a_company"]}
    by_item = {i["item"]: i for i in items}
    assert by_item["Brakes"]["status"] == "out_of_spec"
    assert by_item["Brakes"]["comments"] == "Pushrod out of adjustment"
    assert by_item["Tires"]["status"] == "needs_attention"
    assert by_item[CHECKLIST[0]]["status"] == "good"


def test_submit_report_defaults_date_to_today(client, query, users):
    vehicle_id = add_vehicle(client)
    client.post("/inspections/reports", data={"vehicle_id": str(vehicle_id),
                                              "performed_by_user_id": str(users["a_owner"])})
    assert query("SELECT inspection_date FROM inspection_reports")[0]["inspection_date"] == date.today()


def test_submit_report_requires_inspector(client, query):
    vehicle_id = add_vehicle(client)
    resp = client.post("/inspections/reports", data={"vehicle_id": str(vehicle_id)})
    assert resp.headers["Location"] == "/inspections"
    assert flashes(client) == ["Inspector is required."]
    assert query("SELECT * FROM inspection_reports") == []


def test_submit_report_requires_valid_vehicle(client, query, users):
    resp = client.post("/inspections/reports", data={"vehicle_id": "999", "performed_by_user_id": str(users["a_owner"])})
    assert resp.headers["Location"] == "/inspections"
    assert flashes(client) == ["Select a valid unit number."]
    assert query("SELECT * FROM inspection_reports") == []


def test_view_new_report_prompts_for_work_order(client, query, captured, users):
    vehicle_id = add_vehicle(client)
    brakes = CHECKLIST.index("Brakes")
    report_id = add_inspection_report(client, query, vehicle_id, **{
        "performed_by_user_id": str(users["a_mechanic"]),
        f"status_{brakes}": "out_of_spec", f"comments_{brakes}": "Pushrod out of adjustment",
    })

    resp = client.get(f"/inspections/reports/{report_id}?new=1")
    assert resp.status_code == 200
    name, ctx = next(c for c in captured if c[0] == "inspection_report_detail.html")
    assert ctx["report"]["id"] == report_id
    assert ctx["report"]["performed_by_name"] == "Mechanic A"
    assert len(ctx["items"]) == len(CHECKLIST)
    assert [i["item"] for i in ctx["out_of_spec_items"]] == ["Brakes"]
    assert ctx["work_order_prefill_description"] == "Brakes: Pushrod out of adjustment"
    assert ctx["prompt_work_order"] is True
    assert b"Create a work order" in resp.data
    assert b"Mechanic A" in resp.data


def test_view_report_without_new_flag_does_not_prompt(client, query, captured):
    vehicle_id = add_vehicle(client)
    report_id = add_inspection_report(client, query, vehicle_id, status_0="out_of_spec")
    client.get(f"/inspections/reports/{report_id}")
    _, ctx = next(c for c in captured if c[0] == "inspection_report_detail.html")
    assert ctx["prompt_work_order"] is False


def test_report_listed_with_flagged_count_and_inspector(client, query, captured):
    vehicle_id = add_vehicle(client)
    report_id = add_inspection_report(client, query, vehicle_id, status_0="out_of_spec", status_1="needs_attention")
    client.get("/inspections")
    _, ctx = captured[-1]
    assert [(r["id"], r["flagged_count"], r["performed_by_name"]) for r in ctx["reports"]] == [(report_id, 2, "Owner A")]


def test_view_missing_report_is_404(client):
    assert client.get("/inspections/reports/999").status_code == 404


def test_add_one_time_reminder(client, query, users):
    vehicle_id = add_vehicle(client)
    resp = client.post(f"/vehicles/{vehicle_id}/reminders", data={
        "reminder_type": "dot_annual", "due_date": "2027-01-15", "interval": "none",
        "assigned_to_user_id": str(users["a_mechanic"]),
    })
    assert resp.headers["Location"] == f"/vehicles/{vehicle_id}"
    rows = query("SELECT * FROM inspection_reminders")
    assert len(rows) == 1
    assert rows[0]["company_id"] == users["a_company"]
    assert rows[0]["reminder_type"] == "dot_annual"
    assert rows[0]["due_date"] == date(2027, 1, 15)
    assert rows[0]["status"] == "upcoming"
    assert rows[0]["assigned_to_user_id"] == users["a_mechanic"]


def test_reminder_assigned_to_is_optional(client, query):
    vehicle_id = add_vehicle(client)
    add_reminder(client, query, vehicle_id)
    assert query("SELECT assigned_to_user_id FROM inspection_reminders")[0]["assigned_to_user_id"] is None


def test_add_recurring_monthly_reminder_creates_two_years(client, query):
    vehicle_id = add_vehicle(client)
    add_reminder(client, query, vehicle_id, reminder_type="monthly", due_date="2027-01-31", interval="monthly")
    due_dates = [r["due_date"] for r in query("SELECT due_date FROM inspection_reminders ORDER BY due_date")]
    assert len(due_dates) == 25
    assert due_dates[:3] == [date(2027, 1, 31), date(2027, 2, 28), date(2027, 3, 31)]
    assert due_dates[-1] == date(2029, 1, 31)


def test_add_reminder_custom_interval_quarterly(client, query):
    vehicle_id = add_vehicle(client)
    add_reminder(client, query, vehicle_id, reminder_type="quarterly", due_date="2027-01-01",
                 interval="custom", custom_value="3", custom_unit="months")
    due_dates = [r["due_date"] for r in query("SELECT due_date FROM inspection_reminders ORDER BY due_date")]
    assert due_dates[:3] == [date(2027, 1, 1), date(2027, 4, 1), date(2027, 7, 1)]
    assert len(due_dates) == 9


def test_add_reminder_invalid_custom_interval(client, query):
    vehicle_id = add_vehicle(client)
    resp = client.post(f"/vehicles/{vehicle_id}/reminders", data={
        "reminder_type": "other", "due_date": "2027-01-01", "interval": "custom",
        "custom_value": "0", "custom_unit": "months",
    })
    assert resp.headers["Location"] == f"/vehicles/{vehicle_id}"
    assert flashes(client) == ["Enter a valid custom interval."]
    assert query("SELECT * FROM inspection_reminders") == []


def test_add_reminder_requires_due_date(client, query):
    vehicle_id = add_vehicle(client)
    client.post(f"/vehicles/{vehicle_id}/reminders", data={"reminder_type": "other", "due_date": ""})
    assert flashes(client) == ["Due date is required."]
    assert query("SELECT * FROM inspection_reminders") == []


def test_reminder_shows_on_inspections_and_vehicle_pages(client, query, captured, users):
    vehicle_id = add_vehicle(client)
    add_reminder(client, query, vehicle_id, reminder_type="pre_trip", due_date="2000-01-01",
                 assigned_to_user_id=str(users["a_mechanic"]))
    client.get("/inspections")
    _, ctx = captured[-1]
    assert [(r["reminder_type"], r["bucket"], r["assigned_to_name"]) for r in ctx["upcoming"]] == [
        ("pre_trip", "overdue", "Mechanic A")]

    client.get(f"/vehicles/{vehicle_id}")
    _, ctx = captured[-1]
    assert [r["bucket"] for r in ctx["reminders"]] == ["overdue"]


def test_complete_reminder(client, query, captured):
    vehicle_id = add_vehicle(client)
    reminder_id = add_reminder(client, query, vehicle_id)

    resp = client.post(f"/reminders/{reminder_id}/complete")
    # No Referer header, so it falls back to the vehicle page
    assert resp.headers["Location"] == f"/vehicles/{vehicle_id}"
    assert query("SELECT status FROM inspection_reminders")[0]["status"] == "completed"

    client.get("/inspections")
    _, ctx = captured[-1]
    assert ctx["upcoming"] == []


def test_complete_reminder_redirects_to_referrer(client, query):
    vehicle_id = add_vehicle(client)
    reminder_id = add_reminder(client, query, vehicle_id)
    resp = client.post(f"/reminders/{reminder_id}/complete", headers={"Referer": "/inspections"})
    assert resp.headers["Location"] == "/inspections"


def test_complete_missing_reminder_is_404(client):
    assert client.post("/reminders/999/complete").status_code == 404


def test_dashboard_shows_overdue_dot_annual_warning(client, query, captured):
    vehicle_id = add_vehicle(client)
    add_reminder(client, query, vehicle_id, due_date="2000-01-01")
    resp = client.get("/")
    assert resp.status_code == 200
    _, ctx = captured[-1]
    assert [w["vehicle_id"] for w in ctx["dot_warnings"]] == [vehicle_id]


@pytest.mark.xfail(
    strict=True,
    reason="BUG: a due date that isn't YYYY-MM-DD raises ValueError in date.fromisoformat -> 500. "
           "The form's date input prevents this in normal use.",
)
def test_add_reminder_rejects_malformed_due_date(app, client, query, monkeypatch):
    monkeypatch.setitem(app.config, "PROPAGATE_EXCEPTIONS", False)
    vehicle_id = add_vehicle(client)
    resp = client.post(f"/vehicles/{vehicle_id}/reminders", data={"reminder_type": "other", "due_date": "01/15/2027"})
    assert resp.status_code == 302


@pytest.mark.xfail(
    strict=True,
    reason="BUG: an unknown reminder_type fails the CHECK constraint mid-insert -> 500 instead of a "
           "validation message. The form's select prevents this in normal use.",
)
def test_add_reminder_rejects_unknown_type(app, client, query, monkeypatch):
    monkeypatch.setitem(app.config, "PROPAGATE_EXCEPTIONS", False)
    vehicle_id = add_vehicle(client)
    resp = client.post(f"/vehicles/{vehicle_id}/reminders", data={"reminder_type": "bogus", "due_date": "2027-01-15"})
    assert resp.status_code == 302


def test_failed_write_does_not_lock_database(app, client, query, monkeypatch):
    """A request that crashes mid-write must not leave its transaction holding locks."""
    monkeypatch.setitem(app.config, "PROPAGATE_EXCEPTIONS", False)
    vehicle_id = add_vehicle(client)
    crashed = client.post(f"/vehicles/{vehicle_id}/reminders", data={"reminder_type": "bogus", "due_date": "2027-01-15"})
    assert crashed.status_code == 500
    resp = client.post("/inventory", data={"name": "Brake chamber"})
    assert resp.status_code == 302
    assert len(query("SELECT * FROM inventory")) == 1
