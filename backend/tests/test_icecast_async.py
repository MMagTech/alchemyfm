"""Exercises icecast.py's Icecast status-json fetch as genuinely async.

_fetch_icestats_sources used to make a *blocking* httpx.Client call from
inside async route handlers. With a single Uvicorn worker/event loop, that
stalled every other in-flight request (including already-open audio
streams) for however long Icecast took to answer -- a freeze bug that
looked identical to, but was entirely separate from, the DB
connection-pool exhaustion fixed elsewhere. These tests verify the
httpx.AsyncClient conversion actually awaits (rather than blocks), parses
correctly, and that passing a shared mount_stats dict really does skip a
redundant Icecast round-trip (the optimization admin_list_stations, the
public station list, and the background refresh loop all rely on to avoid
N Icecast calls for N stations).
"""

from __future__ import annotations

from unittest.mock import patch

from app.services.icecast import (
    fetch_all_mount_stats,
    fetch_broadcast_totals,
    fetch_mount_now_playing,
)

ICESTATS_PAYLOAD = {
    "icestats": {
        "source": [
            {
                "listenurl": "http://icecast:8000/jazz",
                "artist": "Miles Davis",
                "title": "So What",
                "listeners": 3,
                "outgoing_kbitrate": 128,
            },
            {
                "listenurl": "http://icecast:8000/rock",
                "artist": "",
                "title": "Radar Love",
                "listeners": 0,
            },
        ]
    }
}


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url):
        return _FakeResponse(self._payload)


def _client_factory(payload):
    def _make(*args, **kwargs):
        return _FakeAsyncClient(payload)

    return _make


async def test_fetch_all_mount_stats_parses_sources_asynchronously():
    with patch("app.services.icecast.httpx.AsyncClient", side_effect=_client_factory(ICESTATS_PAYLOAD)):
        stats = await fetch_all_mount_stats()

    assert set(stats.keys()) == {"/jazz", "/rock"}
    assert stats["/jazz"].artist == "Miles Davis"
    assert stats["/jazz"].title == "So What"
    assert stats["/jazz"].listeners == 3
    assert stats["/jazz"].outgoing_kbps == 128
    assert stats["/rock"].listeners == 0


async def test_fetch_mount_now_playing_uses_shared_mount_stats_without_refetching():
    with patch(
        "app.services.icecast.httpx.AsyncClient", side_effect=_client_factory(ICESTATS_PAYLOAD)
    ) as mock_client:
        shared = await fetch_all_mount_stats()
        assert mock_client.call_count == 1

        now_playing = await fetch_mount_now_playing("/jazz", mount_stats=shared)
        # No second AsyncClient instantiation -- the shared stats were reused
        # instead of triggering another Icecast round-trip.
        assert mock_client.call_count == 1

    assert now_playing is not None
    assert now_playing.artist == "Miles Davis"
    assert now_playing.title == "So What"


async def test_fetch_mount_now_playing_fetches_its_own_stats_when_not_shared():
    with patch(
        "app.services.icecast.httpx.AsyncClient", side_effect=_client_factory(ICESTATS_PAYLOAD)
    ) as mock_client:
        now_playing = await fetch_mount_now_playing("/jazz")

    assert mock_client.call_count == 1
    assert now_playing is not None
    assert now_playing.artist == "Miles Davis"


async def test_fetch_all_mount_stats_returns_empty_on_connection_failure():
    def _raise(*args, **kwargs):
        raise ConnectionError("icecast unreachable")

    with patch("app.services.icecast.httpx.AsyncClient", side_effect=_raise):
        stats = await fetch_all_mount_stats()

    assert stats == {}


async def test_fetch_broadcast_totals_sums_listeners_and_kbps():
    with patch("app.services.icecast.httpx.AsyncClient", side_effect=_client_factory(ICESTATS_PAYLOAD)):
        totals = await fetch_broadcast_totals(["/jazz", "/rock"], stream_bitrate_kbps=128)

    assert totals["listeners"] == 3
    assert totals["outgoing_kbps"] == 3 * 128


async def test_admin_list_stations_fetches_icecast_stats_once_for_n_stations(admin_client):
    """The actual production bug: admin_list_stations used to call
    fetch_all_mount_stats once *per station* (via station_to_admin ->
    get_now_playing, each defaulting to mount_stats=None), so loading the
    admin page with N stations meant N separate Icecast round-trips,
    serially, on the single event loop. Confirm it now fetches once
    regardless of station count."""
    for i in range(5):
        resp = admin_client.post(
            "/api/admin/stations",
            json={
                "name": f"Icecast Count {i}",
                "slug": f"icecast-count-{i}",
                "icecast_mount": f"/icecast-count-{i}",
                "source_type": "clap_query",
                "source_ref": "test",
                "bootstrap_queue": False,
            },
        )
        assert resp.status_code == 201, resp.text

    with patch(
        "app.services.icecast.httpx.AsyncClient", side_effect=_client_factory(ICESTATS_PAYLOAD)
    ) as mock_client:
        resp = admin_client.get("/api/admin/stations")

    assert resp.status_code == 200
    assert len(resp.json()) >= 5
    assert mock_client.call_count == 1
