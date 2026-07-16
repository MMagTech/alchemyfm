"""Exercises the real extend_queue/collect_refill_candidates session-juggling
logic directly (bypassing the autouse _mock_extend_queue fixture, which only
patches the app.services.queue module attribute -- a name already bound to
the real function via direct import is unaffected).

The point of these tests is to prove the fix for the connection-pool-freeze
bug: each network-bound tier commits, closes, and reopens the DB session
around its external call, and callers must keep using the returned
(db, station). These tests verify that dance actually preserves queued
writes and hands back a usable session, not just that the code runs.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.database import QueueItem, QueueItemStatus, SessionLocal, StationPoolItem
from app.schemas import TrackInfo, TrackRef
from app.services.queue import bootstrap_station as real_bootstrap_station
from app.services.queue import extend_queue as real_extend_queue


async def test_extend_queue_survives_session_cycling_and_adds_tracks(sample_station, db_session):
    """Real extend_queue call: tier 0's network await forces a close/reopen
    of the DB session (and station gets re-merged into it) before the
    enrich_tracks await triggers a second one. Both the pool import and the
    final QueueItem inserts must survive both cycles."""
    batch = [
        TrackRef(item_id="refill-1", title="Song One", artist="Artist A"),
        TrackRef(item_id="refill-2", title="Song Two", artist="Artist B"),
    ]
    enriched = [
        TrackInfo(item_id="refill-1", title="Song One", artist="Artist A", duration_sec=200),
        TrackInfo(item_id="refill-2", title="Song Two", artist="Artist B", duration_sec=210),
    ]

    expected_slug = sample_station.slug
    expected_id = sample_station.id

    with (
        patch("app.services.refill.fetch_programming_batch", new=AsyncMock(return_value=batch)),
        patch("app.services.navidrome.navidrome_client.enrich_tracks", new=AsyncMock(return_value=enriched)),
    ):
        added, returned_db, returned_station = await real_extend_queue(db_session, sample_station, 2)

    # extend_queue's internal commit/close cycling expires and detaches the
    # original sample_station/db_session objects (expire_on_commit is on by
    # default) -- exactly why callers must switch to the returned
    # (db, station) instead of continuing to use what they passed in.
    assert added == 2
    assert returned_station.id == expected_id
    assert returned_station.slug == expected_slug

    # The returned session must be live and usable, not the closed original.
    pool_rows = (
        returned_db.query(StationPoolItem)
        .filter(StationPoolItem.station_id == returned_station.id)
        .all()
    )
    assert {r.item_id for r in pool_rows} == {"refill-1", "refill-2"}

    queued = (
        returned_db.query(QueueItem)
        .filter(
            QueueItem.station_id == returned_station.id,
            QueueItem.status == QueueItemStatus.queued,
        )
        .all()
    )
    assert {q.item_id for q in queued} == {"refill-1", "refill-2"}
    assert {q.duration_sec for q in queued} == {200, 210}

    returned_db.close()


async def test_extend_queue_disabled_station_returns_original_session(sample_station, db_session):
    """A disabled station returns immediately without touching the network
    at all -- db/station should come back unchanged."""
    sample_station.enabled = False
    db_session.commit()

    added, returned_db, returned_station = await real_extend_queue(db_session, sample_station, 5)

    assert added == 0
    assert returned_db is db_session
    assert returned_station is sample_station


async def test_extend_queue_closes_reopened_session_on_enrich_failure(sample_station, db_session):
    """If enrich_tracks blows up after the pre-enrich commit/close cycle,
    extend_queue must close the session it reopened for that call before
    re-raising -- otherwise the caller's own (now-stale) db reference can
    never reach it and the connection leaks, silently recreating the
    freeze bug this refactor exists to fix."""
    batch = [TrackRef(item_id="refill-3", title="Song Three", artist="Artist C")]
    opened_sessions = []

    def _tracking_session_local():
        session = SessionLocal()
        opened_sessions.append(session)
        return session

    with (
        patch("app.services.refill.fetch_programming_batch", new=AsyncMock(return_value=batch)),
        patch(
            "app.services.navidrome.navidrome_client.enrich_tracks",
            new=AsyncMock(side_effect=RuntimeError("navidrome down")),
        ),
        patch("app.services.queue.SessionLocal", side_effect=_tracking_session_local),
    ):
        with pytest.raises(RuntimeError, match="navidrome down"):
            await real_extend_queue(db_session, sample_station, 2)

    assert opened_sessions, "expected extend_queue to reopen a session before the failing call"
    # A closed SQLAlchemy session has no active transaction left to commit against.
    for session in opened_sessions:
        assert not session.in_transaction()


async def test_bootstrap_station_survives_session_cycling_and_creates_queue(sample_station, db_session):
    """Real bootstrap_station call: both the fetch_bootstrap_batch and
    enrich_tracks awaits force a close/reopen of the DB session (with
    station re-merged in), exactly like extend_queue. The resulting pool
    import and queue rows must survive both cycles, and the caller must
    get back a usable, attached session."""
    batch = [
        TrackRef(item_id="boot-1", title="Boot One", artist="Artist A"),
        TrackRef(item_id="boot-2", title="Boot Two", artist="Artist B"),
    ]
    enriched = [
        TrackInfo(item_id="boot-1", title="Boot One", artist="Artist A", duration_sec=180),
        TrackInfo(item_id="boot-2", title="Boot Two", artist="Artist B", duration_sec=190),
    ]

    expected_slug = sample_station.slug
    expected_id = sample_station.id

    with (
        patch("app.services.queue.fetch_bootstrap_batch", new=AsyncMock(return_value=batch)),
        patch("app.services.navidrome.navidrome_client.enrich_tracks", new=AsyncMock(return_value=enriched)),
    ):
        returned_db, returned_station = await real_bootstrap_station(db_session, sample_station)

    assert returned_station.id == expected_id
    assert returned_station.slug == expected_slug

    pool_rows = (
        returned_db.query(StationPoolItem)
        .filter(StationPoolItem.station_id == returned_station.id)
        .all()
    )
    assert {r.item_id for r in pool_rows} == {"boot-1", "boot-2"}

    queued = (
        returned_db.query(QueueItem)
        .filter(
            QueueItem.station_id == returned_station.id,
            QueueItem.status == QueueItemStatus.queued,
        )
        .all()
    )
    assert {q.item_id for q in queued} == {"boot-1", "boot-2"}

    returned_db.close()


async def test_bootstrap_station_closes_reopened_session_on_empty_batch(sample_station, db_session):
    """An empty fetch_bootstrap_batch raises ValueError *after* the session
    has already been reopened for that call -- bootstrap_station must close
    that reopened session itself before propagating, since the caller's
    stale db reference can't reach it."""
    with patch("app.services.queue.fetch_bootstrap_batch", new=AsyncMock(return_value=[])):
        with pytest.raises(ValueError, match="No tracks imported"):
            await real_bootstrap_station(db_session, sample_station)
