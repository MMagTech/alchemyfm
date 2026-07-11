"""Operator heart API — must stay working after retired route cleanup."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch


def test_read_song_heart_route_removed(admin_client):
    """Prefetch GET was unused after fetchHearted() removal (PR 1)."""
    resp = admin_client.get("/api/admin/navidrome/songs/track-99")
    assert resp.status_code == 404


def test_set_song_heart_on(admin_client, db_session):
    from app.database import BroadcastSettings

    row = db_session.query(BroadcastSettings).filter(BroadcastSettings.id == 1).one()
    row.default_navidrome_playlist_id = "pl-hearts"
    db_session.commit()

    with patch(
        "app.routers.admin_navidrome.navidrome_client.heart_song_with_playlist",
        new=AsyncMock(return_value=True),
    ) as heart_mock:
        resp = admin_client.post(
            "/api/admin/navidrome/songs/track-42/heart",
            json={"hearted": True},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data == {
        "hearted": True,
        "playlist_added": True,
        "playlist_configured": True,
    }
    heart_mock.assert_awaited_once_with("track-42", "pl-hearts")


def test_set_song_heart_off(admin_client):
    with patch(
        "app.routers.admin_navidrome.navidrome_client.unstar_song",
        new=AsyncMock(),
    ) as unstar_mock:
        resp = admin_client.post(
            "/api/admin/navidrome/songs/track-42/heart",
            json={"hearted": False},
        )

    assert resp.status_code == 200
    assert resp.json() == {"hearted": False, "playlist_added": None, "playlist_configured": True}
    unstar_mock.assert_awaited_once_with("track-42")


def test_set_song_heart_requires_auth(client):
    resp = client.post(
        "/api/admin/navidrome/songs/track-42/heart",
        json={"hearted": True},
    )
    assert resp.status_code == 401
