"""Soft-deleted work orders and inspection reports disappear everywhere:
lists, detail pages (404), the dashboard, and audit reports."""
from conftest import add_inspection_report, add_vehicle, add_work_order


def test_soft_deleted_work_order_disappears_everywhere(client, query, captured):
    vehicle_id = add_vehicle(client)
    open_id = add_work_order(client, query, vehicle_id, scheduled_completion_date="2027-03-10")
    closed_id = add_work_order(client, query, vehicle_id, status="closed")
    query("UPDATE work_orders SET closed_at = '2027-03-11 12:00:00+00' WHERE id = %s", (closed_id,))
    for wo_id in (open_id, closed_id):
        assert client.post(f"/work-orders/{wo_id}/delete").status_code == 302

    client.get("/work-orders")
    _, ctx = captured[-1]
    assert ctx["open_work_orders"] == [] and ctx["closed_work_orders"] == []

    assert client.get(f"/work-orders/{open_id}").status_code == 404
    assert client.post(f"/work-orders/{open_id}/close", data={"performed_by_user_id": "1"}).status_code == 404
    assert client.post(f"/work-orders/{open_id}/edit", data={}).status_code == 404

    client.get("/?year=2027&month=3")
    _, ctx = captured[-1]
    assert ctx["open_work_orders"] == []
    assert ctx["events_by_day"] == {}

    client.get(f"/vehicles/{vehicle_id}")
    assert captured[-1][1]["work_orders"] == []

    client.get("/audit?start_date=2027-01-01&end_date=2027-12-31")
    assert captured[-1][1]["work_orders"] == []


def test_soft_deleted_inspection_report_disappears_everywhere(client, query, captured):
    vehicle_id = add_vehicle(client)
    report_id = add_inspection_report(client, query, vehicle_id, inspection_date="2026-03-15", status_0="out_of_spec")
    query("UPDATE inspection_reports SET deleted_at = now() WHERE id = %s", (report_id,))

    client.get("/inspections")
    assert captured[-1][1]["reports"] == []

    assert client.get(f"/inspections/reports/{report_id}").status_code == 404

    client.get("/audit?start_date=2026-01-01&end_date=2026-12-31")
    assert captured[-1][1]["inspection_reports"] == []
