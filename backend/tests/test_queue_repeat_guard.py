"""Liquidsoap consumes queue.m3u with reload_mode="watch" and mode="normal":
every rewrite reloads the playlist, and a reload can reset the cursor to the
top of the file. When the on-air track was still written at the head, that
reset made Liquidsoap pull the same track again at song end -- an audible
back-to-back repeat that never shows in play history (the duplicate
track-start callback is deduped). These tests pin the two guards added after
catching one live: the playing item never enters the m3u, and Recently
Played skips the row for the track still on air.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.database import PlayHistory, QueueItem, QueueItemStatus
from app.services.queue import (
    get_recently_played,
    queue_m3u_path,
    rebuild_m3u_from_db,
)


def _queue_item(station, *, item_id, title, artist, status, position):
    return QueueItem(
        station_id=station.id,
        item_id=item_id,
        title=title,
        artist=artist,
        duration_sec=200,
        status=status,
        position=position,
        stream_url=f"http://navidrome.test/stream/{item_id}",
    )


def _history_row(station, *, item_id, title, artist, minutes_ago):
    return PlayHistory(
        station_id=station.id,
        item_id=item_id,
        title=title,
        artist=artist,
        played_at=datetime.utcnow() - timedelta(minutes=minutes_ago),
    )


def test_rebuild_m3u_excludes_the_playing_track(sample_station, db_session):
    db_session.add(_queue_item(
        sample_station, item_id="on-air", title="On Air Song", artist="Artist A",
        status=QueueItemStatus.playing, position=1,
    ))
    db_session.add(_queue_item(
        sample_station, item_id="next-up", title="Next Up Song", artist="Artist B",
        status=QueueItemStatus.queued, position=2,
    ))
    db_session.commit()

    rebuild_m3u_from_db(db_session, sample_station)

    body = queue_m3u_path(sample_station.slug).read_text(encoding="utf-8")
    assert "next-up" in body
    assert "Next Up Song" in body
    assert "on-air" not in body
    assert "On Air Song" not in body


def test_recently_played_skips_the_track_still_on_air(sample_station, db_session):
    db_session.add(_queue_item(
        sample_station, item_id="on-air", title="On Air Song", artist="Artist A",
        status=QueueItemStatus.playing, position=1,
    ))
    # History is written at track start, so the newest row IS the on-air track.
    db_session.add(_history_row(
        sample_station, item_id="on-air", title="On Air Song", artist="Artist A",
        minutes_ago=1,
    ))
    db_session.add(_history_row(
        sample_station, item_id="finished", title="Finished Song", artist="Artist B",
        minutes_ago=5,
    ))
    db_session.commit()

    result = get_recently_played(db_session, sample_station)

    ids = [t.item_id for t in result]
    assert "on-air" not in ids
    assert ids == ["finished"]


def test_recently_played_keeps_an_earlier_play_of_the_same_track(sample_station, db_session):
    """Only the newest row is skipped -- a genuine earlier airing of the same
    track (the repeat we want operators to be able to SEE) stays listed."""
    db_session.add(_queue_item(
        sample_station, item_id="on-air", title="On Air Song", artist="Artist A",
        status=QueueItemStatus.playing, position=1,
    ))
    db_session.add(_history_row(
        sample_station, item_id="on-air", title="On Air Song", artist="Artist A",
        minutes_ago=1,
    ))
    db_session.add(_history_row(
        sample_station, item_id="on-air", title="On Air Song", artist="Artist A",
        minutes_ago=9,
    ))
    db_session.commit()

    result = get_recently_played(db_session, sample_station)

    ids = [t.item_id for t in result]
    assert ids == ["on-air"]


def test_recently_played_untouched_when_nothing_is_playing(sample_station, db_session):
    db_session.add(_history_row(
        sample_station, item_id="latest", title="Latest Song", artist="Artist A",
        minutes_ago=1,
    ))
    db_session.add(_history_row(
        sample_station, item_id="older", title="Older Song", artist="Artist B",
        minutes_ago=5,
    ))
    db_session.commit()

    result = get_recently_played(db_session, sample_station)

    assert [t.item_id for t in result] == ["latest", "older"]
