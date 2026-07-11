"""Listen proxy tests — Safari/iOS probe path and stream metadata."""

from __future__ import annotations

from unittest.mock import patch


def test_listen_head_returns_metadata_without_upstream(client, sample_station):
    with patch("app.routers.stations.httpx.AsyncClient") as mock_client_cls:
        resp = client.head(f"/api/stations/{sample_station.slug}/listen")

    assert resp.status_code == 200
    assert resp.headers.get("Accept-Ranges") == "bytes"
    assert resp.headers.get("Content-Type")
    assert resp.headers.get("Cache-Control") == "no-store"
    mock_client_cls.assert_not_called()


def test_listen_head_unknown_station(client):
    resp = client.head("/api/stations/missing-station/listen")
    assert resp.status_code == 404


def test_listen_head_disabled_station(client, db_session, sample_station):
    sample_station.enabled = False
    db_session.commit()

    resp = client.head(f"/api/stations/{sample_station.slug}/listen")
    assert resp.status_code == 404


def test_listen_m3u_includes_station_stream(client, sample_station):
    resp = client.get(f"/api/stations/{sample_station.slug}/listen.m3u")
    assert resp.status_code == 200
    body = resp.text
    assert "#EXTM3U" in body
    assert "Test Jazz" in body
    assert sample_station.icecast_mount.lstrip("/") in body
