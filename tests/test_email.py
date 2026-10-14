"""Email plumbing: console backend, dev-only on-screen link, links from the request URL."""
import pytest

from app.email import send_email


def test_console_backend_prints_full_message(app, capsys):
    with app.test_request_context():
        send_email("someone@example.com", "Hello there", "Line one\nhttp://localhost/x\n")
    out = capsys.readouterr().out
    assert "To: someone@example.com" in out
    assert "Subject: Hello there" in out
    assert "Line one\nhttp://localhost/x" in out


def test_console_mode_shows_link_on_screen(anon, users, capsys):
    resp = anon.post("/forgot-password", data={"email": "mechanic@a.example.com"})
    assert b"Dev mode:" in resp.data
    assert b'href="http://localhost/reset-password/' in resp.data
    assert "/reset-password/" in capsys.readouterr().out


def test_other_modes_never_show_link_on_screen(app, anon, users, outbox):
    app.config["MAIL_BACKEND"] = "ses"
    resp = anon.post("/forgot-password", data={"email": "mechanic@a.example.com"})
    assert len(outbox) == 1
    assert b"Dev mode:" not in resp.data
    assert b"/reset-password/" not in resp.data


def test_links_use_request_root_url(app, users, outbox):
    client = app.test_client()
    client.post("/forgot-password", data={"email": "mechanic@a.example.com"},
                base_url="https://fleet.example.com")
    assert "https://fleet.example.com/reset-password/" in outbox[0]["body_text"]


def test_ses_backend_not_implemented_yet(app):
    app.config["MAIL_BACKEND"] = "ses"
    with app.test_request_context(), pytest.raises(NotImplementedError):
        send_email("someone@example.com", "Hi", "Body")


def test_unknown_backend_refused_at_startup(make_app):
    with pytest.raises(RuntimeError, match="MAIL_BACKEND"):
        make_app(MAIL_BACKEND="smtp")
