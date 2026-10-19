"""My Account: every logged-in user can change their own name and password."""
from flask import Blueprint, current_app, flash, g, redirect, render_template, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db
from .passwords import password_error

bp = Blueprint("account", __name__)


@bp.route("/account")
def index():
    return render_template("account.html")


@bp.route("/account/name", methods=["POST"])
def change_name():
    full_name = (request.form.get("full_name") or "").strip()
    if not full_name:
        flash("Full name can't be blank.")
        return redirect("/account")

    db = get_db()
    db.execute(
        "UPDATE users SET full_name = %s WHERE id = %s AND company_id = %s",
        (full_name, g.user["id"], g.user["company_id"]),
    )
    db.commit()
    flash("Your name has been updated.")
    return redirect("/account")


@bp.route("/account/password", methods=["POST"])
def change_password():
    """Requires the current password. Logs out every OTHER session (auth_version bump)
    but keeps this one logged in."""
    current_password = request.form.get("current_password") or ""
    password = request.form.get("password") or ""
    confirmation = request.form.get("confirmation")

    db = get_db()
    row = db.execute(
        "SELECT password_hash FROM users WHERE id = %s AND company_id = %s",
        (g.user["id"], g.user["company_id"]),
    ).fetchone()
    if not check_password_hash(row["password_hash"], current_password):
        flash("Current password is incorrect.")
        return redirect("/account")
    error = password_error(password, g.user["email"])
    if error is None and password != confirmation:
        error = "Passwords do not match."
    if error:
        flash(error)
        return redirect("/account")

    user = db.execute(
        """
        UPDATE users SET password_hash = %s, auth_version = auth_version + 1
        WHERE id = %s AND company_id = %s
        RETURNING auth_version
        """,
        (generate_password_hash(password), g.user["id"], g.user["company_id"]),
    ).fetchone()
    db.commit()

    # Keep this session: store the new auth_version, and give it a fresh id too.
    session["auth_version"] = user["auth_version"]
    current_app.session_interface.regenerate(session)
    flash("Your password has been changed. Other devices have been logged out.")
    return redirect("/account")
