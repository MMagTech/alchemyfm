"""Deploy route tests — plugin form POST action=push (not just AlchemyFmClient mocks)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from bridge_loader import bridge, requires_postgres


def _push_form(**overrides: Any) -> dict[str, Any]:
    data = {
        "action": "push",
        "name": "Deploy Route FM",
        "slug": "deploy-route-fm",
        "description": "Route integration test",
        "icecast_mount": "/deploy-route-fm",
        "programming_type": "clap_query",
        "clap_query": "late night rock",
        "refresh_mode": "similar_to_last",
        "preview_limit": "30",
        "bootstrap_queue": "on",
        "enabled": "on",
        "queue_target": "30",
        "refresh_threshold": "10",
        "artist_separation_minutes": "90",
    }
    data.update(overrides)
    return data


@requires_postgres
class TestDeployRoute:
    def test_push_deploy_success(self, client, audiomuse_mocks, pg_db):
        captured: dict[str, Any] = {}

        def fake_push(payload, slug=None, station_id=None, create_new=False, bootstrap=True):
            captured["payload"] = payload
            captured["bootstrap"] = bootstrap
            captured["create_new"] = create_new
            return (
                {
                    "id": 99,
                    "name": payload["name"],
                    "slug": payload["slug"],
                    "enabled": True,
                    "queued_count": 5,
                },
                "created",
                None,
            )

        mock_client = patch.object(bridge, "_client")
        with mock_client as client_factory:
            alchemy = client_factory.return_value
            alchemy.push_station.side_effect = fake_push

            resp = client.post("/", data=_push_form(), follow_redirects=True)

        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Deploy Route FM" in body
        assert "created on Alchemy FM" in body
        assert captured["payload"]["source_type"] == "clap_query"
        assert captured["payload"]["source_ref"] == "late night rock"
        assert captured["bootstrap"] is True
        assert captured["create_new"] is True

        loaded = bridge._load_saved_channel("deploy-route-fm")
        assert loaded is not None
        profile, preview_ids = loaded
        assert len(preview_ids) == 3
        assert profile["programming"]["type"] == "clap_query"

    def test_push_deploy_requires_channel_name(self, client, audiomuse_mocks, pg_db):
        resp = client.post(
            "/",
            data=_push_form(name="", slug="", clap_query="late night rock"),
        )
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "afm-flash-error" in body
        assert "channel name is required" in body.lower()

    def test_push_deploy_requires_tracks(self, client, audiomuse_mocks, pg_db):
        mock_client = patch.object(bridge, "_client")
        with mock_client as client_factory:
            client_factory.return_value.verify_deploy_ready.return_value = None
            with patch.object(bridge, "preview_programming", return_value=[]):
                resp = client.post("/", data=_push_form())
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "afm-flash-error" in body
        assert "no tracks to deploy" in body.lower()

    def test_push_deploy_surfaces_alchemy_error(self, client, audiomuse_mocks, pg_db):
        mock_client = patch.object(bridge, "_client")
        with mock_client as client_factory:
            client_factory.return_value.push_station.side_effect = bridge.ChannelDesignerError(
                "Alchemy FM returned HTTP 502 (bootstrap failed)"
            )
            resp = client.post("/", data=_push_form())

        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "502" in body or "bootstrap failed" in body.lower()
        assert bridge._channel_last_error("deploy-route-fm")

    def test_push_deploy_uses_saved_preview_when_form_empty(self, client, audiomuse_mocks, pg_db):
        slug = "saved-preview-fm"
        profile = {
            "programming": {"type": "clap_query", "query": "late night rock", "limit": 30},
            "refresh": {"mode": "similar_to_last"},
            "filters": {},
            "living": {"enabled": False},
            "bootstrap": {},
            "station": {
                "name": "Saved Preview FM",
                "slug": slug,
                "description": "",
                "icecast_mount": f"/{slug}",
                "enabled": True,
                "bootstrap_queue": True,
                "queue_target": 30,
                "refresh_threshold": 10,
                "artist_separation_minutes": 90,
            },
        }
        bridge._save_channel(
            profile,
            preview_ids=["t1", "t2", "t3"],
            unfiltered_preview_ids=["t1", "t2", "t3"],
        )

        mock_client = patch.object(bridge, "_client")
        with mock_client as client_factory:
            alchemy = client_factory.return_value
            alchemy.verify_deploy_ready.return_value = None
            alchemy.push_station.return_value = (
                {"id": 1, "name": "Saved Preview FM", "slug": slug, "enabled": True, "queued_count": 3},
                "created",
                None,
            )
            with patch.object(bridge, "preview_programming") as preview_mock:
                preview_mock.side_effect = AssertionError("deploy should use saved preview, not re-query")
                resp = client.post(
                    "/",
                    data=_push_form(
                        name="Saved Preview FM",
                        slug=slug,
                        clap_query="",
                        editing_slug=slug,
                    ),
                    follow_redirects=True,
                )

        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "created on Alchemy FM" in body
        alchemy.push_station.assert_called_once()

    def test_stale_deploy_error_not_shown_on_delete(self, client, audiomuse_mocks, pg_db):
        client.post("/", data=_push_form(name="", slug=""))
        mock_client = patch.object(bridge, "_client")
        with mock_client as client_factory:
            alchemy = client_factory.return_value
            alchemy.delete_station.return_value = None
            resp = client.post(
                "/",
                data={
                    "action": "delete",
                    "station_id": "1",
                    "delete_slug": "some-other-station",
                },
            )
        body = resp.get_data(as_text=True)
        # A blank designer form legitimately shows a "Channel name is required
        # before deploy" Step 6 readiness blocker, so assert on the specific
        # stale-flash marker instead of the raw substring (which collides with
        # that unrelated, always-present checklist item).
        assert "Last deploy failed" not in body
        assert 'id="afm-deploy-error-pinned"' not in body

    def test_push_deploy_partial_success_when_bootstrap_fails(self, client, audiomuse_mocks, pg_db):
        mock_client = patch.object(bridge, "_client")
        with mock_client as client_factory:
            alchemy = client_factory.return_value
            alchemy.verify_deploy_ready.return_value = None
            alchemy.push_station.return_value = (
                {"id": 9, "name": "Deploy Route FM", "slug": "deploy-route-fm", "queued_count": 0},
                "created",
                "Station 'deploy-route-fm' was saved on Alchemy FM (id 9), but the play queue could not be filled.",
            )
            resp = client.post("/", data=_push_form())

        assert resp.status_code == 302
        assert "deploy_ok=1" in resp.headers.get("Location", "")
        follow = client.get(resp.headers["Location"])
        body = follow.get_data(as_text=True)
        assert "created on Alchemy FM" in body
        assert "play queue could not be filled" in body
        assert "Deploy failed — fix this before trying again" not in body
