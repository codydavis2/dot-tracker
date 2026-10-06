import pytest

from conftest import flashes, login, register


def test_register_creates_user_with_hashed_password(anon, query):
    resp = register(anon, "alice", "s3cret")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/login"

    rows = query("SELECT username, hash FROM users")
    assert len(rows) == 1
    assert rows[0]["username"] == "alice"
    assert rows[0]["hash"] != "s3cret"


def test_register_rejects_mismatched_passwords(anon, query):
    resp = anon.post("/register", data={"username": "alice", "password": "a", "confirmation": "b"})
    assert resp.headers["Location"] == "/register"
    assert flashes(anon) == ["Passwords do not match."]
    assert query("SELECT * FROM users") == []


def test_register_rejects_missing_fields(anon, query):
    resp = anon.post("/register", data={"username": "", "password": "", "confirmation": ""})
    assert resp.headers["Location"] == "/register"
    assert flashes(anon) == ["Username and password are required."]
    assert query("SELECT * FROM users") == []


def test_register_rejects_duplicate_username(anon, query):
    register(anon, "alice")
    resp = register(anon, "alice")
    assert resp.headers["Location"] == "/register"
    assert flashes(anon) == ["Username already taken."]
    assert len(query("SELECT * FROM users")) == 1


def test_login_page_renders(anon):
    resp = anon.get("/login")
    assert resp.status_code == 200
    assert b'action="/login"' in resp.data


def test_login_success_redirects_to_dashboard(anon):
    register(anon, "alice", "s3cret")
    resp = login(anon, "alice", "s3cret")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/"

    dashboard = anon.get("/")
    assert dashboard.status_code == 200
    assert b"Log Out" in dashboard.data


def test_login_wrong_password(anon):
    register(anon, "alice", "s3cret")
    resp = login(anon, "alice", "wrong")
    assert resp.headers["Location"] == "/login"
    assert flashes(anon) == ["Invalid username or password."]
    assert anon.get("/").headers["Location"] == "/login"


def test_login_unknown_user(anon):
    resp = login(anon, "nobody", "whatever")
    assert resp.headers["Location"] == "/login"
    assert flashes(anon) == ["Invalid username or password."]


def test_logout_clears_session(client):
    assert client.get("/").status_code == 200
    resp = client.get("/logout")
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
