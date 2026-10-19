"""User management (owner and managers) and My Account."""
import pytest

from conftest import PASSWORD, flashes, logged_in_client, login, make_user


def snapshot(query):
    return (query("SELECT * FROM users ORDER BY id"), query("SELECT * FROM invites ORDER BY id"))


@pytest.fixture
def a_invite(client, users, outbox, query):
    """A pending invite in company A."""
    client.post("/users/invites", data={"email": "pending@example.com", "role": "driver"})
    flashes(client)
    return query("SELECT id FROM invites")[0]["id"]


def owner_routes(user_id, invite_id):
    """Every user-management and invite-management route: (method, path, form data)."""
    return [
        ("GET", "/users", {}),
        ("POST", "/users/invites", {"email": "x@example.com", "role": "mechanic"}),
        ("POST", f"/users/invites/{invite_id}/resend", {}),
        ("POST", f"/users/invites/{invite_id}/revoke", {}),
        ("POST", f"/users/{user_id}/role", {"role": "driver"}),
        ("POST", f"/users/{user_id}/name", {"full_name": "Renamed"}),
        ("POST", f"/users/{user_id}/deactivate", {}),
        ("POST", f"/users/{user_id}/reactivate", {}),
    ]


def test_owner_routes_cover_every_users_route(app):
    rules = {r.rule for r in app.url_map.iter_rules()
             if r.rule.startswith("/users")}
    listed = {path for _, path, _ in owner_routes(1, 1)}
    normalized = {rule.replace("<int:user_id>", "1").replace("<int:invite_id>", "1") for rule in rules}
    assert normalized == listed


@pytest.mark.parametrize("role", ["mechanic", "driver"])
def test_mechanics_and_drivers_get_403(app, users, a_invite, query, outbox, role):
    make_user(query, users["a_company"], f"{role}2@a.example.com", f"{role.title()} Two", role)
    client = logged_in_client(app, f"{role}2@a.example.com")
    before = snapshot(query)
    for method, path, data in owner_routes(users["a_mechanic"], a_invite):
        resp = client.open(path, method=method, data=data)
        assert resp.status_code == 403, (role, path)
    assert snapshot(query) == before
    assert len(outbox) == 1  # only the fixture's invite


def test_logged_out_redirects_to_login(anon, users, a_invite):
    for method, path, data in owner_routes(users["a_mechanic"], a_invite):
        resp = anon.open(path, method=method, data=data)
        assert resp.status_code == 302 and resp.headers["Location"] == "/login", path
    for path in ("/account",):
        assert anon.get(path).headers["Location"] == "/login"
    for path in ("/account/name", "/account/password"):
        assert anon.post(path, data={}).headers["Location"] == "/login"


def test_users_link_only_for_owner_and_managers(client, manager_client, mechanic_client, driver_client):
    assert b'href="/users"' in client.get("/").data
    assert b'href="/users"' in manager_client.get("/").data
    assert b'href="/users"' not in mechanic_client.get("/").data
    assert b'href="/users"' not in driver_client.get("/inspections").data


def test_users_page_lists_only_own_company(client, other_client, users, a_invite, query):
    make_user(query, users["a_company"], "gone@a.example.com", "Gone Guy", is_active=False)
    other_client.post("/users/invites", data={"email": "b-pending@example.com", "role": "driver"})
    html = client.get("/users").get_data(as_text=True)
    assert "Owner A" in html and "Mechanic A" in html and "Gone Guy" in html
    assert "pending@example.com" in html
    assert "Owner B" not in html and "Mechanic B" not in html and "b-pending@example.com" not in html
    # Sections: deactivated user is under "Deactivated", with a Reactivate button
    assert html.index("Gone Guy") > html.index("Deactivated")
    assert f"/users/{users['a_mechanic']}/deactivate" in html
    assert f"/users/{users['a_owner']}/deactivate" not in html


def test_change_role(client, users, query):
    resp = client.post(f"/users/{users['a_mechanic']}/role", data={"role": "manager"})
    assert resp.headers["Location"] == "/users"
    assert query("SELECT role FROM users WHERE id = %s", (users["a_mechanic"],))[0]["role"] == "manager"


@pytest.mark.parametrize("role", ["owner", "admin", ""])
def test_nobody_can_be_made_owner(client, users, query, role):
    client.post(f"/users/{users['a_mechanic']}/role", data={"role": role})
    assert flashes(client) == ["Choose a role: manager, mechanic, or driver."]
    assert query("SELECT role FROM users WHERE id = %s", (users["a_mechanic"],))[0]["role"] == "mechanic"


def test_owner_cannot_change_own_role(client, users, query):
    client.post(f"/users/{users['a_owner']}/role", data={"role": "manager"})
    assert flashes(client) == [
        "You can't change your own role. (The owner can hand over the company with Transfer ownership.)"]
    assert query("SELECT role FROM users WHERE id = %s", (users["a_owner"],))[0]["role"] == "owner"


def test_owner_cannot_deactivate_self(client, users, query):
    client.post(f"/users/{users['a_owner']}/deactivate")
    assert flashes(client) == ["You can't deactivate your own account."]
    row = query("SELECT is_active, auth_version FROM users WHERE id = %s", (users["a_owner"],))[0]
    assert row == {"is_active": True, "auth_version": 1}
    assert client.get("/").status_code == 200


def test_rename(client, users, query):
    client.post(f"/users/{users['a_mechanic']}/name", data={"full_name": "  Mike Rivera "})
    assert query("SELECT full_name FROM users WHERE id = %s", (users["a_mechanic"],))[0]["full_name"] == "Mike Rivera"
    client.post(f"/users/{users['a_mechanic']}/name", data={"full_name": " "})
    assert flashes(client)[-1] == "Full name can't be blank."
    assert query("SELECT full_name FROM users WHERE id = %s", (users["a_mechanic"],))[0]["full_name"] == "Mike Rivera"


def test_deactivate_and_reactivate(client, app, users, query):
    client.post(f"/users/{users['a_mechanic']}/deactivate")
    row = query("SELECT is_active, auth_version FROM users WHERE id = %s", (users["a_mechanic"],))[0]
    assert row == {"is_active": False, "auth_version": 2}
    assert login(app.test_client(), "mechanic@a.example.com").headers["Location"] == "/login"

    client.post(f"/users/{users['a_mechanic']}/reactivate")
    assert query("SELECT is_active FROM users WHERE id = %s", (users["a_mechanic"],))[0]["is_active"] is True
    assert login(app.test_client(), "mechanic@a.example.com").headers["Location"] == "/"


def test_unknown_ids_404(client):
    for path in ("/users/999999/role", "/users/999999/name", "/users/999999/deactivate",
                 "/users/999999/reactivate", "/users/invites/999999/resend", "/users/invites/999999/revoke"):
        assert client.post(path, data={"role": "driver", "full_name": "X"}).status_code == 404, path


# ---------- tenant isolation ----------

def test_other_company_owner_gets_404_on_users_and_invites(other_client, users, a_invite, query, outbox):
    before = snapshot(query)
    sent_before = len(outbox)
    for user_id in (users["a_mechanic"], users["a_owner"]):
        for method, path, data in owner_routes(user_id, a_invite):
            if path in ("/users", "/users/invites"):
                continue  # not per-record
            resp = other_client.open(path, method=method, data=data)
            assert resp.status_code == 404, path
    assert snapshot(query) == before
    assert len(outbox) == sent_before


# ---------- My Account ----------

def test_account_page(mechanic_client):
    html = mechanic_client.get("/account").get_data(as_text=True)
    assert "mechanic@a.example.com" in html and "Mechanic" in html and "Company A" in html
    assert 'href="/account"' in mechanic_client.get("/").get_data(as_text=True)


def test_change_own_name(mechanic_client, users, query):
    resp = mechanic_client.post("/account/name", data={"full_name": " Mike R. "})
    assert resp.headers["Location"] == "/account"
    rows = query("SELECT id, full_name FROM users WHERE full_name = 'Mike R.'")
    assert rows == [{"id": users["a_mechanic"], "full_name": "Mike R."}]
    mechanic_client.post("/account/name", data={"full_name": ""})
    assert flashes(mechanic_client)[-1] == "Full name can't be blank."


def test_change_password_requires_current_password(mechanic_client, app, users, query):
    mechanic_client.post("/account/password", data={
        "current_password": "wrong", "password": "new-air-filter", "confirmation": "new-air-filter"})
    assert flashes(mechanic_client) == ["Current password is incorrect."]
    assert query("SELECT auth_version FROM users WHERE id = %s", (users["a_mechanic"],))[0]["auth_version"] == 1
    assert login(app.test_client(), "mechanic@a.example.com", PASSWORD).headers["Location"] == "/"


def test_change_password(mechanic_client, app, users, query):
    mechanic_client.post("/account/password", data={
        "current_password": PASSWORD, "password": "new-air-filter", "confirmation": "new-air-filter"})
    assert flashes(mechanic_client) == ["Your password has been changed. Other devices have been logged out."]
    assert query("SELECT auth_version FROM users WHERE id = %s", (users["a_mechanic"],))[0]["auth_version"] == 2
    assert mechanic_client.get("/account").status_code == 200  # this session kept
    assert login(app.test_client(), "mechanic@a.example.com", PASSWORD).headers["Location"] == "/login"
    assert login(app.test_client(), "mechanic@a.example.com", "new-air-filter").headers["Location"] == "/"
    # Only changes the logged-in user's own password
    assert login(app.test_client(), "owner@a.example.com", PASSWORD).headers["Location"] == "/"
