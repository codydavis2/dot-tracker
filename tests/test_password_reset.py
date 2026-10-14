"""Password reset: no account enumeration, hashed single-use tokens, expiry, superseding."""
import hashlib

from conftest import PASSWORD, email_link, flashes, link_path, login

NEW_PASSWORD = "torque-wrench-77"


def request_reset(client, email):
    return client.post("/forgot-password", data={"email": email})


def test_login_page_links_to_forgot_password(anon):
    assert b'href="/forgot-password"' in anon.get("/login").data


def test_same_response_for_real_and_unknown_email(app, anon, users, outbox):
    # With the console backend the emailed link is also shown on screen (dev only),
    # which would differ; compare with a non-console backend, as in production.
    app.config["MAIL_BACKEND"] = "ses"
    real = request_reset(anon, "mechanic@a.example.com")
    unknown = request_reset(anon, "nobody@example.com")
    assert real.status_code == unknown.status_code == 200
    assert real.data == unknown.data
    assert b"If an account exists for that email, we've sent a reset link" in real.data
    assert [m["to"] for m in outbox] == ["mechanic@a.example.com"]


def test_reset_email_and_flow(anon, users, outbox, query):
    request_reset(anon, "  Mechanic@A.example.com ")
    assert len(outbox) == 1
    assert outbox[0]["to"] == "mechanic@a.example.com"
    link = email_link(outbox[0])
    assert link.startswith("http://localhost/reset-password/")

    assert anon.get(link_path(link)).status_code == 200
    resp = anon.post(link_path(link), data={"password": NEW_PASSWORD, "confirmation": NEW_PASSWORD})
    assert resp.headers["Location"] == "/login"

    row = query("SELECT used_at FROM password_resets")[0]
    assert row["used_at"] is not None
    assert query("SELECT auth_version FROM users WHERE id = %s", (users["a_mechanic"],))[0]["auth_version"] == 2
    assert login(anon, "mechanic@a.example.com", PASSWORD).headers["Location"] == "/login"
    assert login(anon, "mechanic@a.example.com", NEW_PASSWORD).headers["Location"] == "/"


def test_db_stores_only_the_token_hash(anon, users, outbox, query):
    request_reset(anon, "mechanic@a.example.com")
    token = email_link(outbox[0]).rsplit("/", 1)[1]
    row = query("SELECT * FROM password_resets")[0]
    assert row["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert all(token not in str(value) for value in row.values())
    assert row["user_id"] == users["a_mechanic"] and row["company_id"] == users["a_company"]
    lifetime = row["expires_at"] - row["created_at"]
    assert 59 * 60 < lifetime.total_seconds() <= 3600


def test_used_token_rejected(anon, users, outbox):
    request_reset(anon, "mechanic@a.example.com")
    path = link_path(email_link(outbox[0]))
    anon.post(path, data={"password": NEW_PASSWORD, "confirmation": NEW_PASSWORD})

    resp = anon.get(path)
    assert resp.status_code == 404
    assert b"invalid or has expired" in resp.data
    resp = anon.post(path, data={"password": "another-new-one", "confirmation": "another-new-one"})
    assert resp.status_code == 404
    assert login(anon, "mechanic@a.example.com", NEW_PASSWORD).headers["Location"] == "/"


def test_expired_token_rejected(anon, users, outbox, query):
    request_reset(anon, "mechanic@a.example.com")
    path = link_path(email_link(outbox[0]))
    query("UPDATE password_resets SET expires_at = now() - interval '1 second'")

    assert anon.get(path).status_code == 404
    resp = anon.post(path, data={"password": NEW_PASSWORD, "confirmation": NEW_PASSWORD})
    assert resp.status_code == 404
    assert login(anon, "mechanic@a.example.com", PASSWORD).headers["Location"] == "/"


def test_newer_request_supersedes_older_link(anon, users, outbox):
    request_reset(anon, "mechanic@a.example.com")
    request_reset(anon, "mechanic@a.example.com")
    old, new = (link_path(email_link(m)) for m in outbox)

    assert anon.get(old).status_code == 404
    assert anon.post(old, data={"password": NEW_PASSWORD, "confirmation": NEW_PASSWORD}).status_code == 404
    assert anon.get(new).status_code == 200


def test_unknown_token_rejected(anon, users):
    assert anon.get("/reset-password/not-a-real-token").status_code == 404


def test_deactivated_user_gets_no_email(anon, users, outbox, query):
    query("UPDATE users SET is_active = false WHERE id = %s", (users["a_mechanic"],))
    resp = request_reset(anon, "mechanic@a.example.com")
    assert resp.status_code == 200
    assert outbox == []
    assert query("SELECT * FROM password_resets") == []


def test_link_dies_if_user_deactivated_after_request(anon, users, outbox, query):
    request_reset(anon, "mechanic@a.example.com")
    query("UPDATE users SET is_active = false WHERE id = %s", (users["a_mechanic"],))
    assert anon.get(link_path(email_link(outbox[0]))).status_code == 404


def test_mismatched_confirmation(anon, users, outbox):
    request_reset(anon, "mechanic@a.example.com")
    path = link_path(email_link(outbox[0]))
    resp = anon.post(path, data={"password": NEW_PASSWORD, "confirmation": "something-else"})
    assert resp.headers["Location"] == path
    assert flashes(anon) == ["Passwords do not match."]
    assert anon.get(path).status_code == 200  # token not used up
