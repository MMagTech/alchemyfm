"""Programming preview tests — mocked AudioMuse HTTP."""

from __future__ import annotations

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
                "action": "preview",
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
                "action": "preview",
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
                "action": "preview",
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
                "action": "preview",
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
                "action": "preview",
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
                "action": "preview",
                "programming_type": "mood_centroid",
                "mood_name": "energetic",
                "centroid_index": "",
                "refresh_mode": "similar_to_last",
                "editing_slug": profile["station"]["slug"],
            },
        )
        body = resp.get_data(as_text=True)
        assert "stale-1" not in body or "No preview yet" in body
