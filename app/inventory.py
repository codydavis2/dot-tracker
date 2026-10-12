from flask import Blueprint, abort, flash, g, redirect, render_template, request

from .db import get_db
from .helpers import login_required

bp = Blueprint("inventory", __name__)


@bp.route("/inventory", methods=["GET", "POST"])
@login_required
def inventory():
    db = get_db()
    company_id = g.user["company_id"]

    if request.method == "POST":
        part_number = request.form.get("part_number") or None
        name = request.form.get("name")
        cost = request.form.get("cost") or None
        vendor = request.form.get("vendor") or None
        location = request.form.get("location") or None
        quantity = request.form.get("quantity") or 0
        low_stock_threshold = request.form.get("low_stock_threshold") or None

        if not name:
            flash("Part name is required.")
            return redirect("/inventory")

        db.execute(
            """
            INSERT INTO inventory
                (company_id, part_number, name, cost, vendor, location, quantity, low_stock_threshold)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (company_id, part_number, name, cost, vendor, location, quantity, low_stock_threshold),
        )
        db.commit()
        return redirect("/inventory")

    rows = db.execute(
        "SELECT * FROM inventory WHERE company_id = %s ORDER BY name, id", (company_id,)
    ).fetchall()

    items = [
        {
            **r,
            "low_stock": r["low_stock_threshold"] is not None and r["quantity"] <= r["low_stock_threshold"],
        }
        for r in rows
    ]

    return render_template("inventory.html", items=items)


@bp.route("/inventory/<int:item_id>/edit", methods=["POST"])
@login_required
def edit_inventory_item(item_id):
    db = get_db()
    company_id = g.user["company_id"]
    owned = db.execute(
        "SELECT id FROM inventory WHERE id = %s AND company_id = %s",
        (item_id, company_id),
    ).fetchone()
    if owned is None:
        abort(404)

    part_number = request.form.get("part_number") or None
    name = request.form.get("name")
    cost = request.form.get("cost") or None
    vendor = request.form.get("vendor") or None
    location = request.form.get("location") or None
    quantity = request.form.get("quantity") or 0
    low_stock_threshold = request.form.get("low_stock_threshold") or None

    if not name:
        flash("Part name is required.")
        return redirect("/inventory")

    db.execute(
        """
        UPDATE inventory
        SET part_number = %s, name = %s, cost = %s, vendor = %s, location = %s, quantity = %s, low_stock_threshold = %s
        WHERE id = %s AND company_id = %s
        """,
        (part_number, name, cost, vendor, location, quantity, low_stock_threshold, item_id, company_id),
    )
    db.commit()
    return redirect("/inventory")
