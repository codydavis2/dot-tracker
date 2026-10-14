"""Invites: owner sends / resends / revokes; the emailed link creates the account."""
import hashlib

from conftest import email_link, flashes, link_path, login

PASSWORD_NEW = "grease-gun-2026"


def invite(client, email="newhire@example.com", role="mechanic"):
    return client.post("/users/invites", data={"email": email, "role": role})


def accept(client, path, full_name="New Hire", password=PASSWORD_NEW, **extra):
    data = {"full_name": full_name, "password": password, "confirmation": password}
    data.update(extra)
    return client.post(path, data=data)


def test_owner_invites(client, users, outbox, query):
    resp = invite(client, email="  NewHire@Example.com ", role="driver")
    assert resp.headers["Location"] == "/users"
    assert flashes(client) == ["Invite sent to newhire@example.com."]

    row = query("SELECT * FROM invites")[0]
    assert row["company_id"] == users["a_company"]
    assert (row["email"], row["role"]) == ("newhire@example.com", "driver")
    assert row["invited_by_user_id"] == users["a_owner"]
    assert row["accepted_at"] is None and row["revoked_at"] is None
    assert 7 * 86400 - 60 < (row["expires_at"] - row["created_at"]).total_seconds() <= 7 * 86400

    assert len(outbox) == 1
    assert outbox[0]["to"] == "newhire@example.com"
    assert "Company A" in outbox[0]["subject"]
    assert "Owner A invited you" in outbox[0]["body_text"]
    assert email_link(outbox[0]).startswith("http://localhost/invite/")


def test_db_stores_only_the_token_hash(client, users, outbox, query):
    invite(client)
    token = email_link(outbox[0]).rsplit("/", 1)[1]
    row = query("SELECT * FROM invites")[0]
    assert row["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert all(token not in str(value) for value in row.values())


def test_invite_rejects_bad_role_and_email(client, users, outbox, query):
    for role in ("owner", "admin", ""):
        invite(client, role=role)
        assert flashes(client) == ["Choose a role: manager, mechanic, or driver."]
    invite(client, email="not-an-email")
    assert flashes(client) == ["Enter a valid email address to invite."]
    assert query("SELECT * FROM invites") == [] and outbox == []


def test_invite_rejects_email_of_any_existing_user(client, users, outbox, query):
    for email in ("mechanic@a.example.com", "OWNER@b.example.com"):  # same company, other company
        invite(client, email=email)
        assert flashes(client) == [
            f"{email.lower()} already has a Gladhand account. Each person can belong to only one company."]
    assert query("SELECT * FROM invites") == [] and outbox == []


def test_only_one_pending_invite_per_email(client, other_client, users, outbox, query):
    invite(client)
    flashes(client)
    invite(client, role="driver")
    assert flashes(client) == ["newhire@example.com already has a pending invite. Use Resend to send a fresh link."]
    assert len(query("SELECT * FROM invites")) == 1
    # Another company may invite the same person (whoever accepts first wins)
    invite(other_client)
    assert len(query("SELECT * FROM invites")) == 2


def test_accept_creates_user_in_right_company_with_right_role(client, anon, users, outbox, query):
    invite(client, role="manager")
    path = link_path(email_link(outbox[0]))

    page = anon.get(path)
    assert page.status_code == 200
    assert b'value="newhire@example.com" readonly' in page.data
    assert b"Company A" in page.data

    # A submitted email is ignored: the address always comes from the invite
    resp = accept(anon, path, full_name="  Pat Jones ", email="attacker@example.com")
    assert resp.headers["Location"] == "/"

    user = query("SELECT * FROM users WHERE email = 'newhire@example.com'")[0]
    assert user["company_id"] == users["a_company"]
    assert user["role"] == "manager"
    assert user["full_name"] == "Pat Jones"
    assert user["is_active"] is True
    assert query("SELECT * FROM users WHERE email = 'attacker@example.com'") == []
    assert query("SELECT accepted_at FROM invites")[0]["accepted_at"] is not None

    # Logged in as the new user
    with anon.session_transaction() as sess:
        assert sess["user_id"] == user["id"]
    assert b"Pat Jones" in anon.get("/").data
    # ...and can log in again later
    anon.post("/logout")
    assert login(anon, "newhire@example.com", PASSWORD_NEW).headers["Location"] == "/"


def test_accepted_link_rejected(client, anon, users, outbox, query):
    invite(client)
    path = link_path(email_link(outbox[0]))
    accept(anon, path)

    other = anon.application.test_client()
    resp = other.get(path)
    assert resp.status_code == 404
    assert b"Ask your company owner for a new one" in resp.data
    assert accept(other, path, full_name="Second").status_code == 404
    assert len(query("SELECT * FROM users WHERE email = 'newhire@example.com'")) == 1


def test_expired_link_rejected(client, anon, users, outbox, query):
    invite(client)
    path = link_path(email_link(outbox[0]))
    query("UPDATE invites SET expires_at = now() - interval '1 second'")
    assert anon.get(path).status_code == 404
    assert accept(anon, path).status_code == 404
    assert query("SELECT * FROM users WHERE email = 'newhire@example.com'") == []


def test_revoke(client, anon, users, outbox, query):
    invite(client)
    path = link_path(email_link(outbox[0]))
    invite_id = query("SELECT id FROM invites")[0]["id"]

    resp = client.post(f"/users/invites/{invite_id}/revoke")
    assert resp.headers["Location"] == "/users"
    assert query("SELECT revoked_at FROM invites")[0]["revoked_at"] is not None

    assert anon.get(path).status_code == 404
    assert accept(anon, path).status_code == 404
    assert query("SELECT * FROM users WHERE email = 'newhire@example.com'") == []
    # The email can be invited again now
    flashes(client)
    invite(client)
    assert flashes(client) == ["Invite sent to newhire@example.com."]


def test_resend_replaces_link_and_extends_expiry(client, anon, users, outbox, query):
    invite(client)
    old_path = link_path(email_link(outbox[0]))
    query("UPDATE invites SET expires_at = now() - interval '1 day'")  # expired: resend still works
    invite_id = query("SELECT id FROM invites")[0]["id"]

    resp = client.post(f"/users/invites/{invite_id}/resend")
    assert resp.headers["Location"] == "/users"
    assert len(outbox) == 2
    new_path = link_path(email_link(outbox[1]))
    assert new_path != old_path
    assert query("SELECT expires_at > now() + interval '6 days' AS fresh FROM invites")[0]["fresh"]

    assert anon.get(old_path).status_code == 404  # superseded
    assert anon.get(new_path).status_code == 200
    assert accept(anon, new_path).headers["Location"] == "/"


def test_cannot_resend_or_revoke_finished_invite(client, anon, users, outbox, query):
    invite(client)
    accept(anon, link_path(email_link(outbox[0])))
    invite_id = query("SELECT id FROM invites")[0]["id"]
    flashes(client)
    for action in ("resend", "revoke"):
        client.post(f"/users/invites/{invite_id}/{action}")
        assert flashes(client) == ["That invite is no longer pending."]
    assert len(outbox) == 1
    assert query("SELECT revoked_at FROM invites")[0]["revoked_at"] is None


def test_accept_rechecks_email_not_taken(client, other_client, anon, users, outbox, query):
    # Invited by both companies; accepting company B's invite first...
    invite(client)
    invite(other_client)
    path_a, path_b = (link_path(email_link(m)) for m in outbox)
    assert accept(anon, path_b).headers["Location"] == "/"

    # ...means company A's link can no longer create a second account
    second = anon.application.test_client()
    resp = second.get(path_a)
    assert resp.status_code == 409
    assert b"already a Gladhand account" in resp.data
    assert accept(second, path_a).status_code == 409
    users_with_email = query("SELECT company_id FROM users WHERE email = 'newhire@example.com'")
    assert users_with_email == [{"company_id": users["b_company"]}]


def test_accept_requires_name_and_matching_passwords(client, anon, users, outbox, query):
    invite(client)
    path = link_path(email_link(outbox[0]))
    accept(anon, path, full_name="  ")
    assert flashes(anon) == ["Enter your full name."]
    anon.post(path, data={"full_name": "X", "password": PASSWORD_NEW, "confirmation": "different-one"})
    assert flashes(anon) == ["Passwords do not match."]
    assert query("SELECT * FROM users WHERE email = 'newhire@example.com'") == []
    assert anon.get(path).status_code == 200  # still valid


def test_unknown_token(anon, users):
    assert anon.get("/invite/nope").status_code == 404
