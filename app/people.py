"""People dropdowns ("Created by", "Assigned to", "Completed by", "Inspector", ...).

Every people dropdown is built with people_choices() and every submitted value is
checked with validate_person(), so the rules live in one place: a person must be
an active user in the logged-in user's company.
"""
from flask import g

from .db import get_db


def people_choices(*keep_user_ids):
    """Users to list in a people dropdown: the company's active users by full name.

    When editing a record, pass the user ids already stored on it. Those users are
    included even if they have since been deactivated, so saving an edit never
    silently changes who signed the record.
    """
    keep = [user_id for user_id in keep_user_ids if user_id is not None]
    return get_db().execute(
        """
        SELECT id, full_name, is_active
        FROM users
        WHERE company_id = %s AND (is_active OR id = ANY(%s))
        ORDER BY full_name, id
        """,
        (g.user["company_id"], keep),
    ).fetchall()


def validate_person(value, label, required=False, current_user_id=None):
    """Check a user id submitted from a people dropdown.

    Returns (user_id, None) when valid, (None, None) when the field is optional and
    left blank, or (None, error_message) otherwise. Valid means an active user in the
    logged-in user's company. current_user_id is the value already stored on the
    record being edited; keeping it is allowed even if that user is now inactive.
    """
    if not value:
        if required:
            return None, f"{label} is required."
        return None, None

    try:
        user_id = int(value)
    except ValueError:
        return None, f"{label}: select an active user from your company."

    user = get_db().execute(
        "SELECT id, is_active FROM users WHERE id = %s AND company_id = %s",
        (user_id, g.user["company_id"]),
    ).fetchone()
    if user is None or (not user["is_active"] and user_id != current_user_id):
        return None, f"{label}: select an active user from your company."
    return user_id, None
