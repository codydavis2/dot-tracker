import os

from flask import Flask, flash, redirect, request
from flask_session import Session

from . import audit, auth, dashboard, db, inspections, inventory, migrate, vehicles, work_orders
from .config import database_url, path_setting
from .helpers import format_date, format_datetime, usd


def create_app(test_config=None):
    app = Flask(__name__)

    # Jinja filters
    app.jinja_env.filters["usd"] = usd
    app.jinja_env.filters["date"] = format_date
    app.jinja_env.filters["datetime"] = format_datetime

    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or "dev-only-insecure-secret-key"
    app.config["DATABASE_URL"] = database_url()

    # Server-side sessions (same pattern as CS50 Finance)
    app.config["SESSION_PERMANENT"] = False
    app.config["SESSION_TYPE"] = "filesystem"

    # Work order attachments (invoices, quotes, photos) — stored outside static/ so
    # they're only reachable through the login-gated download route, not served directly.
    app.config["UPLOAD_DIR"] = path_setting("UPLOAD_DIR", os.path.join("uploads", "work_orders"))
    app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB per request

    if test_config:
        app.config.update(test_config)

    Session(app)
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)
    db.init_app(app)
    migrate.init_app(app)

    @app.after_request
    def after_request(response):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response

    @app.errorhandler(413)
    def file_too_large(e):
        flash("That upload is too large. Max total upload size is 20 MB.")
        return redirect(request.referrer or "/work-orders")

    for module in (auth, dashboard, vehicles, inspections, work_orders, inventory, audit):
        app.register_blueprint(module.bp)

    return app
