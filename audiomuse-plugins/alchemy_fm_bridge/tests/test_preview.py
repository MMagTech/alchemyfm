"""Programming preview tests — mocked AudioMuse HTTP."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from bridge_loader import bridge, requires_postgres


@requires_postgres
class TestPreviewProgramming:
    def test_clap_query_returns_tracks(self, audiomuse_mocks):
        profile = {
            "programming": {"type": "clap_query", "query": "late night rock", "limit": 30},
        }
        tracks = bridge.preview_programming(profile)
        assert len(tracks) == 3
        assert tracks[0]["item_id"] == "clap-1"

    def test_lyrics_query_returns_tracks(self, audiomuse_mocks):
        profile = {
            "programming": {"type": "lyrics_query", "query": "open road", "limit": 30},
        }
        tracks = bridge.preview_programming(profile)
        assert len(tracks) == 2

    def test_mood_centroid_returns_tracks(self, audiomuse_mocks):
        profile = {
            "programming": {
                "type": "mood_centroid",
                "mood": "energetic",
                "centroid_index": 0,
                "limit": 30,
            },
        }
        tracks = bridge.preview_programming(profile)
        assert len(tracks) >= 1

    def test_mood_empty_cluster_raises(self, audiomuse_mocks):
        profile = {
            "programming": {
                "type": "mood_centroid",
                "mood": "energetic",
                "centroid_index": "",
                "limit": 30,
            },
        }
        with pytest.raises(bridge.ChannelDesignerError, match="mood and cluster"):
            bridge.preview_programming(profile)

    def test_alchemy_anchor_returns_tracks(self, audiomuse_mocks):
        profile = {
            "programming": {"type": "alchemy_anchor", "anchor_id": "42", "limit": 30},
        }
        tracks = bridge.preview_programming(profile)
        assert len(tracks) == 2

    def test_similar_seed_returns_tracks(self, audiomuse_mocks):
        profile = {
            "programming": {"type": "similar_seed", "seed_id": "seed-99", "limit": 30},
        }
        tracks = bridge.preview_programming(profile)
        assert len(tracks) == 2


@requires_postgres
class TestPreviewRoute:
    def test_preview_clap_redirects_and_persists(self, client, audiomuse_mocks, pg_db):
        resp = client.post(
            "/",
            data={
                "afm_action": "preview",
                "programming_type": "clap_query",
                "clap_query": "late night rock",
                "refresh_mode": "similar_to_last",
                "preview_limit": "30",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "preview_ok=1" in resp.headers.get("Location", "")

        loaded = bridge._load_saved_channel("late-night-rock")
        assert loaded is not None
        profile, preview_ids = loaded
        assert len(preview_ids) == 3
        assert profile["programming"]["type"] == "clap_query"

    def test_preview_without_channel_name(self, client, audiomuse_mocks, pg_db):
        resp = client.post(
            "/",
            data={
                "afm_action": "preview",
                "programming_type": "clap_query",
                "clap_query": "jazz piano",
                "refresh_mode": "similar_to_last",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302

    def test_preview_mood_validation_error_not_500(self, client, audiomuse_mocks, pg_db):
        resp = client.post(
            "/",
            data={
                "afm_action": "preview",
                "programming_type": "mood_centroid",
                "mood_name": "energetic",
                "centroid_index": "",
                "refresh_mode": "similar_to_last",
            },
        )
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "afm-flash-error" in body
        assert "mood and cluster" in body.lower() or "choose a mood" in body.lower()

    def test_preview_alchemy_anchor_from_search_name(self, client, audiomuse_mocks, pg_db):
        resp = client.post(
            "/",
            data={
                "afm_action": "preview",
                "name": "Pop Punk",
                "slug": "pop-punk",
                "programming_type": "alchemy_anchor",
                "anchor_search": "Test Anchor",
                "refresh_mode": "similar_to_last",
                "preview_limit": "30",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "preview_ok=1" in resp.headers.get("Location", "")

        loaded = bridge._load_saved_channel("pop-punk")
        assert loaded is not None
        profile, preview_ids = loaded
        assert profile["programming"]["anchor_id"] == "42"
        assert len(preview_ids) == 2

    def test_preview_anchor_error_scrolls_step2(self, client, audiomuse_mocks, pg_db):
        resp = client.post(
            "/",
            data={
                "afm_action": "preview",
                "programming_type": "alchemy_anchor",
                "anchor_search": "No Such Anchor",
                "refresh_mode": "similar_to_last",
            },
        )
        body = resp.get_data(as_text=True)
        assert "afm-flash-error" in body
        assert "step-programming" in body
        assert '"step-programming"' in body or "'step-programming'" in body

    def test_failed_preview_does_not_restore_stale_results(self, client, audiomuse_mocks, pg_db):
        # Seed a channel with old preview ids.
        profile = bridge.profile_from_form(
            {
                "name": "Stale Test",
                "programming_type": "clap_query",
                "clap_query": "old query",
                "refresh_mode": "similar_to_last",
            }
        )
        bridge._save_channel(profile, preview_ids=["stale-1", "stale-2"])

        resp = client.post(
            "/",
            data={
                "afm_action": "preview",
                "programming_type": "mood_centroid",
                "mood_name": "energetic",
                "centroid_index": "",
                "refresh_mode": "similar_to_last",
                "editing_slug": profile["station"]["slug"],
            },
        )
        body = resp.get_data(as_text=True)
        assert "stale-1" not in body or "No preview yet" in body


@requires_postgres
class TestChatPreviewAjax:
    """Regression coverage for the JS AJAX path in runChatPreviewAjax().

    That JS builds its own FormData and sets the dispatch field explicitly
    (formData.set('afm_action', 'chat_preview')) rather than relying on a
    submit button's name/value — the 3.0.8 action -> afm_action rename
    updated every button but missed this one manually-constructed request,
    silently breaking chat preview with zero test coverage to catch it.
    """

    def test_chat_preview_ajax_returns_json(self, client, pg_db):
        fake_response = {
            "response": {
                "query_results": [
                    {"item_id": "chat-1", "title": "Track One", "author": "Artist A"},
                    {"item_id": "chat-2", "title": "Track Two", "author": "Artist B"},
                ]
            }
        }
        with patch.object(bridge, "audiomuse_post", return_value=fake_response) as mock_post:
            resp = client.post(
                "/",
                data={
                    "afm_action": "chat_preview",
                    "afm_ajax": "chat_preview",
                    "chat_prompt": "upbeat 90s alt rock for a road trip",
                },
                headers={"X-AFM-Chat-Preview": "1"},
            )
        assert resp.status_code == 200
        assert resp.content_type.startswith("application/json")
        data = resp.get_json()
        assert data["ok"] is True
        assert data["track_count"] == 2
        mock_post.assert_called_once()
        assert mock_post.call_args[0][0] == "/chat/api/chatPlaylist"

    def test_chat_preview_ajax_wrong_dispatch_field_is_not_mistaken_for_success(self, client, pg_db):
        """Reproduces the exact 3.0.8 regression: JS sending the pre-rename
        field name ('action' instead of 'afm_action') must not look like a
        successful chat preview -- it should clearly not be JSON."""
        resp = client.post(
            "/",
            data={
                "action": "chat_preview",  # old, wrong field name
                "afm_ajax": "chat_preview",
                "chat_prompt": "upbeat 90s alt rock for a road trip",
            },
            headers={"X-AFM-Chat-Preview": "1"},
        )
        assert not resp.content_type.startswith("application/json")
