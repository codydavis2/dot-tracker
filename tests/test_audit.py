from conftest import add_inspection_report, add_vehicle, add_work_order


def test_audit_page_without_dates_does_not_run(client, captured):
    resp = client.get("/audit")
    assert resp.status_code == 200
    _, ctx = captured[-1]
    assert ctx["ran_report"] is False
    assert ctx["work_orders"] == []
    assert ctx["inspection_reports"] == []


def close_on(app, wo_id, closed_at):
    """Set closed_at directly so the test controls which date range it falls in."""
    import sqlite3
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.execute("UPDATE work_orders SET status = 'closed', closed_at = ? WHERE id = ?", (closed_at, wo_id))
    conn.commit()
    conn.close()


def test_audit_report_for_date_range(app, client, query, captured):
    v1 = add_vehicle(client, unit_number="1")
    v2 = add_vehicle(client, unit_number="2")

    in_range = add_work_order(client, query, v1, **{"part_name[]": ["Brake chamber"], "part_quantity[]": ["2"]})
    close_on(app, in_range, "2026-03-10 14:00:00")
    out_of_range = add_work_order(client, query, v1)
    close_on(app, out_of_range, "2026-05-01 09:00:00")
    add_work_order(client, query, v2)  # open work orders never appear

    report_in = add_inspection_report(client, query, v2, inspection_date="2026-03-20", status_0="out_of_spec")
    add_inspection_report(client, query, v1, inspection_date="2026-01-01")

    resp = client.get("/audit?start_date=2026-03-01&end_date=2026-03-31")
    assert resp.status_code == 200
    _, ctx = captured[-1]
    assert ctx["ran_report"] is True
    assert ctx["doc_types"] == ["work_orders", "inspections"]
    assert ctx["selected_vehicle"] is None
    assert [w["id"] for w in ctx["work_orders"]] == [in_range]
    assert ctx["work_orders"][0]["parts_lines"] == "Brake chamber (x2)"
    assert [r["id"] for r in ctx["inspection_reports"]] == [report_in]
    assert ctx["inspection_reports"][0]["checklist_items"][0]["status"] == "out_of_spec"


def test_audit_report_filtered_by_vehicle_and_doc_type(app, client, query, captured):
    v1 = add_vehicle(client, unit_number="1")
    v2 = add_vehicle(client, unit_number="2")
    wo1 = add_work_order(client, query, v1)
    close_on(app, wo1, "2026-03-10 14:00:00")
    wo2 = add_work_order(client, query, v2)
    close_on(app, wo2, "2026-03-11 14:00:00")
    add_inspection_report(client, query, v1, inspection_date="2026-03-20")

    client.get(f"/audit?start_date=2026-03-01&end_date=2026-03-31&vehicle_id={v1}&doc_type=work_orders")
    _, ctx = captured[-1]
    assert ctx["selected_vehicle"]["id"] == v1
    assert [w["id"] for w in ctx["work_orders"]] == [wo1]
    assert ctx["inspection_reports"] == []
