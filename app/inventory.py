from flask import Blueprint, flash, redirect, render_template, request, session

from .db import get_db
from .helpers import login_required

bp = Blueprint("inventory", __name__)


@bp.route("/inventory", methods=["GET", "POST"])
@login_required
def inventory():
    db = get_db()

    if request.method == "POST":
        part_number = request.form.get("part_number") or None
        name = request.form.get("name")
        cost = request.form.get("cost") or None
        vendor = request.form.get("vendor") or None
        location = request.form.get("location") or None
        quantity = request.form.get("quantity") or 0
        low_stock_threshold = request.form.get("low_stock_threshold") or None

        if not name:
            db.close()
            flash("Part name is required.")
            return redirect("/inventory")

        db.execute(
            """
            INSERT INTO inventory
                (user_id, part_number, name, cost, vendor, location, quantity, low_stock_threshold)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (session["user_id"], part_number, name, cost, vendor, location, quantity, low_stock_threshold),
        )
        db.commit()
        db.close()
        return redirect("/inventory")

    rows = db.execute(
        "SELECT * FROM inventory WHERE user_id = ? ORDER BY name", (session["user_id"],)
    ).fetchall()
    db.close()

    items = [
        {
            **dict(r),
            "low_stock": r["low_stock_threshold"] is not None and r["quantity"] <= r["low_stock_threshold"],
        }
        for r in rows
    ]

    return render_template("inventory.html", items=items)


@bp.route("/inventory/<int:item_id>/edit", methods=["POST"])
@login_required
def edit_inventory_item(item_id):
    db = get_db()
    owned = db.execute(
        "SELECT id FROM inventory WHERE id = ? AND user_id = ?",
        (item_id, session["user_id"]),
    ).fetchone()
    if owned is None:
        db.close()
        flash("Inventory item not found.")
        return redirect("/inventory")

    part_number = request.form.get("part_number") or None
    name = request.form.get("name")
    cost = request.form.get("cost") or None
    vendor = request.form.get("vendor") or None
    location = request.form.get("location") or None
    quantity = request.form.get("quantity") or 0
    low_stock_threshold = request.form.get("low_stock_threshold") or None

    if not name:
        db.close()
        flash("Part name is required.")
        return redirect("/inventory")

    db.execute(
        """
        UPDATE inventory
        SET part_number = ?, name = ?, cost = ?, vendor = ?, location = ?, quantity = ?, low_stock_threshold = ?
        WHERE id = ?
        """,
        (part_number, name, cost, vendor, location, quantity, low_stock_threshold, item_id),
    )
    db.commit()
    db.close()
    return redirect("/inventory")
