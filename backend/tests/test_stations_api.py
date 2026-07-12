"""Public stations API tests."""

from __future__ import annotations


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


def test_list_stations_empty(client):
    resp = client.get("/api/stations")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_stations_with_enabled_station(client, sample_station):
    resp = client.get("/api/stations")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["slug"] == "test-jazz"
    assert data[0]["name"] == "Test Jazz"


def test_get_station_detail(client, sample_station):
    resp = client.get(f"/api/stations/{sample_station.slug}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["slug"] == "test-jazz"
    assert data["source_type"] == "clap_query"


def test_list_stations_sorts_featured_first(client, db_session):
    from app.database import Station

    db_session.add_all(
        [
            Station(
                name="Alpha",
                slug="alpha",
                icecast_mount="/alpha",
                enabled=True,
                source_type="clap_query",
                source_ref="alpha",
            ),
            Station(
                name="Zeta",
                slug="zeta",
                icecast_mount="/zeta",
                enabled=True,
                source_type="clap_query",
                source_ref="zeta",
                featured=True,
                featured_order=0,
            ),
        ]
    )
    db_session.commit()

    resp = client.get("/api/stations")
    assert resp.status_code == 200
    data = resp.json()
    assert [s["slug"] for s in data] == ["zeta", "alpha"]
    assert data[0]["featured"] is True
    assert data[1]["featured"] is False
