"""Invites: the owner or a manager invites someone by email + role (never owner);
they open the emailed link, choose a name and password, and get an account in
the inviter's company.

Managing invites (send / resend / revoke) is for the owner and managers
(permissions.py). Accepting needs no login:
the token in the link is the proof. Tokens are stored only as hashes (tokens.py),
expire after 7 days, and stop working once accepted, revoked, or replaced by a resend.
"""
from datetime import timedelta

import psycopg
from flask import Blueprint, abort, flash, g, redirect, render_template, request
from werkzeug.security import generate_password_hash

from .auth import normalize_email, start_session
from .constants import ASSIGNABLE_ROLES, ROLE_LABELS
from .db import get_db
from .email import absolute_url, send_email, show_dev_link
from .extensions import limiter
from .passwords import password_error
from .permissions import home_path
from .tokens import hash_token, new_token

bp = Blueprint("invites", __name__)

INVITE_LIFETIME = timedelta(days=7)

INVALID_INVITE_MESSAGE = (
    "This invite is no longer valid — it may have expired, been used already, or been "
    "replaced by a newer invite. Ask your company owner for a new one."
)


def email_has_account(email):
    """True if any user, in any company, has this email. Deliberately not scoped to the
    current company: emails are globally unique because each person belongs to one company."""
    return get_db().execute("SELECT 1 FROM users WHERE email = %s", (email,)).fetchone() is not None


def _send_invite_email(email, role, token):
    link = absolute_url(f"/invite/{token}")
    send_email(
        email,
        f"You're invited to join {g.user['company_name']} on Gladhand",
        f"{g.user['full_name']} invited you to join {g.user['company_name']} on Gladhand "
        f"as a {ROLE_LABELS[role].lower()}.\n\n"
        f"Set up your account here (the link works for 7 days):\n{link}\n\n"
        "If you weren't expecting this, you can ignore this email.\n",
    )
    show_dev_link(link)


def _get_company_invite(invite_id):
    """An invite in the logged-in user's company, or 404."""
    invite = get_db().execute(
        "SELECT * FROM invites WHERE id = %s AND company_id = %s",
        (invite_id, g.user["company_id"]),
    ).fetchone()
    if invite is None:
        abort(404)
    return invite


@bp.route("/users/invites", methods=["POST"])
def create_invite():
    email = normalize_email(request.form.get("email"))
    role = request.form.get("role")

    if not email or "@" not in email:
        flash("Enter a valid email address to invite.")
        return redirect("/users")
    if role not in ASSIGNABLE_ROLES:
        flash("Choose a role: manager, mechanic, or driver.")
        return redirect("/users")
    if email_has_account(email):
        flash(f"{email} already has a Gladhand account. Each person can belong to only one company.")
        return redirect("/users")

    db = get_db()
    token, token_hash = new_token()
    try:
        db.execute(
            """
            INSERT INTO invites (company_id, email, role, token_hash, invited_by_user_id, expires_at)
            VALUES (%s, %s, %s, %s, %s, now() + %s)
            """,
            (g.user["company_id"], email, role, token_hash, g.user["id"], INVITE_LIFETIME),
        )
    except psycopg.errors.UniqueViolation:
        # invites_one_pending_per_email
        db.rollback()
        flash(f"{email} already has a pending invite. Use Resend to send a fresh link.")
        return redirect("/users")
    db.commit()

    _send_invite_email(email, role, token)
    flash(f"Invite sent to {email}.")
    return redirect("/users")


@bp.route("/users/invites/<int:invite_id>/resend", methods=["POST"])
def resend_invite(invite_id):
    """New token and a fresh 7 days; the previously emailed link stops working."""
    invite = _get_company_invite(invite_id)
    if invite["accepted_at"] is not None or invite["revoked_at"] is not None:
        flash("That invite is no longer pending.")
        return redirect("/users")
    if email_has_account(invite["email"]):
        flash(f"{invite['email']} already has a Gladhand account. Revoke this invite.")
        return redirect("/users")

    db = get_db()
    token, token_hash = new_token()
    db.execute(
        "UPDATE invites SET token_hash = %s, expires_at = now() + %s WHERE id = %s AND company_id = %s",
        (token_hash, INVITE_LIFETIME, invite["id"], g.user["company_id"]),
    )
    db.commit()

    _send_invite_email(invite["email"], invite["role"], token)
    flash(f"Invite resent to {invite['email']}.")
    return redirect("/users")


@bp.route("/users/invites/<int:invite_id>/revoke", methods=["POST"])
def revoke_invite(invite_id):
    invite = _get_company_invite(invite_id)
    if invite["accepted_at"] is not None or invite["revoked_at"] is not None:
        flash("That invite is no longer pending.")
        return redirect("/users")

    db = get_db()
    db.execute(
        "UPDATE invites SET revoked_at = now() WHERE id = %s AND company_id = %s",
        (invite["id"], g.user["company_id"]),
    )
    db.commit()
    flash(f"Invite to {invite['email']} revoked.")
    return redirect("/users")


def _find_valid_invite(token, lock=False):
    """The pending, unexpired invite for this raw token (with its company name), or None.
    No company filter here: there's no logged-in user yet, the token itself identifies
    the invite and its company."""
    return get_db().execute(
        """
        SELECT invites.id, invites.company_id, invites.email, invites.role, companies.name AS company_name
        FROM invites
        JOIN companies ON companies.id = invites.company_id
        WHERE invites.token_hash = %s
          AND invites.accepted_at IS NULL
          AND invites.revoked_at IS NULL
          AND invites.expires_at > now()
        """ + (" FOR UPDATE OF invites" if lock else ""),
        (hash_token(token),),
    ).fetchone()


def _invalid_invite_page():
    return render_template("error.html", title="Invite no longer valid", message=INVALID_INVITE_MESSAGE), 404


def _email_taken_page(email):
    return render_template(
        "error.html",
        title="Account already exists",
        message=f"There's already a Gladhand account for {email}. Log in with it, or ask "
                "your company owner to invite a different email address.",
    ), 409


@bp.route("/invite/<token>", methods=["GET", "POST"])
@limiter.limit("5/minute", methods=["POST"])
def accept_invite(token):
    """Accept an invite: choose a name and password; the account gets the invite's
    company and role, then is logged in."""
    invite = _find_valid_invite(token)
    if invite is None:
        return _invalid_invite_page()
    if email_has_account(invite["email"]):
        return _email_taken_page(invite["email"])

    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        password = request.form.get("password") or ""
        confirmation = request.form.get("confirmation")

        error = None
        if not full_name:
            error = "Enter your full name."
        else:
            error = password_error(password, invite["email"])
        if error is None and password != confirmation:
            error = "Passwords do not match."
        if error:
            flash(error)
            return redirect(f"/invite/{token}")

        db = get_db()
        # Lock the invite and check it again, so it can't be accepted twice at once.
        invite = _find_valid_invite(token, lock=True)
        if invite is None:
            db.rollback()
            return _invalid_invite_page()
        try:
            user = db.execute(
                """
                INSERT INTO users (company_id, email, password_hash, full_name, role)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, auth_version
                """,
                (invite["company_id"], invite["email"], generate_password_hash(password), full_name, invite["role"]),
            ).fetchone()
        except psycopg.errors.UniqueViolation:
            # Someone took the email between the check above and now
            db.rollback()
            return _email_taken_page(invite["email"])
        db.execute(
            "UPDATE invites SET accepted_at = now() WHERE id = %s AND company_id = %s",
            (invite["id"], invite["company_id"]),
        )
        db.execute("UPDATE users SET last_login_at = now() WHERE id = %s", (user["id"],))
        db.commit()

        start_session(user)
        flash(f"Welcome to {invite['company_name']}!")
        return redirect(home_path(invite["role"]))

    return render_template("accept_invite.html", invite=invite, token=token)
