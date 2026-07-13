"""Regression test for retrofitting AUTOINCREMENT onto stations.id.

SQLite ROWIDs are reused after a delete unless the table was created with
AUTOINCREMENT. The AudioMuse plugin caches this backend's station id per
channel, so a reused id (after a delete, or a database restore) can silently
point cached plugin state at the wrong station. _migrate_db must detect an
existing stations table lacking AUTOINCREMENT and rebuild it in place,
preserving all rows (and related QueueItem/PlayHistory rows by station_id).
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import QueueItem, Station, _migrate_db, engine


def _create_sql(conn, name: str) -> str | None:
    return conn.execute(
        text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:name"),
        {"name": name},
    ).scalar()


def test_migrate_retrofits_autoincrement_and_preserves_data(db_session: Session) -> None:
    # Simulate a pre-existing installation: a stations table created WITHOUT
    # AUTOINCREMENT (as every station before this change would have been).
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS stations"))
        conn.execute(
            text(
                "CREATE TABLE stations ("
                "id INTEGER PRIMARY KEY, name TEXT, slug TEXT UNIQUE, "
                "description TEXT DEFAULT '', artwork_url TEXT DEFAULT '', "
                "icecast_mount TEXT, enabled BOOLEAN DEFAULT 1, "
                "featured BOOLEAN DEFAULT 0, featured_order INTEGER DEFAULT 0, "
                "sort_order INTEGER DEFAULT 0, "
                "source_type TEXT, source_ref TEXT, programming_json TEXT DEFAULT '', "
                "queue_target INTEGER DEFAULT 30, refresh_threshold INTEGER DEFAULT 10, "
                "artist_separation_minutes INTEGER DEFAULT 90, "
                "continuation_mode TEXT DEFAULT 'source_only', "
                "identity_seed_item_id TEXT DEFAULT '', identity_anchor_id TEXT DEFAULT '', "
                "source_last_error TEXT DEFAULT '', source_last_ok_at DATETIME, "
                "created_at DATETIME"
                ")"
            )
        )
        conn.execute(
            text(
                "INSERT INTO stations (id, name, slug, icecast_mount, source_type, source_ref) "
                "VALUES (1, 'Jazz', 'jazz', '/jazz', 'clap_query', 'smooth jazz'), "
                "(5, 'Rock', 'rock', '/rock', 'clap_query', 'rock')"
            )
        )
    db_session.add(
        QueueItem(
            station_id=5,
            item_id="track-1",
            title="Track One",
            artist="Someone",
            stream_url="http://x/track-1",
        )
    )
    db_session.commit()

    assert "AUTOINCREMENT" not in _create_sql(engine.connect(), "stations").upper()

    _migrate_db()

    with engine.connect() as conn:
        create_sql = _create_sql(conn, "stations")
        assert create_sql and "AUTOINCREMENT" in create_sql.upper()

        rows = conn.execute(text("SELECT id, name, slug FROM stations ORDER BY id")).all()
        assert [tuple(r) for r in rows] == [(1, "Jazz", "jazz"), (5, "Rock", "rock")]

        # Related row's station_id still points at the same preserved id.
        queued = conn.execute(
            text("SELECT station_id FROM queue_items WHERE item_id = 'track-1'")
        ).scalar()
        assert queued == 5

        # The rebuilt table's unique index on slug survived.
        idx = conn.execute(
            text(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='stations' AND sql IS NOT NULL"
            )
        ).fetchall()
        assert any("slug" in name.lower() for (name,) in idx)

    # Delete the highest-id row, then insert a new one -- id must NOT be reused.
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM stations WHERE id = 5"))
    station = Station(
        name="Metal",
        slug="metal",
        icecast_mount="/metal",
        source_type="clap_query",
        source_ref="metal",
    )
    db_session.add(station)
    db_session.commit()
    db_session.refresh(station)
    assert station.id == 6, "id 5 must not be reused after AUTOINCREMENT retrofit"


def test_migrate_is_noop_on_already_autoincrement_table(db_session: Session) -> None:
    # A fresh DB (created via Base.metadata.create_all with the current model)
    # already has AUTOINCREMENT -- re-running migrate must not touch it.
    with engine.connect() as conn:
        before = _create_sql(conn, "stations")
    assert before and "AUTOINCREMENT" in before.upper()

    _migrate_db()

    with engine.connect() as conn:
        after = _create_sql(conn, "stations")
    assert after == before
