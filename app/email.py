"""Outgoing email.

MAIL_BACKEND picks how messages go out:
  "console" (default) — print the whole message to the terminal running the app
                        (and the app log). Nothing is actually sent.
  "ses"               — Amazon SES. NOT IMPLEMENTED YET; added at deploy time.
"""
import sys

from flask import current_app, flash, request

MAIL_BACKENDS = ("console", "ses")


def absolute_url(path):
    """Full link for an email, built from the current request's root URL
    (e.g. http://localhost:5000 + /invite/abc)."""
    return request.url_root.rstrip("/") + path


def send_email(to, subject, body_text):
    """Send a plain-text email using the configured MAIL_BACKEND."""
    _deliver({
        "from": current_app.config["MAIL_FROM"],
        "to": to,
        "subject": subject,
        "body_text": body_text,
    })


def show_dev_link(link):
    """In console mode only, also show an emailed link on screen, so it can be
    clicked without digging through the terminal. Never shown in any other mode —
    in production the link must only reach the recipient's inbox."""
    if current_app.config["MAIL_BACKEND"] == "console":
        flash(link, "dev_link")


def _deliver(message):
    backend = current_app.config["MAIL_BACKEND"]
    if backend == "console":
        _print_to_console(message)
    elif backend == "ses":
        # TODO(deploy): send through Amazon SES (boto3 sesv2 send_email), with the
        # sender address verified in SES. Not built until the app is deployed.
        raise NotImplementedError("MAIL_BACKEND=ses is not implemented yet")
    else:
        raise RuntimeError(f"Unknown MAIL_BACKEND {backend!r}; expected one of {MAIL_BACKENDS}")


def _print_to_console(message):
    text = (
        "\n===== EMAIL (MAIL_BACKEND=console: printed here, not sent) =====\n"
        f"From: {message['from']}\n"
        f"To: {message['to']}\n"
        f"Subject: {message['subject']}\n"
        "\n"
        f"{message['body_text']}\n"
        "===== END EMAIL =====\n"
    )
    print(text, file=sys.stdout, flush=True)
    current_app.logger.info("Email to %s: %s", message["to"], message["subject"])
