"""Red-item alerts: inspections that "need attention" (an Out of Spec item, not
deleted, no live linked work order, not reviewed), creating a linked work order
from one, and "Mark reviewed"."""
import pytest

from app.constants import PRE_TRIP_CHECKLIST
from conftest import add_inspection_report, add_vehicle, add_work_order, flashes, force_login, logged_in_client

TIRES = PRE_TRIP_CHECKLIST.index("Tires")
HORN = PRE_TRIP_CHECKLIST.index("Horn")


@pytest.fixture
def red(client, query, roles):
    """Company A: unit 42 with a driver's pre-trip that has an Out of Spec item."""
    vehicle_id = add_vehicle(client)
    report_id = add_inspection_report(client, query, vehicle_id, inspection_type="pre_trip",
                                      performed_by_user_id=str(roles["a_driver"]), inspection_date="2026-03-15",
                                      **{f"status_{TIRES}": "out_of_spec", f"comments_{TIRES}": "RF sidewall bulge",
                                         f"status_{HORN}": "needs_attention"})
    return {"vehicle_id": vehicle_id, "report_id": report_id}


def alert_ids(client, captured):
    client.get("/")
    _, ctx = next(c for c in reversed(captured) if c[0] == "dashboard.html")
    return [a["id"] for a in ctx["alerts"]]


def create_linked_work_order(client, query, red, **overrides):
    return add_work_order(client, query, red["vehicle_id"], inspection_report_id=str(red["report_id"]), **overrides)


@pytest.mark.parametrize("email", ["owner@a.example.com", "manager@a.example.com", "mechanic@a.example.com"])
def test_red_pre_trip_alerts_for_owner_manager_mechanic(app, red, captured, email):
    client = logged_in_client(app, email)
    resp = client.get("/")
    _, ctx = captured[-1]
    assert [a["id"] for a in ctx["alerts"]] == [red["report_id"]]
    alert = ctx["alerts"][0]
    assert alert["inspection_type"] == "pre_trip"
    assert alert["performed_by_name"] == "Driver A"
    assert [(i["item"], i["comments"]) for i in alert["out_of_spec_items"]] == [("Tires", "RF sidewall bulge")]
    html = resp.get_data(as_text=True)
    assert "OUT OF SPEC:" in html and "Unit 42" in html and "Pre-trip inspection 2026-03-15" in html
    assert "Tires — RF sidewall bulge" in html and "Horn" not in html  # yellow items aren't listed
    assert f'href="/work-orders?inspection_report_id={red["report_id"]}"' in html
    assert f'action="/inspections/reports/{red["report_id"]}/review"' in html


def test_alert_confirm_text_is_fixed(client, query, red, roles):
    """No record data in the confirm() JavaScript, even when a comment tries to break out."""
    add_inspection_report(client, query, red["vehicle_id"], inspection_type="pre_trip",
                          **{"status_0": "out_of_spec", "comments_0": "');alert(1);//"})
    html = client.get("/").get_data(as_text=True)
    confirms = [line for line in html.splitlines() if "confirm(" in line]
    assert confirms and all("alert(1)" not in line for line in confirms)
    assert "&#39;);alert(1);//" in html  # shown, escaped, in the alert body


def test_alerts_newest_first_and_any_type(client, query, red):
    later = add_inspection_report(client, query, red["vehicle_id"], inspection_type="monthly",
                                  inspection_date="2026-04-01", status_3="out_of_spec")
    annual = add_inspection_report(client, query, red["vehicle_id"], inspection_type="annual",
                                   inspection_date="2026-03-20", status_3="out_of_spec")
    html = client.get("/").get_data(as_text=True)
    order = [html.index(f"/inspections/reports/{rid}/review") for rid in (later, annual, red["report_id"])]
    assert order == sorted(order)


def test_yellow_only_inspection_never_alerts(client, query, captured):
    vehicle_id = add_vehicle(client)
    add_inspection_report(client, query, vehicle_id, inspection_type="pre_trip", status_0="needs_attention",
                          status_1="needs_attention")
    add_inspection_report(client, query, vehicle_id, inspection_type="general")  # all good
    assert alert_ids(client, captured) == []


def test_deleted_inspection_does_not_alert(client, query, red, captured):
    query("UPDATE inspection_reports SET deleted_at = now() WHERE id = %s", (red["report_id"],))
    assert alert_ids(client, captured) == []


def test_create_work_order_from_alert_prefills_and_links(client, query, red, captured, users):
    resp = client.get(f"/work-orders?inspection_report_id={red['report_id']}")
    assert resp.status_code == 200
    _, ctx = captured[-1]
    assert ctx["from_report"]["id"] == red["report_id"]
    assert ctx["prefill_vehicle_id"] == red["vehicle_id"]
    assert ctx["prefill_description"] == "Tires: RF sidewall bulge"
    html = resp.get_data(as_text=True)
    assert f'name="inspection_report_id" value="{red["report_id"]}"' in html
    assert "RF sidewall bulge</textarea>" in html

    wo_id = create_linked_work_order(client, query, red, description="Tires: RF sidewall bulge")
    assert query("SELECT inspection_report_id FROM work_orders WHERE id = %s", (wo_id,))[0] == {
        "inspection_report_id": red["report_id"]}
    assert alert_ids(client, captured) == []


def test_work_order_without_link_does_not_clear_alert(client, query, red, captured):
    add_work_order(client, query, red["vehicle_id"])
    assert alert_ids(client, captured) == [red["report_id"]]


def test_post_inspection_prompt_links_the_report(client, query, red):
    html = client.get(f"/inspections/reports/{red['report_id']}?new=1").get_data(as_text=True)
    assert "Create a work order" in html
    assert f'"/work-orders?inspection_report_id={red["report_id"]}"' in html


def test_mark_reviewed_clears_alert_and_sets_only_reviewed_at(client, query, red, captured):
    before = query("SELECT * FROM inspection_reports WHERE id = %s", (red["report_id"],))[0]
    items_before = query("SELECT * FROM inspection_report_items ORDER BY id")

    resp = client.post(f"/inspections/reports/{red['report_id']}/review", data={"next": "dashboard"})
    assert resp.headers["Location"] == "/"
    assert flashes(client) == ["Inspection marked reviewed."]

    after = query("SELECT * FROM inspection_reports WHERE id = %s", (red["report_id"],))[0]
    assert after["reviewed_at"] is not None
    assert {k: v for k, v in after.items() if k != "reviewed_at"} == {
        k: v for k, v in before.items() if k != "reviewed_at"}
    assert query("SELECT * FROM inspection_report_items ORDER BY id") == items_before
    assert query("SELECT * FROM work_orders") == []
    assert alert_ids(client, captured) == []

    # Again: no error, and the time isn't moved
    client.post(f"/inspections/reports/{red['report_id']}/review")
    assert query("SELECT reviewed_at FROM inspection_reports")[0]["reviewed_at"] == after["reviewed_at"]


def test_mark_reviewed_from_report_page(client, query, red):
    html = client.get(f"/inspections/reports/{red['report_id']}").get_data(as_text=True)
    assert "Needs attention:" in html and "Mark reviewed" in html
    resp = client.post(f"/inspections/reports/{red['report_id']}/review", data={"next": "report"})
    assert resp.headers["Location"] == f"/inspections/reports/{red['report_id']}"
    html = client.get(f"/inspections/reports/{red['report_id']}").get_data(as_text=True)
    assert "Reviewed on 20" in html
    assert "Mark reviewed" not in html and "Needs attention:" not in html


def test_report_page_lists_linked_work_orders(client, query, red):
    wo_id = create_linked_work_order(client, query, red, description="Replace RF steer tire")
    html = client.get(f"/inspections/reports/{red['report_id']}").get_data(as_text=True)
    assert f'href="/work-orders/{wo_id}"' in html and "Replace RF steer tire" in html
    assert "Mark reviewed" not in html
    assert 'href="/inspections/reports/' in client.get(f"/work-orders/{wo_id}").get_data(as_text=True)


def test_soft_deleting_linked_work_order_brings_alert_back(client, query, red, captured):
    wo_id = create_linked_work_order(client, query, red)
    assert alert_ids(client, captured) == []
    client.post(f"/work-orders/{wo_id}/delete")
    assert alert_ids(client, captured) == [red["report_id"]]


def test_soft_deleted_work_order_does_not_bring_back_reviewed_alert(client, query, red, captured):
    wo_id = create_linked_work_order(client, query, red)
    client.post(f"/inspections/reports/{red['report_id']}/review")
    client.post(f"/work-orders/{wo_id}/delete")
    assert alert_ids(client, captured) == []


def test_linking_another_companys_report_is_rejected(client, other_client, query, red, users, captured):
    b_vehicle = add_vehicle(other_client, unit_number="B1")
    b_report = add_inspection_report(other_client, query, b_vehicle, inspection_type="pre_trip", status_0="out_of_spec")
    resp = client.post("/work-orders", content_type="multipart/form-data", data={
        "vehicle_id": str(red["vehicle_id"]), "created_by_user_id": str(users["a_owner"]),
        "inspection_report_id": str(b_report)})
    assert resp.headers["Location"] == "/work-orders"
    assert flashes(client) == ["That inspection report wasn't found."]
    assert query("SELECT * FROM work_orders") == []
    # ...and company B's alert is untouched
    assert alert_ids(other_client, captured) == [b_report]


@pytest.mark.parametrize("bad", ["abc", "999999"])
def test_linking_unknown_report_is_rejected(client, query, red, users, bad):
    client.post("/work-orders", content_type="multipart/form-data", data={
        "vehicle_id": str(red["vehicle_id"]), "created_by_user_id": str(users["a_owner"]),
        "inspection_report_id": bad})
    assert flashes(client) == ["That inspection report wasn't found."]
    assert query("SELECT * FROM work_orders") == []


def test_linking_report_of_a_different_unit_is_rejected(client, query, red, users):
    other_unit = add_vehicle(client, unit_number="43")
    client.post("/work-orders", content_type="multipart/form-data", data={
        "vehicle_id": str(other_unit), "created_by_user_id": str(users["a_owner"]),
        "inspection_report_id": str(red["report_id"])})
    assert flashes(client) == ["The work order must be for the same unit as the inspection it comes from."]
    assert query("SELECT * FROM work_orders") == []


def test_prefill_from_deleted_or_unknown_report_is_404(client, query, red):
    assert client.get("/work-orders?inspection_report_id=999999").status_code == 404
    assert client.get("/work-orders?inspection_report_id=abc").status_code == 404
    query("UPDATE inspection_reports SET deleted_at = now() WHERE id = %s", (red["report_id"],))
    assert client.get(f"/work-orders?inspection_report_id={red['report_id']}").status_code == 404


# ---------- drivers never see alerts or "Mark reviewed" ----------

def test_driver_mark_reviewed_is_403(app, query, red, roles):
    driver = force_login(app.test_client(), query, roles["a_driver"])
    resp = driver.post(f"/inspections/reports/{red['report_id']}/review")
    assert resp.status_code == 403
    assert query("SELECT reviewed_at FROM inspection_reports")[0]["reviewed_at"] is None


def test_driver_sees_no_alert_ui_on_any_page(app, query, red, roles):
    driver = force_login(app.test_client(), query, roles["a_driver"])
    pages = ["/inspections", "/vehicles", f"/vehicles/{red['vehicle_id']}", "/account",
             f"/inspections/reports/{red['report_id']}", f"/inspections/reports/{red['report_id']}?new=1"]
    for page in pages:
        resp = driver.get(page)
        assert resp.status_code == 200, page
        html = resp.get_data(as_text=True)
        for marker in ("Mark reviewed", "/review", "OUT OF SPEC:", "Needs attention:", "inspection_report_id",
                       "Create a work order", "Create work order"):
            assert marker not in html, (page, marker)
    assert driver.get("/").status_code == 403
