"""Admin station API tests — Channel Designer deploy surface."""

from __future__ import annotations

# Minimal valid PNG generated at import (Pillow is a backend dependency).
def _mini_png() -> bytes:
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (1, 1), color="#336699").save(buf, format="PNG")
    return buf.getvalue()


MINI_PNG = _mini_png()


def _station_payload(**overrides):
    payload = {
        "name": "Deploy Test FM",
        "slug": "deploy-test",
        "description": "integration test station",
        "icecast_mount": "/deploy-test",
        "enabled": True,
        "source_type": "clap_query",
        "source_ref": "smooth jazz",
        "programming_json": '{"programming":{"type":"clap_query","query":"smooth jazz"}}',
        "continuation_mode": "similar_to_last",
        "bootstrap_queue": False,
    }
    payload.update(overrides)
    return payload


def test_admin_stations_require_auth(client):
    resp = client.get("/api/admin/stations")
    assert resp.status_code == 401


def test_admin_login_with_test_credentials(client):
    resp = client.post(
        "/api/admin/login",
        json={"username": "admin", "password": "test-admin-secret"},
    )
    assert resp.status_code == 200
    assert resp.json()["username"] == "admin"


def test_admin_create_station(admin_client):
    resp = admin_client.post("/api/admin/stations", json=_station_payload())
    assert resp.status_code == 201
    data = resp.json()
    assert data["slug"] == "deploy-test"
    assert data["source_type"] == "clap_query"
    assert data["queued_count"] == 0


def test_admin_create_station_with_bootstrap(admin_client, bootstrap_mocks):
    resp = admin_client.post(
        "/api/admin/stations",
        json=_station_payload(slug="bootstrapped", bootstrap_queue=True),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["slug"] == "bootstrapped"
    assert data["queued_count"] >= 1


def test_admin_bootstrap_endpoint(admin_client, sample_station, bootstrap_mocks):
    resp = admin_client.post(f"/api/admin/stations/{sample_station.id}/bootstrap")
    assert resp.status_code == 200
    assert resp.json()["queued_count"] >= 1


def test_admin_refresh_queue(admin_client, sample_station, bootstrap_mocks):
    resp = admin_client.post(f"/api/admin/stations/{sample_station.id}/refresh-queue")
    assert resp.status_code == 200
    assert resp.json()["slug"] == sample_station.slug


def test_admin_rebuild_m3u(admin_client, sample_station, db_session):
    from app.database import QueueItem, QueueItemStatus

    db_session.add(
        QueueItem(
            station_id=sample_station.id,
            item_id="rebuild-1",
            title="Rebuild Track",
            artist="Test Artist",
            duration_sec=200,
            status=QueueItemStatus.queued,
            position=1,
            stream_url="http://navidrome.test/stream/rebuild-1",
        )
    )
    db_session.commit()

    resp = admin_client.post(f"/api/admin/stations/{sample_station.id}/rebuild-m3u")
    assert resp.status_code == 200

    from app.services.queue import queue_m3u_path

    body = queue_m3u_path(sample_station.slug).read_text(encoding="utf-8")
    assert "Rebuild Track" in body
    assert "rebuild-1" in body


def test_admin_update_station(admin_client, sample_station):
    resp = admin_client.put(
        f"/api/admin/stations/{sample_station.id}",
        json={"description": "updated via admin"},
    )
    assert resp.status_code == 200
    assert resp.json()["description"] == "updated via admin"


def test_admin_feature_station_auto_assigns_order(admin_client, sample_station):
    resp = admin_client.put(
        f"/api/admin/stations/{sample_station.id}",
        json={"featured": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["featured"] is True
    assert data["featured_order"] == 0


def test_admin_feature_stations_stack_order(admin_client):
    ids = []
    for i in range(3):
        resp = admin_client.post(
            "/api/admin/stations",
            json=_station_payload(slug=f"feat-{i}", icecast_mount=f"/feat-{i}"),
        )
        ids.append(resp.json()["id"])

    orders = []
    for station_id in ids:
        resp = admin_client.put(f"/api/admin/stations/{station_id}", json={"featured": True})
        assert resp.status_code == 200
        orders.append(resp.json()["featured_order"])
    assert orders == [0, 1, 2]


def test_admin_feature_station_rejects_fourth(admin_client):
    ids = []
    for i in range(4):
        resp = admin_client.post(
            "/api/admin/stations",
            json=_station_payload(slug=f"cap-{i}", icecast_mount=f"/cap-{i}"),
        )
        ids.append(resp.json()["id"])

    for station_id in ids[:3]:
        resp = admin_client.put(f"/api/admin/stations/{station_id}", json={"featured": True})
        assert resp.status_code == 200

    resp = admin_client.put(f"/api/admin/stations/{ids[3]}", json={"featured": True})
    assert resp.status_code == 400
    assert "featured" in resp.json()["detail"].lower()


def test_admin_feature_cap_expands_at_fifteen_stations(admin_client):
    ids = []
    for i in range(15):
        resp = admin_client.post(
            "/api/admin/stations",
            json=_station_payload(slug=f"tier-{i}", icecast_mount=f"/tier-{i}"),
        )
        ids.append(resp.json()["id"])

    for station_id in ids[:3]:
        resp = admin_client.put(f"/api/admin/stations/{station_id}", json={"featured": True})
        assert resp.status_code == 200

    # 15 enabled stations exist now, so the cap should have expanded to 6.
    for station_id in ids[3:6]:
        resp = admin_client.put(f"/api/admin/stations/{station_id}", json={"featured": True})
        assert resp.status_code == 200

    resp = admin_client.put(f"/api/admin/stations/{ids[6]}", json={"featured": True})
    assert resp.status_code == 400


def test_admin_unfeature_then_refeature_reuses_slot(admin_client):
    ids = []
    for i in range(4):
        resp = admin_client.post(
            "/api/admin/stations",
            json=_station_payload(slug=f"slot-{i}", icecast_mount=f"/slot-{i}"),
        )
        ids.append(resp.json()["id"])

    for station_id in ids[:3]:
        admin_client.put(f"/api/admin/stations/{station_id}", json={"featured": True})

    admin_client.put(f"/api/admin/stations/{ids[0]}", json={"featured": False})

    resp = admin_client.put(f"/api/admin/stations/{ids[3]}", json={"featured": True})
    assert resp.status_code == 200


def test_admin_upload_and_delete_artwork(admin_client, sample_station):
    upload = admin_client.post(
        f"/api/admin/stations/{sample_station.id}/artwork",
        files={"file": ("art.png", MINI_PNG, "image/png")},
    )
    assert upload.status_code == 200
    assert upload.json()["has_uploaded_artwork"] is True

    delete = admin_client.delete(f"/api/admin/stations/{sample_station.id}/artwork")
    assert delete.status_code == 200
    assert delete.json()["has_uploaded_artwork"] is False


def test_admin_delete_station(admin_client):
    create = admin_client.post(
        "/api/admin/stations",
        json=_station_payload(slug="delete-me", icecast_mount="/delete-me"),
    )
    station_id = create.json()["id"]

    resp = admin_client.delete(f"/api/admin/stations/{station_id}")
    assert resp.status_code == 204

    listing = admin_client.get("/api/admin/stations")
    assert all(row["id"] != station_id for row in listing.json())


def test_admin_list_stations(admin_client, sample_station):
    resp = admin_client.get("/api/admin/stations")
    assert resp.status_code == 200
    slugs = {row["slug"] for row in resp.json()}
    assert sample_station.slug in slugs
