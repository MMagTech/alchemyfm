"""PlayHistory grows forever otherwise -- one row per track played, on every
station, all day. prune_old_play_history is the only thing that ever
deletes from it, so these tests pin down its actual cutoff behavior.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.database import PlayHistory
from app.services.queue import prune_old_play_history


def _add_play(db_session, station, *, hours_ago: float, item_id: str = "item") -> None:
    db_session.add(
        PlayHistory(
            station_id=station.id,
            item_id=item_id,
            title="Some Song",
            artist="Some Artist",
            played_at=datetime.utcnow() - timedelta(hours=hours_ago),
        )
    )
    db_session.commit()


def test_prune_deletes_rows_older_than_cutoff_keeps_newer(sample_station, db_session):
    _add_play(db_session, sample_station, hours_ago=25, item_id="old")
    _add_play(db_session, sample_station, hours_ago=23, item_id="recent")
    _add_play(db_session, sample_station, hours_ago=0.1, item_id="just-played")

    deleted = prune_old_play_history(db_session, older_than_hours=24)

    assert deleted == 1
    remaining = {
        row.item_id
        for row in db_session.query(PlayHistory).filter(PlayHistory.station_id == sample_station.id).all()
    }
    assert remaining == {"recent", "just-played"}


def test_prune_returns_zero_when_nothing_is_old_enough(sample_station, db_session):
    _add_play(db_session, sample_station, hours_ago=1)
    _add_play(db_session, sample_station, hours_ago=5)

    deleted = prune_old_play_history(db_session, older_than_hours=24)

    assert deleted == 0
    assert db_session.query(PlayHistory).filter(PlayHistory.station_id == sample_station.id).count() == 2


def test_prune_respects_custom_cutoff(sample_station, db_session):
    _add_play(db_session, sample_station, hours_ago=2)
    _add_play(db_session, sample_station, hours_ago=0.5)

    deleted = prune_old_play_history(db_session, older_than_hours=1)

    assert deleted == 1
    remaining = db_session.query(PlayHistory).filter(PlayHistory.station_id == sample_station.id).all()
    assert len(remaining) == 1
    assert remaining[0].played_at > datetime.utcnow() - timedelta(hours=1)
