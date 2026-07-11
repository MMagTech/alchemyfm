"""Pure helper tests — no database or HTTP required."""

from __future__ import annotations

import pytest

from bridge_loader import bridge


class TestTrackRowsFromResults:
    def test_bare_list(self):
        rows = bridge._track_rows_from_results(
            [{"item_id": "a", "title": "A", "author": "X"}]
        )
        assert len(rows) == 1
        assert rows[0]["item_id"] == "a"

    def test_results_wrapper(self):
        rows = bridge._track_rows_from_results(
            {"results": [{"item_id": "b", "title": "B", "artist": "Y"}]}
        )
        assert rows[0]["author"] == "Y"

    def test_empty_dict(self):
        assert bridge._track_rows_from_results({}) == []


class TestParseCentroidIndex:
    def test_none_and_empty(self):
        assert bridge._parse_centroid_index(None) is None
        assert bridge._parse_centroid_index("") is None
        assert bridge._parse_centroid_index("   ") is None

    def test_valid(self):
        assert bridge._parse_centroid_index("3") == 3
        assert bridge._parse_centroid_index(2) == 2


class TestProfileFromForm:
    def test_clap_query(self):
        profile = bridge.profile_from_form(
            {
                "programming_type": "clap_query",
                "clap_query": "late night rock",
                "refresh_mode": "similar_to_last",
            }
        )
        assert profile["programming"]["type"] == "clap_query"
        assert profile["programming"]["query"] == "late night rock"

    def test_mood_missing_cluster_raises(self):
        with pytest.raises(bridge.ChannelDesignerError, match="mood and cluster"):
            bridge.profile_from_form(
                {
                    "programming_type": "mood_centroid",
                    "mood_name": "energetic",
                    "centroid_index": "",
                    "refresh_mode": "similar_to_last",
                }
            )

    def test_anchor_without_pick_raises(self):
        with pytest.raises(bridge.ChannelDesignerError, match="Choose a Song Alchemy anchor"):
            bridge.profile_from_form(
                {
                    "programming_type": "alchemy_anchor",
                    "anchor_id": "",
                    "refresh_mode": "similar_to_last",
                }
            )

    def test_anchor_resolves_from_search_name(self, monkeypatch):
        monkeypatch.setattr(
            bridge,
            "_anchors",
            lambda: [{"id": 42, "name": "Pop Punk"}],
        )
        profile = bridge.profile_from_form(
            {
                "programming_type": "alchemy_anchor",
                "anchor_search": "Pop Punk",
                "refresh_mode": "similar_to_last",
            }
        )
        assert profile["programming"]["anchor_id"] == "42"


class TestFlashQueue:
    def test_step_programming_anchor_registered(self):
        queue = bridge._FlashQueue()
        queue.add("oops", "error", anchor="step-programming")
        assert "afm-flash-error" in queue.html_for("step-programming")


class TestPreviewEmptyMessage:
    def test_api_empty_vs_filters(self):
        profile = {
            "programming": {"type": "clap_query"},
            "filters": {"tempo_min": 200.0},
        }
        unfiltered = [{"item_id": "1", "tempo": 120, "energy": 0.5}]
        msg = bridge._preview_empty_message(unfiltered, profile)
        assert "Filters removed all" in msg

        msg2 = bridge._preview_empty_message([], profile)
        assert "sonic vibe" in msg2.lower()
