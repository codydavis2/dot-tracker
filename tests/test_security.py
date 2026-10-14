"""CSRF, rate limiting, session rotation / auth_version, cookie flags."""
import re
from pathlib import Path

import pytest
from flask import url_for

from conftest import PASSWORD, email_link, flashes, force_login, link_path, logged_in_client, login

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"


# ---------- CSRF ----------

def post_urls(app):
    """A concrete URL for every rule in the app that accepts POST (ids filled with 1)."""
    urls = []
    with app.test_request_context():
        for rule in app.url_map.iter_rules():
            if "POST" in rule.methods:
                urls.append(url_for(rule.endpoint, **{arg: 1 for arg in rule.arguments}))
    return sorted(urls)


@pytest.fixture
def csrf_app(make_app):
    return make_app(WTF_CSRF_ENABLED=True)


def test_post_routes_are_enumerated(csrf_app):
    urls = post_urls(csrf_app)
    # Sanity check that the enumeration finds the routes it should
    for expected in ("/login", "/logout", "/register", "/work-orders", "/users/invites", "/invite/1",
                     "/reset-password/1", "/account/password"):
        assert expected in urls
    assert len(urls) >= 25


def test_every_post_route_rejects_missing_csrf_token(csrf_app, users, query):
    client = force_login(csrf_app.test_client(), query, users["a_owner"])
    for url in post_urls(csrf_app):
        resp = client.post(url, data={})
        assert resp.status_code == 400, url
        assert b"Form expired" in resp.data, url
    # Nothing got through: still logged in, nothing created
    assert client.get("/").status_code == 200
    assert query("SELECT * FROM vehicles") == []


def test_every_post_route_rejects_missing_csrf_token_when_logged_out(csrf_app):
    anon = csrf_app.test_client()
    for url in post_urls(csrf_app):
        assert anon.post(url, data={}).status_code == 400, url


def test_wrong_csrf_token_rejected(csrf_app, users, query):
    client = force_login(csrf_app.test_client(), query, users["a_owner"])
    client.get("/vehicles")  # gets a real token into the session
    resp = client.post("/logout", data={"csrf_token": "not-the-token"})
    assert resp.status_code == 400
    assert client.get("/").status_code == 200  # still logged in


def csrf_token_from(html):
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def test_forms_work_with_csrf_token(csrf_app, users):
    client = csrf_app.test_client()
    token = csrf_token_from(client.get("/login").get_data(as_text=True))
    resp = client.post("/login", data={"email": "owner@a.example.com", "password": PASSWORD, "csrf_token": token})
    assert resp.headers["Location"] == "/"

    token = csrf_token_from(client.get("/").get_data(as_text=True))  # the navbar logout form
    resp = client.post("/logout", data={"csrf_token": token})
    assert resp.headers["Location"] == "/login"
    assert client.get("/").headers["Location"] == "/login"


def test_every_post_form_in_templates_has_csrf_token():
    forms = 0
    for path in TEMPLATES.glob("*.html"):
        html = path.read_text()
        for match in re.finditer(r"<form\b[^>]*>", html, re.S):
            if 'method="post"' not in match.group(0):
                continue
            forms += 1
            following = html[match.end():match.end() + 200]
            assert 'name="csrf_token" value="{{ csrf_token() }}"' in following, f"{path.name}: {match.group(0)}"
    assert forms >= 20


def test_logout_is_post_only(client):
    assert client.get("/logout").status_code == 405
    assert client.get("/").status_code == 200


# ---------- rate limiting ----------

@pytest.fixture
def limited_app(make_app):
    return make_app(RATELIMIT_ENABLED=True)


def test_eleventh_login_attempt_in_a_minute_is_rejected(limited_app, users):
    anon = limited_app.test_client()
    for _ in range(10):
        assert login(anon, "owner@a.example.com", "wrong-password").status_code == 302
    resp = login(anon, "owner@a.example.com", PASSWORD)
    assert resp.status_code == 429
    assert b"Too many attempts" in resp.data
    # Only POSTs count: the login page itself still loads
    assert anon.get("/login").status_code == 200


@pytest.mark.parametrize("path", ["/register", "/forgot-password", "/invite/some-token"])
def test_sixth_post_in_a_minute_is_rejected(limited_app, path, outbox):
    anon = limited_app.test_client()
    for _ in range(5):
        assert anon.post(path, data={}).status_code != 429
    assert anon.post(path, data={}).status_code == 429


def test_rate_limit_is_off_in_general_tests(app, users):
    anon = app.test_client()
    for _ in range(12):
        assert login(anon, "owner@a.example.com", "wrong-password").status_code == 302


# ---------- sessions ----------

def test_session_id_changes_on_login(app, users):
    client = app.test_client()
    # An attacker-planted (fixated) session before login
    with client.session_transaction() as sess:
        sess["planted"] = "x"
    before = client.get_cookie("session").value

    assert login(client, "owner@a.example.com").headers["Location"] == "/"

    after = client.get_cookie("session").value
    assert after != before
    with client.session_transaction() as sess:
        assert "planted" not in sess

    # The old session id is dead, not logged in
    stale = app.test_client()
    stale.set_cookie("session", before)
    assert stale.get("/").headers["Location"] == "/login"


def test_session_cookie_flags(app, users):
    resp = login(app.test_client(), "owner@a.example.com")
    cookie = next(h for h in resp.headers.getlist("Set-Cookie") if h.startswith("session="))
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


@pytest.mark.parametrize("debug, secure", [("0", True), ("1", False)])
def test_session_cookie_secure_unless_debug(make_app, monkeypatch, debug, secure):
    monkeypatch.setenv("FLASK_DEBUG", debug)
    assert make_app().config["SESSION_COOKIE_SECURE"] is secure


def test_password_change_logs_out_other_sessions(app, users):
    laptop = logged_in_client(app, "owner@a.example.com")
    phone = logged_in_client(app, "owner@a.example.com")

    resp = laptop.post("/account/password", data={
        "current_password": PASSWORD, "password": "new-brake-lining", "confirmation": "new-brake-lining"})
    assert resp.headers["Location"] == "/account"

    assert laptop.get("/").status_code == 200  # this session stays logged in
    assert phone.get("/").headers["Location"] == "/login"  # the other one is out


def test_deactivation_logs_user_out(app, users, client, query):
    mechanic = logged_in_client(app, "mechanic@a.example.com")
    assert mechanic.get("/vehicles").status_code == 200

    client.post(f"/users/{users['a_mechanic']}/deactivate")

    assert mechanic.get("/vehicles").headers["Location"] == "/login"
    # Reactivating doesn't revive the old session (auth_version moved on)
    client.post(f"/users/{users['a_mechanic']}/reactivate")
    assert mechanic.get("/vehicles").headers["Location"] == "/login"
    assert login(app.test_client(), "mechanic@a.example.com").headers["Location"] == "/"


def test_stale_auth_version_is_logged_out(app, users, query):
    mechanic = logged_in_client(app, "mechanic@a.example.com")
    query("UPDATE users SET auth_version = auth_version + 1 WHERE id = %s", (users["a_mechanic"],))
    assert mechanic.get("/").headers["Location"] == "/login"


def test_password_reset_logs_out_existing_sessions(app, users, outbox):
    session = logged_in_client(app, "mechanic@a.example.com")
    anon = app.test_client()
    anon.post("/forgot-password", data={"email": "mechanic@a.example.com"})
    path = link_path(email_link(outbox[-1]))
    anon.post(path, data={"password": "fresh-air-filter", "confirmation": "fresh-air-filter"})
    assert flashes(anon) == ["Your password has been changed. Log in with your new password."]
    assert session.get("/").headers["Location"] == "/login"
