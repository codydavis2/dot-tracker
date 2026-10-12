from conftest import add_inspection_report, add_vehicle, add_work_order, flashes


def test_audit_page_without_dates_does_not_run(client, captured):
    resp = client.get("/audit")
    assert resp.status_code == 200
    _, ctx = captured[-1]
    assert ctx["ran_report"] is False
    assert ctx["work_orders"] == []
    assert ctx["inspection_reports"] == []


def close_on(query, wo_id, closed_at):
    """Set closed_at directly so the test controls which date range it falls in."""
    query("UPDATE work_orders SET closed_at = %s WHERE id = %s", (closed_at, wo_id))


def test_audit_report_for_date_range(client, query, captured, users):
    v1 = add_vehicle(client, unit_number="1")
    v2 = add_vehicle(client, unit_number="2")

    in_range = add_work_order(client, query, v1, status="closed", performed_by_user_id=str(users["a_mechanic"]),
                              **{"part_name[]": ["Brake chamber"], "part_quantity[]": ["2"]})
    close_on(query, in_range, "2026-03-10 14:00:00+00")
    out_of_range = add_work_order(client, query, v1, status="closed")
    close_on(query, out_of_range, "2026-05-01 09:00:00+00")
    add_work_order(client, query, v2)  # open work orders never appear

    report_in = add_inspection_report(client, query, v2, inspection_date="2026-03-20", status_0="out_of_spec",
                                      performed_by_user_id=str(users["a_mechanic"]))
    add_inspection_report(client, query, v1, inspection_date="2026-01-01")

    resp = client.get("/audit?start_date=2026-03-01&end_date=2026-03-31")
    assert resp.status_code == 200
    _, ctx = captured[-1]
    assert ctx["ran_report"] is True
    assert ctx["doc_types"] == ["work_orders", "inspections"]
    assert ctx["selected_vehicle"] is None
    assert [w["id"] for w in ctx["work_orders"]] == [in_range]
    assert ctx["work_orders"][0]["parts_lines"] == "Brake chamber (x2)"
    assert ctx["work_orders"][0]["performed_by_name"] == "Mechanic A"
    assert [r["id"] for r in ctx["inspection_reports"]] == [report_in]
    assert ctx["inspection_reports"][0]["performed_by_name"] == "Mechanic A"
    assert ctx["inspection_reports"][0]["checklist_items"][0]["status"] == "out_of_spec"
    html = resp.get_data(as_text=True)
    assert "2026-03-10" in html  # closed date rendered through the date filter


def test_audit_closed_date_boundary_uses_utc_calendar_date(client, query, captured):
    vehicle_id = add_vehicle(client)
    wo_id = add_work_order(client, query, vehicle_id, status="closed")
    close_on(query, wo_id, "2026-03-31 23:30:00+00")
    client.get("/audit?start_date=2026-03-01&end_date=2026-03-31")
    assert [w["id"] for w in captured[-1][1]["work_orders"]] == [wo_id]
    client.get("/audit?start_date=2026-04-01&end_date=2026-04-30")
    assert captured[-1][1]["work_orders"] == []


def test_audit_report_filtered_by_vehicle_and_doc_type(client, query, captured):
    v1 = add_vehicle(client, unit_number="1")
    v2 = add_vehicle(client, unit_number="2")
    wo1 = add_work_order(client, query, v1, status="closed")
    close_on(query, wo1, "2026-03-10 14:00:00+00")
    wo2 = add_work_order(client, query, v2, status="closed")
    close_on(query, wo2, "2026-03-11 14:00:00+00")
    add_inspection_report(client, query, v1, inspection_date="2026-03-20")

    client.get(f"/audit?start_date=2026-03-01&end_date=2026-03-31&vehicle_id={v1}&doc_type=work_orders")
    _, ctx = captured[-1]
    assert ctx["selected_vehicle"]["id"] == v1
    assert [w["id"] for w in ctx["work_orders"]] == [wo1]
    assert ctx["inspection_reports"] == []


def test_audit_invalid_dates(client, captured):
    resp = client.get("/audit?start_date=03/01/2026&end_date=2026-03-31")
    assert resp.status_code == 200
    assert captured[-1][1]["ran_report"] is False
    assert flashes(client) == [] and b"Enter valid From and To dates." in resp.data
