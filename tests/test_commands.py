"""The `flask migrate` and `flask seed-demo` commands."""
from app.migrate import apply_migrations
from conftest import TEST_DATABASE_URL


def test_migrate_is_idempotent(app):
    assert apply_migrations(TEST_DATABASE_URL, echo=lambda *args: None) == []
    result = app.test_cli_runner().invoke(args=["migrate"])
    assert result.exit_code == 0
    assert "Database is up to date." in result.output


def test_seed_demo(app, query):
    result = app.test_cli_runner().invoke(args=["seed-demo"])
    assert result.exit_code == 0, result.output
    assert "owner@demofleet.example.com" in result.output

    company = query("SELECT * FROM companies")[0]
    assert company["name"] == "Demo Fleet Co"
    roles = sorted(u["role"] for u in query("SELECT role FROM users"))
    assert roles == ["driver", "manager", "mechanic", "owner"]
    vehicles = query("SELECT * FROM vehicles")
    assert len(vehicles) == 6
    assert {v["is_dot_regulated"] for v in vehicles} == {True, False}
    statuses = {w["status"] for w in query("SELECT status FROM work_orders")}
    assert statuses == {"open", "closed"}
    assert query("SELECT count(*) AS n FROM work_order_parts")[0]["n"] > 0
    assert query("SELECT count(*) AS n FROM inspection_reports")[0]["n"] > 0
    assert query("SELECT count(*) AS n FROM inventory WHERE quantity <= low_stock_threshold")[0]["n"] >= 1
    assert query("SELECT count(*) AS n FROM inspection_reminders WHERE due_date < current_date")[0]["n"] >= 1

    # Printed passwords work
    password = next(line.split("password: ")[1] for line in result.output.splitlines() if "owner@" in line)
    client = app.test_client()
    resp = client.post("/login", data={"email": "owner@demofleet.example.com", "password": password.strip()})
    assert resp.headers["Location"] == "/"
    html = client.get("/").get_data(as_text=True)

    # Driver pre-trips: one red alert (no work order, not reviewed), one red linked to a
    # work order, one red marked reviewed
    pre_trips = query(
        """
        SELECT r.id, r.reviewed_at, v.unit_number,
               EXISTS (SELECT 1 FROM inspection_report_items i
                       WHERE i.inspection_report_id = r.id AND i.status = 'out_of_spec') AS red,
               EXISTS (SELECT 1 FROM work_orders w WHERE w.inspection_report_id = r.id) AS linked
        FROM inspection_reports r
        JOIN users u ON u.id = r.performed_by_user_id
        JOIN vehicles v ON v.id = r.vehicle_id
        WHERE r.inspection_type = 'pre_trip' AND u.role = 'driver'
        """
    )
    red = {r["unit_number"]: r for r in pre_trips if r["red"]}
    assert red["102"]["linked"] is False and red["102"]["reviewed_at"] is None
    assert red["101"]["linked"] is True
    assert red["202"]["reviewed_at"] is not None and red["202"]["linked"] is False
    assert {r["inspection_type"] for r in query("SELECT inspection_type FROM inspection_reports")} == {
        "pre_trip", "monthly", "annual"}
    assert html.count("OUT OF SPEC:") == 1
    assert "RF steer tire, bulge in sidewall" in html


def test_seed_demo_refuses_to_run_twice(app, query):
    runner = app.test_cli_runner()
    assert runner.invoke(args=["seed-demo"]).exit_code == 0
    result = runner.invoke(args=["seed-demo"])
    assert result.exit_code != 0
    assert "already exists" in result.output
    assert len(query("SELECT * FROM companies")) == 1
