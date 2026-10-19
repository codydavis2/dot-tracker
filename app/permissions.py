"""Who may use which page: the ONE place permissions are decided (CLAUDE.md rule 6).

PERMISSIONS maps every Flask endpoint to the roles allowed to use it, or to
PUBLIC (no login needed) or ANY_LOGGED_IN. check_permission() runs before every
request and enforces it:

  * endpoint not in the map      -> 403 for everyone, logged in or not (fail closed)
  * PUBLIC                       -> allowed
  * anything else, logged out    -> redirect to /login
  * logged in, role not allowed  -> 403

There are no per-route permission decorators. A new route stays unusable until
it is added here, and tests/test_permissions.py fails until it is.

Templates ask the same map with user_can("endpoint") (and Python code with
can(role, endpoint)), so the UI always matches what the server allows. Hiding
a button is only UX; this check is the security control.

Tenant isolation is separate and unchanged: a record of another company is
still a 404 from the route itself, whatever the role.
"""
from flask import abort, g, redirect, request

PUBLIC = "public"
ANY_LOGGED_IN = "any_logged_in"

OWNER = frozenset({"owner"})
OFFICE = frozenset({"owner", "manager"})
SHOP = frozenset({"owner", "manager", "mechanic"})
EVERYONE = frozenset({"owner", "manager", "mechanic", "driver"})

# The roles allowed to create work orders also see red-item alerts and may mark
# an inspection reviewed (see "mark_reviewed" below and dashboard.py).
WORK_ORDER_CREATORS = SHOP

PERMISSIONS = {
    "static": PUBLIC,

    # Logging in, signing up, password reset, accepting an invite
    "auth.login": PUBLIC,
    "auth.register": PUBLIC,
    "auth.forgot_password": PUBLIC,
    "auth.reset_password": PUBLIC,
    "invites.accept_invite": PUBLIC,
    "auth.logout": ANY_LOGGED_IN,

    # My Account
    "account.index": ANY_LOGGED_IN,
    "account.change_name": ANY_LOGGED_IN,
    "account.change_password": ANY_LOGGED_IN,

    # User and invite management
    "users.index": OFFICE,
    "users.change_role": OFFICE,
    "users.rename": OFFICE,
    "users.deactivate": OFFICE,
    "users.reactivate": OFFICE,
    "invites.create_invite": OFFICE,
    "invites.resend_invite": OFFICE,
    "invites.revoke_invite": OFFICE,

    "dashboard.index": SHOP,
    "audit.audit": SHOP,

    # Vehicles. Drivers see the list and a reduced detail page (see vehicles.py).
    "vehicles.vehicles": EVERYONE,
    "vehicles.vehicle_detail": EVERYONE,
    "vehicles.add_vehicle": OFFICE,
    "vehicles.add_maintenance": SHOP,
    "vehicles.add_dtc": SHOP,

    # Inspections. Which TYPES each role may submit: INSPECTION_TYPE_ROLES below.
    "inspections.inspections": EVERYONE,
    "inspections.add_inspection_report": EVERYONE,
    "inspections.inspection_report_detail": EVERYONE,
    "inspections.add_reminder": SHOP,
    "inspections.complete_reminder": SHOP,

    # Work orders and attachments
    "work_orders.work_orders": SHOP,
    "work_orders.create_work_order": WORK_ORDER_CREATORS,
    "work_orders.work_order_detail": SHOP,
    "work_orders.edit_work_order": SHOP,
    "work_orders.close_work_order": SHOP,
    "work_orders.delete_work_order": OFFICE,
    "work_orders.download_attachment": SHOP,
    "work_orders.delete_attachment": OFFICE,

    "inventory.inventory": SHOP,
    "inventory.edit_inventory_item": SHOP,
}


def can(role, endpoint):
    """True if a user with this role (None = logged out) may use this endpoint."""
    allowed = PERMISSIONS.get(endpoint)
    if allowed is None:
        return False
    if allowed == PUBLIC:
        return True
    if role is None:
        return False
    if allowed == ANY_LOGGED_IN:
        return True
    return role in allowed


def user_can(endpoint):
    """can() for the logged-in user (templates: {% if user_can("vehicles.add_vehicle") %})."""
    return can(g.user["role"] if g.user else None, endpoint)


def home_path(role):
    """Where a user lands after logging in: the dashboard, or for roles that can't
    see it (drivers), the inspections page."""
    return "/" if can(role, "dashboard.index") else "/inspections"


def check_permission():
    """before_request: enforce PERMISSIONS. Must run after auth.load_logged_in_user."""
    if request.routing_exception is not None:
        return None  # no such URL / method: let Flask answer 404 / 405 as usual
    endpoint = request.endpoint
    if endpoint not in PERMISSIONS:
        abort(403)  # fail closed: unmapped endpoints are denied for everyone
    if can(g.user["role"] if g.user else None, endpoint):
        return None
    if g.user is None:
        return redirect("/login")
    abort(403)
