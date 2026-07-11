"""Plugin → backend deploy contract — real payload shape from Channel Designer."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

PLUGIN_TESTS = (
    Path(__file__).resolve().parents[2] / "audiomuse-plugins" / "alchemy_fm_bridge" / "tests"
)
if str(PLUGIN_TESTS) not in sys.path:
    sys.path.insert(0, str(PLUGIN_TESTS))

from bridge_loader import bridge  # noqa: E402


def _sample_profile() -> dict[str, Any]:
    return {
        "station": {
            "name": "Late Night Rock",
            "slug": "late-night-rock",
            "description": "Test channel",
            "icecast_mount": "/late-night-rock",
            "enabled": True,
            "queue_target": 30,
            "refresh_threshold": 10,
            "artist_separation_minutes": 90,
            "bootstrap_queue": True,
        },
        "programming": {"type": "clap_query", "query": "late night rock", "limit": 30},
        "refresh": {"mode": "similar_to_last"},
    }


def _preview_tracks() -> list[dict[str, Any]]:
    return [
        {"item_id": "clap-1", "title": "One", "author": "Artist"},
        {"item_id": "clap-2", "title": "Two", "author": "Artist"},
    ]


@pytest.mark.parametrize(
    "programming",
    [
        {"type": "clap_query", "query": "late night rock", "limit": 30},
        {"type": "lyrics_query", "query": "open road", "limit": 30},
        {
            "type": "mood_centroid",
            "mood": "energetic",
            "centroid_index": 0,
            "limit": 30,
        },
        {"type": "alchemy_anchor", "anchor_id": "42", "limit": 30},
        {"type": "similar_seed", "seed_id": "seed-99", "limit": 30},
    ],
)
def test_plugin_payload_creates_and_bootstraps(admin_client, bootstrap_mocks, programming):
    profile = _sample_profile()
    profile["programming"] = programming
    profile["station"]["slug"] = f"contract-{programming['type']}"
    profile["station"]["icecast_mount"] = f"/contract-{programming['type']}"

    payload = bridge.channel_profile_to_alchemy_payload(profile, _preview_tracks())
    payload["bootstrap_queue"] = False

    create = admin_client.post("/api/admin/stations", json=payload)
    assert create.status_code == 201, create.text
    station = create.json()
    assert station["source_type"] == programming["type"]

    bootstrap = admin_client.post(f"/api/admin/stations/{station['id']}/bootstrap")
    assert bootstrap.status_code == 200, bootstrap.text
    assert bootstrap.json()["queued_count"] >= 1


def test_plugin_push_station_sequence(admin_client, bootstrap_mocks):
    """Mirrors AlchemyFmClient.push_station: create (no inline bootstrap) then /bootstrap."""
    payload = bridge.channel_profile_to_alchemy_payload(_sample_profile(), _preview_tracks())

    client = bridge.AlchemyFmClient("http://testserver", "admin", "test-admin-secret")
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def fake_request(method, path, body=None):
        calls.append((method, path, body))
        if method == "GET" and path == "/api/admin/stations":
            return []
        if method == "POST" and path == "/api/admin/stations":
            resp = admin_client.post("/api/admin/stations", json=body)
            assert resp.status_code == 201, resp.text
            return resp.json()
        if method == "POST" and path.endswith("/bootstrap"):
            station_id = path.split("/")[-2]
            resp = admin_client.post(f"/api/admin/stations/{station_id}/bootstrap")
            assert resp.status_code == 200, resp.text
            return resp.json()
        raise AssertionError(f"Unexpected request: {method} {path}")

    with patch.object(client, "_request", side_effect=fake_request):
        station, action = client.push_station(payload, bootstrap=True)
    assert action == "created"
    assert station["queued_count"] >= 1
    create_calls = [c for c in calls if c[0] == "POST" and c[1] == "/api/admin/stations"]
    assert create_calls
    assert create_calls[0][2]["bootstrap_queue"] is False
    assert any(c[1].endswith("/bootstrap") for c in calls)
