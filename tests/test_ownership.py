"""Ownership transfer (owner only)."""
import pytest

from conftest import PASSWORD, flashes, logged_in_client, make_user


def roles_by_id(query, company_id):
    return {r["id"]: r["role"] for r in query("SELECT id, role FROM users WHERE company_id = %s", (company_id,))}


def test_transfer_page_lists_other_active_users(client, roles, query):
    make_user(query, roles["a_company"], "gone@a.example.com", "Gone Guy", is_active=False)
    html = client.get("/transfer-ownership").get_data(as_text=True)
    assert "Manager A" in html and "Mechanic A" in html and "Driver A" in html
    assert "Gone Guy" not in html and "Owner B" not in html
    assert f'<option value="{roles["a_owner"]}"' not in html
    assert 'href="/transfer-ownership"' in client.get("/users").get_data(as_text=True)


def test_transfer_success(app, client, roles, query, outbox):
    mechanic = logged_in_client(app, "mechanic@a.example.com")
    resp = client.post("/transfer-ownership", data={"user_id": roles["a_mechanic"], "password": PASSWORD})
    assert resp.headers["Location"] == "/users"
    assert flashes(client) == ["Mechanic A is now the owner. Your account is now a manager."]

    after = roles_by_id(query, roles["a_company"])
    assert after[roles["a_owner"]] == "manager"
    assert after[roles["a_mechanic"]] == "owner"
    assert list(after.values()).count("owner") == 1
    assert roles_by_id(query, roles["b_company"])[roles["b_owner"]] == "owner"  # company B untouched

    assert sorted(m["to"] for m in outbox) == ["mechanic@a.example.com", "owner@a.example.com"]
    assert all("Company A" in m["subject"] for m in outbox)

    # Effective immediately for both, without logging in again
    assert client.get("/transfer-ownership").status_code == 403  # now a manager
    assert client.get("/users").status_code == 200
    assert mechanic.get("/transfer-ownership").status_code == 200
    # The new owner can't be demoted by the old owner (now a manager): 404
    assert client.post(f"/users/{roles['a_mechanic']}/role", data={"role": "driver"}).status_code == 404


def test_wrong_password(client, roles, query, outbox):
    before = roles_by_id(query, roles["a_company"])
    client.post("/transfer-ownership", data={"user_id": roles["a_manager"], "password": "not-my-password"})
    assert flashes(client) == ["Your password is incorrect. Ownership was not transferred."]
    assert roles_by_id(query, roles["a_company"]) == before
    assert outbox == []


def test_inactive_target(client, roles, query, outbox):
    gone = make_user(query, roles["a_company"], "gone@a.example.com", "Gone Guy", is_active=False)
    before = roles_by_id(query, roles["a_company"])
    client.post("/transfer-ownership", data={"user_id": gone, "password": PASSWORD})
    assert flashes(client) == ["Gone Guy is deactivated. Reactivate them first, or choose an active user."]
    assert roles_by_id(query, roles["a_company"]) == before
    assert outbox == []


def test_self(client, roles, query, outbox):
    before = roles_by_id(query, roles["a_company"])
    client.post("/transfer-ownership", data={"user_id": roles["a_owner"], "password": PASSWORD})
    assert flashes(client) == ["You're already the owner. Choose someone else."]
    assert roles_by_id(query, roles["a_company"]) == before
    assert outbox == []


@pytest.mark.parametrize("user_id", ["", "abc"])
def test_missing_target(client, roles, user_id):
    client.post("/transfer-ownership", data={"user_id": user_id, "password": PASSWORD})
    assert flashes(client) == ["Choose who should become the owner."]


def test_other_company_target_is_404(client, roles, query, outbox):
    before = query("SELECT id, role FROM users ORDER BY id")
    for target in (roles["b_mechanic"], roles["b_owner"], 999999):
        resp = client.post("/transfer-ownership", data={"user_id": target, "password": PASSWORD})
        assert resp.status_code == 404, target
    assert query("SELECT id, role FROM users ORDER BY id") == before
    assert outbox == []


@pytest.mark.parametrize("email", ["manager@a.example.com", "mechanic@a.example.com", "driver@a.example.com"])
def test_only_the_owner_can_transfer(app, roles, query, outbox, email):
    client = logged_in_client(app, email)
    before = query("SELECT id, role FROM users ORDER BY id")
    assert client.get("/transfer-ownership").status_code == 403
    resp = client.post("/transfer-ownership", data={"user_id": roles["a_manager"], "password": PASSWORD})
    assert resp.status_code == 403
    assert query("SELECT id, role FROM users ORDER BY id") == before
    assert 'href="/transfer-ownership"' not in client.get("/account").get_data(as_text=True)
