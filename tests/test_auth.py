import pytest

from conftest import flashes, login, make_company, make_user


def signup(client, email="Dana@Example.com", **overrides):
    data = {"company_name": "Acme Hauling", "full_name": "Dana Smith", "email": email,
            "password": "brake-chamber-9", "confirmation": "brake-chamber-9"}
    data.update(overrides)
    return client.post("/register", data=data)


def test_signup_page_warns_about_owner_account(anon):
    resp = anon.get("/register")
    assert resp.status_code == 200
    assert b"not recommended for day-to-day use" in resp.data
    assert b"including deleting data" in resp.data


def test_signup_creates_company_and_owner(anon, query):
    resp = signup(anon, email="  Dana@Example.COM ")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/login"

    companies = query("SELECT * FROM companies")
    assert [(c["name"], c["company_type"]) for c in companies] == [("Acme Hauling", "fleet")]
    users = query("SELECT * FROM users")
    assert len(users) == 1
    owner = users[0]
    assert owner["company_id"] == companies[0]["id"]
    assert owner["email"] == "dana@example.com"  # stored lowercased and trimmed
    assert owner["full_name"] == "Dana Smith"
    assert owner["role"] == "owner"
    assert owner["is_active"] is True
    assert owner["password_hash"] != "brake-chamber-9"


@pytest.mark.parametrize("missing", ["company_name", "full_name", "email", "password"])
def test_signup_requires_all_fields(anon, query, missing):
    resp = signup(anon, **{missing: ""})
    assert resp.headers["Location"] == "/register"
    assert flashes(anon) == ["Company name, your name, email, and password are all required."]
    assert query("SELECT * FROM companies") == []


def test_signup_rejects_mismatched_passwords(anon, query):
    resp = signup(anon, confirmation="different")
    assert resp.headers["Location"] == "/register"
    assert flashes(anon) == ["Passwords do not match."]
    assert query("SELECT * FROM users") == []


def test_signup_rejects_duplicate_email_case_insensitively(anon, query):
    signup(anon, email="dana@example.com")
    resp = signup(anon, email="DANA@example.com", company_name="Other Co")
    assert resp.headers["Location"] == "/register"
    assert flashes(anon) == ["An account with that email already exists."]
    # The whole signup rolled back: no orphan company
    assert [c["name"] for c in query("SELECT name FROM companies")] == ["Acme Hauling"]


def test_login_is_case_insensitive_and_records_last_login(anon, query):
    signup(anon, email="dana@example.com")
    assert query("SELECT last_login_at FROM users")[0]["last_login_at"] is None

    resp = login(anon, "  DANA@Example.com", "brake-chamber-9")
    assert resp.headers["Location"] == "/"
    assert query("SELECT last_login_at FROM users")[0]["last_login_at"] is not None

    dashboard = anon.get("/")
    assert dashboard.status_code == 200
    assert b"Dana Smith" in dashboard.data and b"Acme Hauling" in dashboard.data


def test_session_stores_only_user_id_and_auth_version(client, users):
    with client.session_transaction() as sess:
        assert {k for k in sess.keys() if not k.startswith("_")} - {"csrf_token"} == {"user_id", "auth_version"}
        assert sess["user_id"] == users["a_owner"]


def test_login_wrong_password(anon, users):
    resp = login(anon, "owner@a.example.com", "wrong")
    assert resp.headers["Location"] == "/login"
    assert flashes(anon) == ["Invalid email or password."]
    assert anon.get("/").headers["Location"] == "/login"


def test_login_unknown_email(anon, users):
    resp = login(anon, "nobody@example.com", "whatever")
    assert resp.headers["Location"] == "/login"
    assert flashes(anon) == ["Invalid email or password."]


def test_deactivated_user_cannot_log_in(anon, query):
    company_id = make_company(query, "Acme")
    make_user(query, company_id, "gone@example.com", "Gone Guy", is_active=False)
    resp = login(anon, "gone@example.com")
    assert resp.headers["Location"] == "/login"
    assert flashes(anon) == ["This account has been deactivated. Contact your company's account owner."]
    assert anon.get("/").headers["Location"] == "/login"


def test_deactivated_user_is_logged_out_mid_session(mechanic_client, query, users):
    assert mechanic_client.get("/vehicles").status_code == 200

    query("UPDATE users SET is_active = false WHERE id = %s", (users["a_mechanic"],))

    resp = mechanic_client.get("/vehicles")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/login"
    with mechanic_client.session_transaction() as sess:
        assert "user_id" not in sess
    # Reactivating doesn't bring the old session back
    query("UPDATE users SET is_active = true WHERE id = %s", (users["a_mechanic"],))
    assert mechanic_client.get("/vehicles").headers["Location"] == "/login"


def test_deleted_user_row_is_logged_out(client, query, users):
    # Users are never deleted in normal operation, but a stale session must not survive it
    with client.session_transaction() as sess:
        sess["user_id"] = 999999
    assert client.get("/").headers["Location"] == "/login"


def test_logout_clears_session(client):
    assert client.get("/").status_code == 200
    assert client.get("/logout").status_code == 405  # POST only
    assert client.get("/").status_code == 200
    resp = client.post("/logout")
    assert resp.headers["Location"] == "/login"
    assert client.get("/").headers["Location"] == "/login"


PROTECTED_GET = [
    "/",
    "/vehicles",
    "/vehicles/1",
    "/inspections",
    "/inspections/reports/1",
    "/work-orders",
    "/work-orders/1",
    "/work-orders/1/attachments/1",
    "/inventory",
    "/audit",
]

PROTECTED_POST = [
    "/vehicles",
    "/vehicles/1/maintenance",
    "/vehicles/1/dtc",
    "/vehicles/1/reminders",
    "/inspections/reports",
    "/reminders/1/complete",
    "/work-orders",
    "/work-orders/1/edit",
    "/work-orders/1/close",
    "/work-orders/1/delete",
    "/work-orders/1/attachments/1/delete",
    "/inventory",
    "/inventory/1/edit",
]


@pytest.mark.parametrize("path", PROTECTED_GET)
def test_protected_get_redirects_to_login(anon, path):
    resp = anon.get(path)
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/login"


@pytest.mark.parametrize("path", PROTECTED_POST)
def test_protected_post_redirects_to_login(anon, path):
    resp = anon.post(path, data={})
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/login"
