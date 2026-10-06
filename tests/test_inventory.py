from conftest import add_inventory_item, flashes


def test_add_inventory_item(client, query):
    item_id = add_inventory_item(client, query)
    row = query("SELECT * FROM inventory WHERE id = ?", (item_id,))[0]
    assert row["part_number"] == "K-123"
    assert row["name"] == "Brake chamber"
    assert row["cost"] == 54.99
    assert row["quantity"] == 5
    assert row["low_stock_threshold"] == 2


def test_add_inventory_requires_name(client, query):
    resp = client.post("/inventory", data={"name": ""})
    assert resp.headers["Location"] == "/inventory"
    assert flashes(client) == ["Part name is required."]
    assert query("SELECT * FROM inventory") == []


def test_add_inventory_defaults(client, query):
    add_inventory_item(client, query, part_number="", cost="", quantity="", low_stock_threshold="")
    row = query("SELECT * FROM inventory")[0]
    assert row["part_number"] is None
    assert row["cost"] is None
    assert row["quantity"] == 0
    assert row["low_stock_threshold"] is None


def test_edit_inventory_item(client, query):
    item_id = add_inventory_item(client, query)
    resp = client.post(f"/inventory/{item_id}/edit", data={
        "part_number": "K-999", "name": "Brake chamber 30/30", "cost": "61.00", "vendor": "TruckPro",
        "location": "Bay 3", "quantity": "8", "low_stock_threshold": "3",
    })
    assert resp.headers["Location"] == "/inventory"
    row = query("SELECT * FROM inventory WHERE id = ?", (item_id,))[0]
    assert row["name"] == "Brake chamber 30/30"
    assert row["vendor"] == "TruckPro"
    assert row["quantity"] == 8


def test_edit_inventory_requires_name(client, query):
    item_id = add_inventory_item(client, query)
    client.post(f"/inventory/{item_id}/edit", data={"name": ""})
    assert flashes(client) == ["Part name is required."]
    assert query("SELECT name FROM inventory")[0][0] == "Brake chamber"


def test_edit_missing_inventory_item(client):
    resp = client.post("/inventory/999/edit", data={"name": "x"})
    assert resp.headers["Location"] == "/inventory"
    assert flashes(client) == ["Inventory item not found."]


def test_low_stock_flag(client, query, captured):
    add_inventory_item(client, query, name="At threshold", quantity="2", low_stock_threshold="2")
    add_inventory_item(client, query, name="Below threshold", quantity="1", low_stock_threshold="2")
    add_inventory_item(client, query, name="Plenty", quantity="10", low_stock_threshold="2")
    add_inventory_item(client, query, name="No threshold", quantity="0", low_stock_threshold="")

    resp = client.get("/inventory")
    assert resp.status_code == 200
    name, ctx = captured[-1]
    assert name == "inventory.html"
    low = {i["name"]: i["low_stock"] for i in ctx["items"]}
    assert low == {"At threshold": True, "Below threshold": True, "Plenty": False, "No threshold": False}
    assert resp.data.count(b"Low Stock</span>") == 2
