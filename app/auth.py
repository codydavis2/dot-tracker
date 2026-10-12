import psycopg
from flask import Blueprint, flash, g, redirect, render_template, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db

bp = Blueprint("auth", __name__)


def normalize_email(value):
    """Emails are stored and compared lowercased and trimmed."""
    return (value or "").strip().lower()


@bp.before_app_request
def load_logged_in_user():
    """Load the logged-in user into g.user on every request.

    The session holds only user_id. Everything else — including company_id, which
    scopes every query — comes from this fresh database row, never from the session,
    a form, or the URL. A user who no longer exists or has been deactivated is logged
    out immediately (login_required then sends them to /login).
    """
    g.user = None
    if request.endpoint == "static":
        return
    user_id = session.get("user_id")
    if user_id is None:
        return

    user = get_db().execute(
        """
        SELECT users.id, users.company_id, users.email, users.full_name, users.role, users.is_active,
               companies.name AS company_name
        FROM users
        JOIN companies ON companies.id = users.company_id
        WHERE users.id = %s
        """,
        (user_id,),
    ).fetchone()
    if user is None or not user["is_active"]:
        session.clear()
        return
    g.user = user


@bp.route("/register", methods=["GET", "POST"])
def register():
    """Signup: creates a company and its owner account together."""
    if request.method == "POST":
        company_name = (request.form.get("company_name") or "").strip()
        full_name = (request.form.get("full_name") or "").strip()
        email = normalize_email(request.form.get("email"))
        password = request.form.get("password")
        confirmation = request.form.get("confirmation")

        if not company_name or not full_name or not email or not password:
            flash("Company name, your name, email, and password are all required.")
            return redirect("/register")
        if "@" not in email:
            flash("Enter a valid email address.")
            return redirect("/register")
        if password != confirmation:
            flash("Passwords do not match.")
            return redirect("/register")

        db = get_db()
        try:
            company = db.execute(
                "INSERT INTO companies (name) VALUES (%s) RETURNING id", (company_name,)
            ).fetchone()
            db.execute(
                """
                INSERT INTO users (company_id, email, password_hash, full_name, role)
                VALUES (%s, %s, %s, %s, 'owner')
                """,
                (company["id"], email, generate_password_hash(password), full_name),
            )
        except psycopg.errors.UniqueViolation:
            db.rollback()
            flash("An account with that email already exists.")
            return redirect("/register")
        db.commit()

        return redirect("/login")

    return render_template("register.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    session.clear()
    g.user = None

    if request.method == "POST":
        email = normalize_email(request.form.get("email"))
        password = request.form.get("password") or ""

        db = get_db()
        user = db.execute(
            "SELECT id, password_hash, is_active FROM users WHERE email = %s", (email,)
        ).fetchone()

        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Invalid email or password.")
            return redirect("/login")
        if not user["is_active"]:
            flash("This account has been deactivated. Contact your company's account owner.")
            return redirect("/login")

        db.execute("UPDATE users SET last_login_at = now() WHERE id = %s", (user["id"],))
        db.commit()

        session["user_id"] = user["id"]
        return redirect("/")

    return render_template("login.html")


@bp.route("/logout")
def logout():
    session.clear()
    return redirect("/login")
