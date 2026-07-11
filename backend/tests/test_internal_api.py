"""Liquidsoap internal callback tests."""

from __future__ import annotations

from app.auth import require_internal_client
from app.config import settings
from app.main import app


def test_track_started_rejects_bad_secret(internal_client, sample_station):
    resp = internal_client.post(
        f"/internal/stations/{sample_station.slug}/track-started",
        params={"secret": "wrong-secret", "artist": "Band", "title": "Song"},
    )
    assert resp.status_code == 403


def test_track_started_updates_queue(internal_client, sample_station, db_session):
    from app.database import QueueItem, QueueItemStatus

    db_session.add(
        QueueItem(
            station_id=sample_station.id,
            item_id="track-1",
            title="First Song",
            artist="Test Band",
            duration_sec=240,
            status=QueueItemStatus.queued,
            position=1,
            stream_url="http://navidrome.test/stream/track-1",
        )
    )
    db_session.commit()

    resp = internal_client.post(
        f"/internal/stations/{sample_station.slug}/track-started",
        params={
            "secret": settings.liquidsoap_callback_secret,
            "artist": "Test Band",
            "title": "First Song",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    playing = (
        db_session.query(QueueItem)
        .filter(
            QueueItem.station_id == sample_station.id,
            QueueItem.status == QueueItemStatus.playing,
        )
        .one()
    )
    assert playing.item_id == "track-1"


def test_track_started_unknown_station(internal_client):
    resp = internal_client.post(
        "/internal/stations/no-such-station/track-started",
        params={"secret": settings.liquidsoap_callback_secret, "artist": "A", "title": "B"},
    )
    assert resp.status_code == 404


def test_internal_routes_reject_untrusted_client(client, sample_station):
    """When restrict_internal_routes is on, non-RFC1918 clients are blocked."""
    assert app.dependency_overrides.get(require_internal_client) is None
    resp = client.post(
        f"/internal/stations/{sample_station.slug}/track-started",
        params={"secret": settings.liquidsoap_callback_secret, "artist": "A", "title": "B"},
    )
    assert resp.status_code == 403
