import os

from dotenv import load_dotenv
from flask import Flask, flash, redirect, request
from flask_session import Session

from . import audit, auth, dashboard, inspections, inventory, vehicles, work_orders
from .helpers import usd

# Project root (the folder above this package). Default paths are anchored here so they
# don't depend on the directory the app happens to be started from.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Load settings from a local .env file if there is one. Real environment variables win.
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))


def _path_setting(name, default):
    """Read a filesystem path from the environment; relative paths are resolved
    against the project root, not the current working directory."""
    return os.path.join(PROJECT_ROOT, os.environ.get(name) or default)


def create_app(test_config=None):
    app = Flask(__name__)

    # Jinja filters
    app.jinja_env.filters["usd"] = usd

    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or "dev-only-insecure-secret-key"
    app.config["DATABASE"] = _path_setting("DATABASE", "dot_tracker.db")

    # Server-side sessions (same pattern as CS50 Finance)
    app.config["SESSION_PERMANENT"] = False
    app.config["SESSION_TYPE"] = "filesystem"

    # Work order attachments (invoices, quotes, photos) — stored outside static/ so
    # they're only reachable through the login-gated download route, not served directly.
    app.config["UPLOAD_DIR"] = _path_setting("UPLOAD_DIR", os.path.join("uploads", "work_orders"))
    app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB per request

    if test_config:
        app.config.update(test_config)

    Session(app)
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)

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
