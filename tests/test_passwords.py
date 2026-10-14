"""Password policy: one validator, applied everywhere a password is set."""
import pytest

from app.passwords import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, password_error
from conftest import PASSWORD, email_link, flashes, link_path, logged_in_client, make_company, make_user

EXACTLY_8 = "kq7#x2pz"


def test_limits():
    assert PASSWORD_MIN_LENGTH == 8
    assert PASSWORD_MAX_LENGTH == 128


@pytest.mark.parametrize("password, email, ok", [
    ("x7#kq2p", "a@b.com", False),                  # 7 characters
    (EXACTLY_8, "a@b.com", True),                   # exactly 8
    ("a" * 128, "a@b.com", True),
    ("a" * 129, "a@b.com", False),
    ("password123", "a@b.com", False),              # common
    ("PassWord123", "a@b.com", False),              # common, any case
    ("12345678", "a@b.com", False),
    ("dana@example.com", "dana@example.com", False),  # same as email
    ("Dana@Example.com", "dana@example.com", False),
    ("all lowercase no digits", "a@b.com", True),   # no composition rules
    ("", "a@b.com", False),
    (None, "a@b.com", False),
])
def test_password_error(password, email, ok):
    assert (password_error(password, email) is None) is ok


# ---------- every place a password is set ----------
# Each flow sets up what it needs and returns (email, submit), where submit(password)
# tries to set that password and returns True if it was accepted.

def signup_flow(app, users, query, outbox):
    email = "dana@newco.example.com"
    client = app.test_client()

    def submit(password):
        client.post("/register", data={"company_name": "New Co", "full_name": "Dana", "email": email,
                                       "password": password, "confirmation": password})
        return bool(query("SELECT 1 FROM users WHERE email = %s", (email,)))
    return email, submit


def reset_flow(app, users, query, outbox):
    email = "mechanic@a.example.com"
    client = app.test_client()
    client.post("/forgot-password", data={"email": email})
    path = link_path(email_link(outbox[-1]))
    before = query("SELECT password_hash FROM users WHERE email = %s", (email,))[0]["password_hash"]

    def submit(password):
        resp = client.post(path, data={"password": password, "confirmation": password})
        after = query("SELECT password_hash FROM users WHERE email = %s", (email,))[0]["password_hash"]
        return resp.headers.get("Location") == "/login" and after != before
    return email, submit


def invite_flow(app, users, query, outbox):
    email = "newhire@a.example.com"
    owner = logged_in_client(app, "owner@a.example.com")
    owner.post("/users/invites", data={"email": email, "role": "mechanic"})
    path = link_path(email_link(outbox[-1]))
    client = app.test_client()

    def submit(password):
        client.post(path, data={"full_name": "New Hire", "password": password, "confirmation": password})
        return bool(query("SELECT 1 FROM users WHERE email = %s", (email,)))
    return email, submit


def change_password_flow(app, users, query, outbox):
    email = "mechanic@a.example.com"
    client = logged_in_client(app, email)

    def submit(password):
        client.post("/account/password", data={"current_password": PASSWORD, "password": password,
                                               "confirmation": password})
        return "Your password has been changed. Other devices have been logged out." in flashes(client)
    return email, submit


FLOWS = [signup_flow, reset_flow, invite_flow, change_password_flow]
BAD_PASSWORDS = {
    "too_short": lambda email: "x7#kq2p",
    "too_long": lambda email: "a" * 129,
    "common": lambda email: "password123",
    "equals_email": lambda email: email.upper(),
}


@pytest.mark.parametrize("flow", FLOWS, ids=lambda f: f.__name__)
@pytest.mark.parametrize("bad", BAD_PASSWORDS, ids=str)
def test_bad_passwords_rejected_everywhere(app, users, query, outbox, flow, bad):
    email, submit = flow(app, users, query, outbox)
    assert submit(BAD_PASSWORDS[bad](email)) is False


@pytest.mark.parametrize("flow", FLOWS, ids=lambda f: f.__name__)
def test_exactly_8_characters_accepted_everywhere(app, users, query, outbox, flow):
    email, submit = flow(app, users, query, outbox)
    assert submit(EXACTLY_8) is True


def test_existing_short_password_still_logs_in(anon, query):
    # The policy applies when a password is set, not at login
    make_user(query, make_company(query, "Old Co"), "old@example.com", "Old Timer")
    assert len(PASSWORD) < PASSWORD_MIN_LENGTH
    assert anon.post("/login", data={"email": "old@example.com", "password": PASSWORD}).headers["Location"] == "/"
