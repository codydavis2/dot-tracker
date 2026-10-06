from datetime import date

import pytest

from app.constants import INSPECTION_CHECKLIST as CHECKLIST
from conftest import add_inspection_report, add_vehicle, flashes


def test_inspections_page(client, captured):
    add_vehicle(client)
    resp = client.get("/inspections")
    assert resp.status_code == 200
    name, ctx = captured[-1]
    assert name == "inspections.html"
    assert ctx["checklist"] == CHECKLIST
    assert len(ctx["vehicles"]) == 1
    assert ctx["reports"] == []
    assert ctx["upcoming"] == []


def test_submit_report_with_out_of_spec_item(client, query):
    vehicle_id = add_vehicle(client)
    brakes = CHECKLIST.index("Brakes")
    tires = CHECKLIST.index("Tires")
    resp = client.post("/inspections/reports", data={
        "vehicle_id": str(vehicle_id),
        "inspector_name": "Alice",
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

    report = query("SELECT * FROM inspection_reports WHERE id = ?", (report_id,))[0]
    assert report["vehicle_id"] == vehicle_id
    assert report["inspector_name"] == "Alice"
    assert report["inspection_date"] == "2026-03-15"
    assert report["mileage"] == 351000

    items = query("SELECT item, status, comments FROM inspection_report_items ORDER BY id")
    assert [i["item"] for i in items] == CHECKLIST
    by_item = {i["item"]: i for i in items}
    assert by_item["Brakes"]["status"] == "out_of_spec"
    assert by_item["Brakes"]["comments"] == "Pushrod out of adjustment"
    assert by_item["Tires"]["status"] == "needs_attention"
    assert by_item[CHECKLIST[0]]["status"] == "good"


def test_submit_report_defaults_date_to_today(client, query):
    vehicle_id = add_vehicle(client)
    client.post("/inspections/reports", data={"vehicle_id": str(vehicle_id)})
    assert query("SELECT inspection_date FROM inspection_reports")[0][0] == date.today().isoformat()


def test_submit_report_requires_valid_vehicle(client, query):
    resp = client.post("/inspections/reports", data={"vehicle_id": "999"})
    assert resp.headers["Location"] == "/inspections"
    assert flashes(client) == ["Select a valid unit number."]
    assert query("SELECT * FROM inspection_reports") == []


def test_view_new_report_prompts_for_work_order(client, query, captured):
    vehicle_id = add_vehicle(client)
    brakes = CHECKLIST.index("Brakes")
    report_id = add_inspection_report(client, query, vehicle_id, **{
        f"status_{brakes}": "out_of_spec", f"comments_{brakes}": "Pushrod out of adjustment",
    })

    resp = client.get(f"/inspections/reports/{report_id}?new=1")
    assert resp.status_code == 200
    name, ctx = next(c for c in captured if c[0] == "inspection_report_detail.html")
    assert ctx["report"]["id"] == report_id
    assert len(ctx["items"]) == len(CHECKLIST)
    assert [i["item"] for i in ctx["out_of_spec_items"]] == ["Brakes"]
    assert ctx["work_order_prefill_description"] == "Brakes: Pushrod out of adjustment"
    assert ctx["prompt_work_order"] is True
    assert b"Create a work order" in resp.data


def test_view_report_without_new_flag_does_not_prompt(client, query, captured):
    vehicle_id = add_vehicle(client)
    report_id = add_inspection_report(client, query, vehicle_id, status_0="out_of_spec")
    client.get(f"/inspections/reports/{report_id}")
    _, ctx = next(c for c in captured if c[0] == "inspection_report_detail.html")
    assert ctx["prompt_work_order"] is False


def test_report_listed_with_flagged_count(client, query, captured):
    vehicle_id = add_vehicle(client)
    report_id = add_inspection_report(client, query, vehicle_id, status_0="out_of_spec", status_1="needs_attention")
    client.get("/inspections")
    _, ctx = captured[-1]
    assert [(r["id"], r["flagged_count"]) for r in ctx["reports"]] == [(report_id, 2)]


def test_view_missing_report(client):
    resp = client.get("/inspections/reports/999")
    assert resp.headers["Location"] == "/inspections"
    assert flashes(client) == ["Inspection report not found."]


def test_add_one_time_reminder(client, query):
    vehicle_id = add_vehicle(client)
    resp = client.post(f"/vehicles/{vehicle_id}/reminders", data={
        "reminder_type": "dot_annual", "due_date": "2027-01-15", "interval": "none", "mechanic_name": "Mike",
    })
    assert resp.headers["Location"] == f"/vehicles/{vehicle_id}"
    rows = query("SELECT * FROM inspection_reminders")
    assert len(rows) == 1
    assert rows[0]["reminder_type"] == "dot_annual"
    assert rows[0]["due_date"] == "2027-01-15"
    assert rows[0]["status"] == "upcoming"
    assert rows[0]["mechanic_name"] == "Mike"


def test_add_recurring_monthly_reminder_creates_two_years(client, query):
    vehicle_id = add_vehicle(client)
    client.post(f"/vehicles/{vehicle_id}/reminders", data={
        "reminder_type": "monthly", "due_date": "2027-01-31", "interval": "monthly",
    })
    due_dates = [r[0] for r in query("SELECT due_date FROM inspection_reminders ORDER BY due_date")]
    assert len(due_dates) == 25
    assert due_dates[:3] == ["2027-01-31", "2027-02-28", "2027-03-31"]
    assert due_dates[-1] == "2029-01-31"


def test_add_reminder_custom_interval_quarterly(client, query):
    vehicle_id = add_vehicle(client)
    client.post(f"/vehicles/{vehicle_id}/reminders", data={
        "reminder_type": "quarterly", "due_date": "2027-01-01", "interval": "custom",
        "custom_value": "3", "custom_unit": "months",
    })
    due_dates = [r[0] for r in query("SELECT due_date FROM inspection_reminders ORDER BY due_date")]
    assert due_dates[:3] == ["2027-01-01", "2027-04-01", "2027-07-01"]
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


def test_reminder_shows_on_inspections_and_vehicle_pages(client, query, captured):
    vehicle_id = add_vehicle(client)
    client.post(f"/vehicles/{vehicle_id}/reminders", data={
        "reminder_type": "pre_trip", "due_date": "2000-01-01", "interval": "none",
    })
    client.get("/inspections")
    _, ctx = captured[-1]
    assert [(r["reminder_type"], r["bucket"]) for r in ctx["upcoming"]] == [("pre_trip", "overdue")]

    client.get(f"/vehicles/{vehicle_id}")
    _, ctx = captured[-1]
    assert [r["bucket"] for r in ctx["reminders"]] == ["overdue"]


def test_complete_reminder(client, query, captured):
    vehicle_id = add_vehicle(client)
    client.post(f"/vehicles/{vehicle_id}/reminders", data={
        "reminder_type": "dot_annual", "due_date": "2027-01-15", "interval": "none",
    })
    reminder_id = query("SELECT id FROM inspection_reminders")[0]["id"]

    resp = client.post(f"/reminders/{reminder_id}/complete")
    # No Referer header, so it falls back to the vehicle page
    assert resp.headers["Location"] == f"/vehicles/{vehicle_id}"
    assert query("SELECT status FROM inspection_reminders")[0][0] == "completed"

    client.get("/inspections")
    _, ctx = captured[-1]
    assert ctx["upcoming"] == []


def test_complete_reminder_redirects_to_referrer(client, query):
    vehicle_id = add_vehicle(client)
    client.post(f"/vehicles/{vehicle_id}/reminders", data={
        "reminder_type": "other", "due_date": "2027-01-15", "interval": "none",
    })
    reminder_id = query("SELECT id FROM inspection_reminders")[0]["id"]
    resp = client.post(f"/reminders/{reminder_id}/complete", headers={"Referer": "/inspections"})
    assert resp.headers["Location"] == "/inspections"


def test_complete_missing_reminder(client):
    resp = client.post("/reminders/999/complete")
    assert resp.headers["Location"] == "/"
    assert flashes(client) == ["Reminder not found."]


def test_dashboard_shows_overdue_dot_annual_warning(client, captured):
    vehicle_id = add_vehicle(client)
    client.post(f"/vehicles/{vehicle_id}/reminders", data={
        "reminder_type": "dot_annual", "due_date": "2000-01-01", "interval": "none",
    })
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


@pytest.mark.xfail(
    strict=True,
    reason="BUG: when a route raises mid-write, its sqlite connection is never closed (no teardown "
           "handler), so the open transaction keeps the database write-locked and the next write "
           "fails with 'database is locked'.",
)
def test_failed_write_does_not_lock_database(app, client, query, monkeypatch):
    monkeypatch.setitem(app.config, "PROPAGATE_EXCEPTIONS", False)
    vehicle_id = add_vehicle(client)
    crashed = client.post(f"/vehicles/{vehicle_id}/reminders", data={"reminder_type": "bogus", "due_date": "2027-01-15"})
    assert crashed.status_code == 500
    resp = client.post("/inventory", data={"name": "Brake chamber"})
    assert resp.status_code == 302
