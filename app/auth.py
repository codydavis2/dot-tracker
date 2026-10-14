from datetime import timedelta

import psycopg
from flask import Blueprint, current_app, flash, g, redirect, render_template, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db
from .email import absolute_url, send_email, show_dev_link
from .extensions import limiter
from .passwords import password_error
from .tokens import hash_token, new_token

bp = Blueprint("auth", __name__)

RESET_TOKEN_LIFETIME = timedelta(hours=1)


def normalize_email(value):
    """Emails are stored and compared lowercased and trimmed."""
    return (value or "").strip().lower()


def start_session(user):
    """Log a user in: a brand-new session (new id, so a session id planted before
    login is useless) holding only user_id and the user's current auth_version."""
    session.clear()
    session["user_id"] = user["id"]
    session["auth_version"] = user["auth_version"]
    current_app.session_interface.regenerate(session)


@bp.before_app_request
def load_logged_in_user():
    """Load the logged-in user into g.user on every request.

    The session holds only user_id and auth_version. Everything else — including company_id, which
    scopes every query — comes from this fresh database row, never from the session,
    a form, or the URL. A user who no longer exists or has been deactivated is logged
    out immediately (login_required then sends them to /login). So is a session whose
    auth_version is stale: password changes, resets and deactivation bump the user's
    auth_version, which logs out every session started before it.
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
               users.auth_version, companies.name AS company_name
        FROM users
        JOIN companies ON companies.id = users.company_id
        WHERE users.id = %s
        """,
        (user_id,),
    ).fetchone()
    if user is None or not user["is_active"] or session.get("auth_version") != user["auth_version"]:
        session.clear()
        return
    g.user = user


@bp.route("/register", methods=["GET", "POST"])
@limiter.limit("5/minute", methods=["POST"])
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
        error = password_error(password, email)
        if error:
            flash(error)
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
@limiter.limit("10/minute;50/hour", methods=["POST"])
def login():
    if request.method == "POST":
        email = normalize_email(request.form.get("email"))
        password = request.form.get("password") or ""

        db = get_db()
        user = db.execute(
            "SELECT id, password_hash, is_active, auth_version FROM users WHERE email = %s", (email,)
        ).fetchone()

        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Invalid email or password.")
            return redirect("/login")
        if not user["is_active"]:
            flash("This account has been deactivated. Contact your company's account owner.")
            return redirect("/login")

        db.execute("UPDATE users SET last_login_at = now() WHERE id = %s", (user["id"],))
        db.commit()

        start_session(user)
        return redirect("/")

    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect("/login")


@bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5/minute", methods=["POST"])
def forgot_password():
    """Ask for a password reset link.

    The page says the same thing whether or not the email has an account, so it
    can't be used to find out who has one. Deactivated users get no email.
    """
    if request.method == "GET":
        return render_template("forgot_password.html", sent=False)

    email = normalize_email(request.form.get("email"))
    db = get_db()
    user = db.execute(
        "SELECT id, company_id, email FROM users WHERE email = %s AND is_active", (email,)
    ).fetchone()
    if user is not None:
        # A new link replaces any older unused ones: expire them now.
        db.execute(
            "UPDATE password_resets SET expires_at = now() "
            "WHERE user_id = %s AND used_at IS NULL AND expires_at > now()",
            (user["id"],),
        )
        token, token_hash = new_token()
        db.execute(
            """
            INSERT INTO password_resets (company_id, user_id, token_hash, expires_at)
            VALUES (%s, %s, %s, now() + %s)
            """,
            (user["company_id"], user["id"], token_hash, RESET_TOKEN_LIFETIME),
        )
        db.commit()

        link = absolute_url(f"/reset-password/{token}")
        send_email(
            user["email"],
            "Reset your Gladhand password",
            "Someone (hopefully you) asked to reset the password for your Gladhand account.\n\n"
            f"Choose a new password here (the link works once, for 1 hour):\n{link}\n\n"
            "If you didn't ask for this, ignore this email. Your password won't change.\n",
        )
        show_dev_link(link)

    return render_template("forgot_password.html", sent=True)


def _find_valid_reset(token):
    """The unused, unexpired reset row for this raw token (with its active user), or None."""
    return get_db().execute(
        """
        SELECT password_resets.id, users.id AS user_id, users.email
        FROM password_resets
        JOIN users ON users.id = password_resets.user_id AND users.company_id = password_resets.company_id
        WHERE password_resets.token_hash = %s
          AND password_resets.used_at IS NULL
          AND password_resets.expires_at > now()
          AND users.is_active
        """,
        (hash_token(token),),
    ).fetchone()


INVALID_RESET_MESSAGE = (
    "This password reset link is invalid or has expired. Links work once, for 1 hour, "
    "and only the most recent link you requested works."
)


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    """Set a new password from an emailed reset link. The link is single use."""
    reset = _find_valid_reset(token)
    if reset is None:
        return render_template(
            "error.html", title="Link no longer valid", message=INVALID_RESET_MESSAGE, reset_link=True
        ), 404

    if request.method == "POST":
        password = request.form.get("password") or ""
        confirmation = request.form.get("confirmation")
        error = password_error(password, reset["email"])
        if error is None and password != confirmation:
            error = "Passwords do not match."
        if error:
            flash(error)
            return redirect(f"/reset-password/{token}")

        db = get_db()
        # Claim the token atomically, so two submissions can't both use it.
        claimed = db.execute(
            "UPDATE password_resets SET used_at = now() WHERE id = %s AND used_at IS NULL RETURNING id",
            (reset["id"],),
        ).fetchone()
        if claimed is None:
            db.rollback()
            return render_template(
                "error.html", title="Link no longer valid", message=INVALID_RESET_MESSAGE, reset_link=True
            ), 404
        # New password + bumped auth_version: every existing session is logged out.
        db.execute(
            "UPDATE users SET password_hash = %s, auth_version = auth_version + 1 WHERE id = %s",
            (generate_password_hash(password), reset["user_id"]),
        )
        db.commit()

        session.clear()
        flash("Your password has been changed. Log in with your new password.")
        return redirect("/login")

    return render_template("reset_password.html", token=token, email=reset["email"])
