"""Retired admin proxy routes — Channel Designer uses AudioMuse directly."""

from __future__ import annotations


def test_audiomuse_admin_proxy_routes_removed(admin_client):
    for path in (
        "/api/admin/audiomuse/anchors",
        "/api/admin/audiomuse/search_tracks?q=jazz",
        "/api/admin/audiomuse/track?item_id=abc",
    ):
        resp = admin_client.get(path)
        assert resp.status_code == 404, path
