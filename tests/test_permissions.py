"""The central permission map (app/permissions.py, CLAUDE.md rule 6).

* Every endpoint in app.url_map is in the map, and the map matches the agreed
  matrix written out below (so an accidental edit to the map fails here too).
* For every endpoint, method and role (plus logged out), the request is let
  through or denied exactly as the map says.
* An endpoint missing from the map is denied for everyone (fail closed).
"""
import functools

import pytest
from flask import url_for

from app.permissions import ANY_LOGGED_IN, PERMISSIONS, PUBLIC, can
from conftest import force_login

ROLES = [None, "owner", "manager", "mechanic", "driver"]

O, M, Mc, D = "owner", "manager", "mechanic", "driver"
OFFICE = {O, M}
SHOP = {O, M, Mc}
EVERYONE = {O, M, Mc, D}

# The agreed matrix (M3). Keep in sync with app/permissions.py and CLAUDE.md.
EXPECTED = {
    "static": PUBLIC,
    "auth.login": PUBLIC,
    "auth.register": PUBLIC,
    "auth.forgot_password": PUBLIC,
    "auth.reset_password": PUBLIC,
    "invites.accept_invite": PUBLIC,
    "auth.logout": ANY_LOGGED_IN,
    "account.index": ANY_LOGGED_IN,
    "account.change_name": ANY_LOGGED_IN,
    "account.change_password": ANY_LOGGED_IN,
    "users.index": OFFICE,
    "users.change_role": OFFICE,
    "users.rename": OFFICE,
    "users.deactivate": OFFICE,
    "users.reactivate": OFFICE,
    "invites.create_invite": OFFICE,
    "invites.resend_invite": OFFICE,
    "invites.revoke_invite": OFFICE,
    "dashboard.index": SHOP,
    "audit.audit": SHOP,
    "vehicles.vehicles": EVERYONE,
    "vehicles.vehicle_detail": EVERYONE,
    "vehicles.add_vehicle": OFFICE,
    "vehicles.add_maintenance": SHOP,
    "vehicles.add_dtc": SHOP,
    "inspections.inspections": EVERYONE,
    "inspections.add_inspection_report": EVERYONE,
    "inspections.inspection_report_detail": EVERYONE,
    "inspections.add_reminder": SHOP,
    "inspections.complete_reminder": SHOP,
    "inspections.mark_reviewed": SHOP,
    "work_orders.work_orders": SHOP,
    "work_orders.create_work_order": SHOP,
    "work_orders.work_order_detail": SHOP,
    "work_orders.edit_work_order": SHOP,
    "work_orders.close_work_order": SHOP,
    "work_orders.delete_work_order": OFFICE,
    "work_orders.download_attachment": SHOP,
    "work_orders.delete_attachment": OFFICE,
    "inventory.inventory": SHOP,
    "inventory.edit_inventory_item": SHOP,
}


@pytest.fixture
def role_users(roles):
    return {O: roles["a_owner"], M: roles["a_manager"], Mc: roles["a_mechanic"], D: roles["a_driver"]}


def client_for(app, query, role_users, role):
    client = app.test_client()
    if role is not None:
        force_login(client, query, role_users[role])
    return client


def every_request(app):
    """(endpoint, method, url) for every rule and method in the app. URL ids are a
    number that matches no record, so allowed requests stop at a 404 and change nothing."""
    requests = []
    with app.test_request_context():
        for rule in app.url_map.iter_rules():
            url = url_for(rule.endpoint, **{arg: 999999 for arg in rule.arguments})
            for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
                requests.append((rule.endpoint, method, url))
    return requests


def test_every_endpoint_is_in_the_permission_map(app):
    endpoints = {rule.endpoint for rule in app.url_map.iter_rules()}
    assert endpoints - set(PERMISSIONS) == set(), "add these endpoints to app/permissions.py"
    assert set(PERMISSIONS) - endpoints == set(), "stale entries in app/permissions.py"


def test_permission_map_matches_agreed_matrix():
    assert {k: (v if isinstance(v, str) else set(v)) for k, v in PERMISSIONS.items()} == EXPECTED


def test_can():
    assert can(None, "auth.login") and can("driver", "auth.login")
    assert not can(None, "account.index") and can("driver", "account.index")
    assert can("manager", "users.index") and not can("mechanic", "users.index")
    assert not can("owner", "no.such_endpoint")


def test_permission_matrix(app, role_users, query):
    """Each request is let through to its view, or not, exactly as the map says."""
    called = []

    def recording(endpoint, view):
        @functools.wraps(view)
        def wrapper(*args, **kwargs):
            called.append(endpoint)
            return view(*args, **kwargs)
        return wrapper

    for endpoint, view in list(app.view_functions.items()):
        app.view_functions[endpoint] = recording(endpoint, view)

    checked = 0
    for endpoint, method, url in every_request(app):
        for role in ROLES:
            client = client_for(app, query, role_users, role)
            called.clear()
            resp = client.open(url, method=method, data={})
            where = (role, method, url)
            if can(role, endpoint):
                assert called == [endpoint], where
                assert resp.status_code != 403, where
            else:
                assert called == [], where
                if role is None:
                    assert resp.status_code == 302 and resp.headers["Location"] == "/login", where
                else:
                    assert resp.status_code == 403, where
            resp.close()
            checked += 1
    assert checked >= 5 * len(EXPECTED)


def test_unmapped_endpoint_is_denied_for_everyone(make_app, role_users, query):
    app = make_app()
    app.add_url_rule("/test-only-unmapped", "test_only_unmapped", lambda: "unmapped secret")
    for role in ROLES:
        resp = client_for(app, query, role_users, role).get("/test-only-unmapped")
        assert resp.status_code == 403, role
        assert b"unmapped secret" not in resp.data


def test_unknown_url_is_still_404(app, role_users, query):
    assert client_for(app, query, role_users, None).get("/no-such-page").status_code == 404
    assert client_for(app, query, role_users, D).get("/no-such-page").status_code == 404
    assert client_for(app, query, role_users, O).delete("/vehicles").status_code == 405


@pytest.mark.parametrize("role, home", [(O, "/"), (M, "/"), (Mc, "/"), (D, "/inspections")])
def test_login_lands_on_home_for_role(app, roles, role, home):
    resp = app.test_client().post("/login", data={"email": f"{role}@a.example.com", "password": "pw123"})
    assert resp.headers["Location"] == home


@pytest.mark.parametrize("role, visible, hidden", [
    (O, ["/vehicles", "/work-orders", "/inspections", "/inventory", "/audit", "/users"], []),
    (M, ["/vehicles", "/work-orders", "/inspections", "/inventory", "/audit", "/users"], []),
    (Mc, ["/vehicles", "/work-orders", "/inspections", "/inventory", "/audit"], ["/users"]),
    (D, ["/vehicles", "/inspections"], ["/work-orders", "/inventory", "/audit", "/users"]),
])
def test_navbar_shows_only_usable_links(app, role_users, query, role, visible, hidden):
    html = client_for(app, query, role_users, role).get("/account").get_data(as_text=True)
    for path in visible:
        assert f'href="{path}"' in html, (role, path)
    for path in hidden:
        assert f'href="{path}"' not in html, (role, path)
    brand = 'href="/inspections">DOT' if role == D else 'href="/">DOT'
    assert brand in html
