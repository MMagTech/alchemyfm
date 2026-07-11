"""Deploy integration tests — plugin → Alchemy FM admin API contract."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from bridge_loader import bridge


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


def test_channel_profile_to_alchemy_payload_clap():
    tracks = [
        {"item_id": "clap-1", "title": "One", "author": "Artist"},
        {"item_id": "clap-2", "title": "Two", "author": "Artist"},
    ]
    payload = bridge.channel_profile_to_alchemy_payload(_sample_profile(), tracks)
    assert payload["slug"] == "late-night-rock"
    assert payload["source_type"] == "clap_query"
    assert payload["source_ref"] == "late night rock"
    assert payload["continuation_mode"] == "similar_to_last"
    assert payload["identity_seed_item_id"] == "clap-2"
    assert payload["bootstrap_queue"] is True


class _RecordingClient(bridge.AlchemyFmClient):
    def __init__(self) -> None:
        super().__init__("http://alchemyfm.test", "admin", "test-password")
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append((method, path, payload))
        if method == "GET" and path == "/api/admin/deploy-check":
            return {"ok": True, "audiomuse": {"ok": True}, "navidrome": {"ok": True}}
        if method == "GET" and path == "/api/admin/stations":
            return []
        if method == "POST" and path == "/api/admin/stations":
            return {"id": 42, "slug": payload["slug"] if payload else "late-night-rock"}
        if method == "PUT" and path.startswith("/api/admin/stations/"):
            return {"id": 42, "slug": "late-night-rock"}
        if method == "POST" and path.endswith("/bootstrap"):
            return {"id": 42, "slug": "late-night-rock", "queued_count": 5}
        if method == "POST" and path.endswith("/refresh-queue"):
            return {"id": 42, "slug": "late-night-rock", "queued_count": 8}
        if method == "POST" and path.endswith("/rebuild-m3u"):
            return {"id": 42, "slug": "late-night-rock", "queued_count": 8}
        if method == "DELETE" and path.endswith("/artwork"):
            return {"id": 42, "slug": "late-night-rock", "has_uploaded_artwork": False}
        if method == "DELETE" and path.startswith("/api/admin/stations/"):
            return None
        raise AssertionError(f"Unexpected request: {method} {path}")


def test_verify_deploy_ready_legacy_backend_audiomuse_401():
    client = bridge.AlchemyFmClient("http://alchemyfm.test", "admin", "test-password")

    def fake_request(method, path, payload=None):
        if method == "GET" and path == "/api/admin/deploy-check":
            raise bridge.ChannelDesignerError("Not Found", status=404)
        raise AssertionError(f"Unexpected request: {method} {path}")

    with (
        patch.object(client, "_request", side_effect=fake_request),
        patch.object(bridge, "audiomuse_get", side_effect=bridge.ChannelDesignerError("Unauthorized", status=401)),
    ):
        try:
            client.verify_deploy_ready()
            assert False, "expected ChannelDesignerError"
        except bridge.ChannelDesignerError as exc:
            assert "401" in str(exc)
            assert "AUDIOMUSE_API_TOKEN" in str(exc)


def test_verify_deploy_ready_legacy_backend_allows_deploy():
    client = bridge.AlchemyFmClient("http://alchemyfm.test", "admin", "test-password")

    def fake_request(method, path, payload=None):
        if method == "GET" and path == "/api/admin/deploy-check":
            raise bridge.ChannelDesignerError("Not Found", status=404)
        raise AssertionError(f"Unexpected request: {method} {path}")

    with (
        patch.object(client, "_request", side_effect=fake_request),
        patch.object(bridge, "audiomuse_get", return_value={"centroids": []}),
    ):
        warning = client.verify_deploy_ready()
    assert warning is not None
    assert "deploy-check" in warning
    assert "AUDIOMUSE_API_TOKEN" in warning


def test_merge_saved_programming_when_form_empty():
    profile = {
        "station": {"name": "Test", "slug": "test-slug"},
        "programming": {"type": "clap_query", "query": "", "limit": 30},
    }
    saved = {
        "station": {"name": "Test", "slug": "test-slug"},
        "programming": {"type": "clap_query", "query": "late night rock", "limit": 30},
    }
    with patch.object(bridge, "_load_saved_channel", return_value=(saved, ["clap-1"])):
        merged = bridge._merge_saved_programming_if_needed(profile, "test-slug")
    assert merged["programming"]["query"] == "late night rock"


def test_push_station_create_and_bootstrap():
    client = _RecordingClient()
    payload = bridge.channel_profile_to_alchemy_payload(
        _sample_profile(),
        [{"item_id": "clap-1", "title": "One", "author": "Artist"}],
    )

    station, action, bootstrap_warning = client.push_station(payload, bootstrap=True)

    assert action == "created"
    assert bootstrap_warning is None
    assert station["id"] == 42
    post_create = [
        (method, path, body)
        for method, path, body in client.calls
        if method == "POST" and path == "/api/admin/stations"
    ]
    assert post_create
    assert post_create[0][2]["bootstrap_queue"] is False
    assert ("POST", "/api/admin/stations/42/bootstrap", None) in client.calls


def test_push_station_update_existing():
    client = _RecordingClient()

    def fake_request(method, path, payload=None):
        client.calls.append((method, path, payload))
        if method == "GET" and path == "/api/admin/deploy-check":
            return {"ok": True, "audiomuse": {"ok": True}, "navidrome": {"ok": True}}
        if method == "GET" and path == "/api/admin/stations":
            return [{"id": 7, "slug": "late-night-rock"}]
        if method == "PUT" and path == "/api/admin/stations/7":
            return {"id": 7, "slug": "late-night-rock"}
        if method == "POST" and path == "/api/admin/stations/7/bootstrap":
            return {"id": 7, "slug": "late-night-rock", "queued_count": 3}
        raise AssertionError(f"Unexpected request: {method} {path}")

    with patch.object(client, "_request", side_effect=fake_request):
        payload = bridge.channel_profile_to_alchemy_payload(_sample_profile(), [])
        payload["description"] = "Updated description"
        station, action, bootstrap_warning = client.push_station(payload, bootstrap=True)

    assert action == "updated"
    assert bootstrap_warning is None
    assert station["id"] == 7
    put_calls = [(method, path, body) for method, path, body in client.calls if method == "PUT"]
    assert put_calls
    assert put_calls[0][1] == "/api/admin/stations/7"
    assert put_calls[0][2]["description"] == "Updated description"
    assert "slug" not in put_calls[0][2]


def test_push_station_update_by_id_skips_station_list():
    client = _RecordingClient()
    with patch.object(
        client,
        "find_station_by_slug",
        side_effect=AssertionError("edit deploy should update by id without slug lookup"),
    ):
        payload = bridge.channel_profile_to_alchemy_payload(_sample_profile(), [])
        station, action, bootstrap_warning = client.push_station(
            payload,
            station_id=7,
            bootstrap=False,
        )

    assert action == "updated"
    assert station["id"] == 42
    assert bootstrap_warning is None


def test_push_station_bootstrap_failure_still_returns_station():
    client = _RecordingClient()

    def fake_request(method, path, payload=None):
        client.calls.append((method, path, payload))
        if method == "GET" and path == "/api/admin/deploy-check":
            return {"ok": True, "audiomuse": {"ok": True}, "navidrome": {"ok": True}}
        if method == "GET" and path == "/api/admin/stations":
            return []
        if method == "POST" and path == "/api/admin/stations":
            return {"id": 42, "slug": "late-night-rock", "name": "Late Night Rock"}
        if method == "POST" and path.endswith("/bootstrap"):
            raise bridge.ChannelDesignerError("AudioMuse rejected the API token (HTTP 401)")
        raise AssertionError(f"Unexpected request: {method} {path}")

    with patch.object(client, "_request", side_effect=fake_request):
        payload = bridge.channel_profile_to_alchemy_payload(_sample_profile(), [])
        station, action, bootstrap_warning = client.push_station(payload, bootstrap=True)

    assert action == "created"
    assert station["id"] == 42
    assert bootstrap_warning is not None
    assert "saved on Alchemy FM" in bootstrap_warning
    assert "401" in bootstrap_warning


def test_operator_client_actions_hit_admin_routes():
    client = _RecordingClient()
    client.refresh_queue(42)
    client.rebuild_m3u(42)
    client.delete_artwork(42)
    client.delete_station(42)

    paths = [path for _, path, _ in client.calls]
    assert "/api/admin/stations/42/refresh-queue" in paths
    assert "/api/admin/stations/42/rebuild-m3u" in paths
    assert "/api/admin/stations/42/artwork" in paths
    assert "/api/admin/stations/42" in paths
