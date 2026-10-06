import io
import os

from conftest import add_vehicle, add_work_order, flashes


def upload(client, wo_id, vehicle_id, filename, content=b"file contents"):
    return client.post(f"/work-orders/{wo_id}/edit", content_type="multipart/form-data", data={
        "vehicle_id": str(vehicle_id),
        "description": "Replace brake chamber",
        "attachments": (io.BytesIO(content), filename),
    })


def stored_path(app, wo_id, stored_filename):
    return os.path.join(app.config["UPLOAD_DIR"], str(wo_id), stored_filename)


def test_upload_attachment(app, client, query):
    vehicle_id = add_vehicle(client)
    wo_id = add_work_order(client, query, vehicle_id)
    resp = upload(client, wo_id, vehicle_id, "invoice 1.pdf", b"%PDF-1.4 test")
    assert resp.headers["Location"] == f"/work-orders/{wo_id}"

    row = query("SELECT * FROM work_order_attachments")[0]
    assert row["work_order_id"] == wo_id
    assert row["original_filename"] == "invoice_1.pdf"  # run through secure_filename
    assert row["stored_filename"].endswith(".pdf")
    assert row["stored_filename"] != row["original_filename"]
    with open(stored_path(app, wo_id, row["stored_filename"]), "rb") as f:
        assert f.read() == b"%PDF-1.4 test"


def test_upload_on_create(app, client, query):
    vehicle_id = add_vehicle(client)
    add_work_order(client, query, vehicle_id, attachments=(io.BytesIO(b"img"), "photo.JPG"))
    row = query("SELECT * FROM work_order_attachments")[0]
    assert row["stored_filename"].endswith(".jpg")
    assert os.path.exists(stored_path(app, row["work_order_id"], row["stored_filename"]))


def test_disallowed_file_type_rejected(app, client, query):
    vehicle_id = add_vehicle(client)
    wo_id = add_work_order(client, query, vehicle_id)
    resp = upload(client, wo_id, vehicle_id, "malware.exe")
    # The rest of the edit still saves; the file is skipped with a message
    assert resp.headers["Location"] == f"/work-orders/{wo_id}"
    assert flashes(client) == ['Skipped "malware.exe" — unsupported file type.']
    assert query("SELECT * FROM work_order_attachments") == []
    assert not os.path.exists(os.path.join(app.config["UPLOAD_DIR"], str(wo_id)))


def test_download_attachment(client, query):
    vehicle_id = add_vehicle(client)
    wo_id = add_work_order(client, query, vehicle_id)
    upload(client, wo_id, vehicle_id, "notes.txt", b"torque to spec")
    attachment_id = query("SELECT id FROM work_order_attachments")[0]["id"]

    resp = client.get(f"/work-orders/{wo_id}/attachments/{attachment_id}")
    assert resp.status_code == 200
    assert resp.data == b"torque to spec"
    assert "notes.txt" in resp.headers["Content-Disposition"]
    resp.close()


def test_download_attachment_wrong_work_order_is_404(client, query):
    vehicle_id = add_vehicle(client)
    wo_id = add_work_order(client, query, vehicle_id)
    other_wo_id = add_work_order(client, query, vehicle_id)
    upload(client, wo_id, vehicle_id, "notes.txt")
    attachment_id = query("SELECT id FROM work_order_attachments")[0]["id"]
    assert client.get(f"/work-orders/{other_wo_id}/attachments/{attachment_id}").status_code == 404


def test_delete_attachment(app, client, query):
    vehicle_id = add_vehicle(client)
    wo_id = add_work_order(client, query, vehicle_id)
    upload(client, wo_id, vehicle_id, "notes.txt")
    row = query("SELECT * FROM work_order_attachments")[0]
    path = stored_path(app, wo_id, row["stored_filename"])
    assert os.path.exists(path)

    resp = client.post(f"/work-orders/{wo_id}/attachments/{row['id']}/delete")
    assert resp.headers["Location"] == f"/work-orders/{wo_id}"
    assert query("SELECT * FROM work_order_attachments") == []
    assert not os.path.exists(path)


def test_deleting_work_order_removes_its_files(app, client, query):
    vehicle_id = add_vehicle(client)
    wo_id = add_work_order(client, query, vehicle_id)
    upload(client, wo_id, vehicle_id, "notes.txt")
    client.post(f"/work-orders/{wo_id}/delete")
    assert query("SELECT * FROM work_order_attachments") == []
    assert not os.path.exists(os.path.join(app.config["UPLOAD_DIR"], str(wo_id)))


def test_upload_too_large(app, client, query, monkeypatch):
    vehicle_id = add_vehicle(client)
    wo_id = add_work_order(client, query, vehicle_id)
    # Shrink the limit (normally 20 MB) after setup so the test upload stays small
    monkeypatch.setitem(app.config, "MAX_CONTENT_LENGTH", 1024)
    resp = client.post(
        f"/work-orders/{wo_id}/edit",
        content_type="multipart/form-data",
        data={"vehicle_id": str(vehicle_id), "attachments": (io.BytesIO(b"x" * 4096), "big.pdf")},
        headers={"Referer": f"/work-orders/{wo_id}"},
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == f"/work-orders/{wo_id}"
    assert flashes(client) == ["That upload is too large. Max total upload size is 20 MB."]
    assert query("SELECT * FROM work_order_attachments") == []
