"""Ownership transfer (owner only, see permissions.py).

The owner picks another ACTIVE user in the company and confirms with their own
current password. In one transaction the old owner becomes a manager and the
chosen user becomes the owner — demote first, so the one-owner-per-company
unique index holds at every step. Both get an email. Roles are re-read from the
database on every request, so the change takes effect immediately for both.
"""
from flask import Blueprint, abort, flash, g, redirect, render_template, request
from werkzeug.security import check_password_hash

from .db import get_db
from .email import send_email

bp = Blueprint("ownership", __name__)


def _candidates():
    """Who the company can be handed to: every other active user."""
    return get_db().execute(
        """
        SELECT id, full_name, email, role FROM users
        WHERE company_id = %s AND is_active AND id <> %s
        ORDER BY full_name, id
        """,
        (g.user["company_id"], g.user["id"]),
    ).fetchall()


@bp.route("/transfer-ownership", methods=["GET", "POST"])
def transfer_ownership():
    if request.method == "GET":
        return render_template("transfer_ownership.html", candidates=_candidates())

    db = get_db()
    company_id = g.user["company_id"]
    try:
        target_id = int(request.form.get("user_id") or "")
    except ValueError:
        flash("Choose who should become the owner.")
        return redirect("/transfer-ownership")

    target = db.execute(
        "SELECT id, full_name, email, is_active FROM users WHERE id = %s AND company_id = %s",
        (target_id, company_id),
    ).fetchone()
    if target is None:
        abort(404)  # not in this company (or doesn't exist): don't reveal which
    if target["id"] == g.user["id"]:
        flash("You're already the owner. Choose someone else.")
        return redirect("/transfer-ownership")
    if not target["is_active"]:
        flash(f"{target['full_name']} is deactivated. Reactivate them first, or choose an active user.")
        return redirect("/transfer-ownership")

    password_hash = db.execute(
        "SELECT password_hash FROM users WHERE id = %s AND company_id = %s", (g.user["id"], company_id)
    ).fetchone()["password_hash"]
    if not check_password_hash(password_hash, request.form.get("password") or ""):
        flash("Your password is incorrect. Ownership was not transferred.")
        return redirect("/transfer-ownership")

    # One transaction: demote, then promote. Each UPDATE re-checks its condition, so
    # a concurrent change (another transfer, a deactivation) makes it a no-op.
    demoted = db.execute(
        "UPDATE users SET role = 'manager' WHERE id = %s AND company_id = %s AND role = 'owner' RETURNING id",
        (g.user["id"], company_id),
    ).fetchone()
    promoted = db.execute(
        "UPDATE users SET role = 'owner' WHERE id = %s AND company_id = %s AND is_active RETURNING id",
        (target["id"], company_id),
    ).fetchone()
    if demoted is None or promoted is None:
        db.rollback()
        flash("Ownership was not transferred: something changed meanwhile. Reload and try again.")
        return redirect("/transfer-ownership")
    db.commit()

    company = g.user["company_name"]
    send_email(
        g.user["email"],
        f"You transferred ownership of {company} on Gladhand",
        f"You transferred ownership of {company} on Gladhand to {target['full_name']} ({target['email']}).\n\n"
        "Your account is now a manager account. Only the new owner can transfer ownership again.\n\n"
        "If you didn't do this, contact the new owner right away.\n",
    )
    send_email(
        target["email"],
        f"You're now the owner of {company} on Gladhand",
        f"{g.user['full_name']} transferred ownership of {company} on Gladhand to you.\n\n"
        "Your account now has full permissions, including managing users and deleting data. "
        f"{g.user['full_name']} is now a manager.\n",
    )

    flash(f"{target['full_name']} is now the owner. Your account is now a manager.")
    return redirect("/users")
