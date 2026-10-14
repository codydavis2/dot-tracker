"""User management (owner only): list users and pending invites, change roles,
rename, deactivate and reactivate. Users are never deleted (CLAUDE.md rule 3).

Nobody can be made owner here and the owner can't change their own role or
deactivate themselves: ownership transfer is a separate flow (M3).
"""
from flask import Blueprint, abort, flash, g, redirect, render_template, request

from .constants import ASSIGNABLE_ROLES
from .db import get_db
from .helpers import role_required

bp = Blueprint("users", __name__)


def _get_company_user(user_id):
    """A user in the logged-in owner's company, or 404."""
    user = get_db().execute(
        "SELECT id, full_name, email, role, is_active FROM users WHERE id = %s AND company_id = %s",
        (user_id, g.user["company_id"]),
    ).fetchone()
    if user is None:
        abort(404)
    return user


@bp.route("/users")
@role_required("owner")
def index():
    db = get_db()
    company_id = g.user["company_id"]
    people = db.execute(
        """
        SELECT id, full_name, email, role, is_active, created_at, last_login_at
        FROM users
        WHERE company_id = %s
        ORDER BY role = 'owner' DESC, full_name, id
        """,
        (company_id,),
    ).fetchall()
    invites = db.execute(
        """
        SELECT invites.id, invites.email, invites.role, invites.created_at, invites.expires_at,
               invites.expires_at <= now() AS is_expired, inviter.full_name AS invited_by
        FROM invites
        JOIN users AS inviter
          ON inviter.id = invites.invited_by_user_id AND inviter.company_id = invites.company_id
        WHERE invites.company_id = %s AND invites.accepted_at IS NULL AND invites.revoked_at IS NULL
        ORDER BY invites.created_at DESC
        """,
        (company_id,),
    ).fetchall()
    return render_template(
        "users.html",
        active_users=[p for p in people if p["is_active"]],
        inactive_users=[p for p in people if not p["is_active"]],
        invites=invites,
        assignable_roles=ASSIGNABLE_ROLES,
    )


@bp.route("/users/<int:user_id>/role", methods=["POST"])
@role_required("owner")
def change_role(user_id):
    user = _get_company_user(user_id)
    role = request.form.get("role")
    if user["id"] == g.user["id"] or user["role"] == "owner":
        flash("You can't change your own role. (Transferring ownership is not available yet.)")
        return redirect("/users")
    if role not in ASSIGNABLE_ROLES:
        flash("Choose a role: manager, mechanic, or driver.")
        return redirect("/users")

    db = get_db()
    db.execute(
        "UPDATE users SET role = %s WHERE id = %s AND company_id = %s",
        (role, user["id"], g.user["company_id"]),
    )
    db.commit()
    flash(f"{user['full_name']} is now a {role}.")
    return redirect("/users")


@bp.route("/users/<int:user_id>/name", methods=["POST"])
@role_required("owner")
def rename(user_id):
    user = _get_company_user(user_id)
    full_name = (request.form.get("full_name") or "").strip()
    if not full_name:
        flash("Full name can't be blank.")
        return redirect("/users")

    db = get_db()
    db.execute(
        "UPDATE users SET full_name = %s WHERE id = %s AND company_id = %s",
        (full_name, user["id"], g.user["company_id"]),
    )
    db.commit()
    flash(f"Renamed {user['full_name']} to {full_name}.")
    return redirect("/users")


@bp.route("/users/<int:user_id>/deactivate", methods=["POST"])
@role_required("owner")
def deactivate(user_id):
    """They can't log in any more and are logged out everywhere right away (auth_version
    bump). Their name stays on every record they signed."""
    user = _get_company_user(user_id)
    if user["id"] == g.user["id"] or user["role"] == "owner":
        flash("You can't deactivate your own account.")
        return redirect("/users")

    db = get_db()
    db.execute(
        "UPDATE users SET is_active = false, auth_version = auth_version + 1 WHERE id = %s AND company_id = %s",
        (user["id"], g.user["company_id"]),
    )
    db.commit()
    flash(f"{user['full_name']} has been deactivated and logged out.")
    return redirect("/users")


@bp.route("/users/<int:user_id>/reactivate", methods=["POST"])
@role_required("owner")
def reactivate(user_id):
    user = _get_company_user(user_id)
    db = get_db()
    db.execute(
        "UPDATE users SET is_active = true WHERE id = %s AND company_id = %s",
        (user["id"], g.user["company_id"]),
    )
    db.commit()
    flash(f"{user['full_name']} has been reactivated.")
    return redirect("/users")
