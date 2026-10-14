import os

from flask import Flask, flash, redirect, render_template, request
from flask.helpers import get_debug_flag
from flask_session import Session
from flask_wtf.csrf import CSRFError

from . import (account, audit, auth, dashboard, db, inspections, inventory, invites, migrate, seed, users,
               vehicles, work_orders)
from .constants import ROLE_LABELS
from .config import database_url, path_setting
from .email import MAIL_BACKENDS
from .extensions import csrf, limiter
from .helpers import format_date, format_datetime, usd
from .passwords import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH


def create_app(test_config=None):
    app = Flask(__name__)

    # Jinja filters
    app.jinja_env.filters["usd"] = usd
    app.jinja_env.filters["date"] = format_date
    app.jinja_env.filters["datetime"] = format_datetime
    app.jinja_env.globals["PASSWORD_MIN_LENGTH"] = PASSWORD_MIN_LENGTH
    app.jinja_env.globals["PASSWORD_MAX_LENGTH"] = PASSWORD_MAX_LENGTH
    app.jinja_env.globals["ROLE_LABELS"] = ROLE_LABELS

    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or "dev-only-insecure-secret-key"
    app.config["DATABASE_URL"] = database_url()

    # Server-side sessions (same pattern as CS50 Finance)
    app.config["SESSION_PERMANENT"] = False
    app.config["SESSION_TYPE"] = "filesystem"
    # Session cookie: not readable by JS, not sent on cross-site POSTs, and HTTPS-only
    # unless running in local debug mode (FLASK_DEBUG=1).
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = not get_debug_flag()

    # CSRF tokens last as long as the login session (the default 1-hour limit would
    # reject a form left open on a shop computer for a while).
    app.config["WTF_CSRF_TIME_LIMIT"] = None

    # Rate limiting (see extensions.py). Always set ENABLED explicitly: the limiter
    # object is shared, so otherwise one app's setting would leak into the next.
    app.config["RATELIMIT_ENABLED"] = True
    app.config["RATELIMIT_STORAGE_URI"] = os.environ.get("RATELIMIT_STORAGE_URI") or "memory://"

    # Work order attachments (invoices, quotes, photos) — stored outside static/ so
    # they're only reachable through the login-gated download route, not served directly.
    app.config["UPLOAD_DIR"] = path_setting("UPLOAD_DIR", os.path.join("uploads", "work_orders"))
    app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB per request

    # Email (see email.py): "console" prints messages to the terminal; "ses" is not built yet.
    app.config["MAIL_BACKEND"] = os.environ.get("MAIL_BACKEND") or "console"
    app.config["MAIL_FROM"] = os.environ.get("MAIL_FROM") or "Gladhand <no-reply@localhost>"

    if test_config:
        app.config.update(test_config)

    if app.config["MAIL_BACKEND"] not in MAIL_BACKENDS:
        raise RuntimeError(f"Unknown MAIL_BACKEND {app.config['MAIL_BACKEND']!r}; expected one of {MAIL_BACKENDS}")

    Session(app)
    csrf.init_app(app)
    limiter.init_app(app)
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)
    db.init_app(app)
    migrate.init_app(app)
    seed.init_app(app)

    @app.after_request
    def after_request(response):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response

    @app.errorhandler(413)
    def file_too_large(e):
        flash("That upload is too large. Max total upload size is 20 MB.")
        return redirect(request.referrer or "/work-orders")

    @app.errorhandler(403)
    def forbidden(e):
        return render_template(
            "error.html",
            title="Not allowed",
            message="Your account doesn't have permission to do that. Ask your company's account owner.",
        ), 403

    @app.errorhandler(CSRFError)
    def csrf_error(e):
        return render_template(
            "error.html",
            title="Form expired",
            message="This form expired or was submitted from somewhere else. "
                    "Go back, reload the page, and try again.",
        ), 400

    @app.errorhandler(429)
    def too_many_requests(e):
        return render_template(
            "error.html",
            title="Too many attempts",
            message="Too many attempts from your network. Wait a minute and try again.",
        ), 429

    for module in (auth, dashboard, vehicles, inspections, work_orders, inventory, audit,
                   users, invites, account):
        app.register_blueprint(module.bp)

    return app
