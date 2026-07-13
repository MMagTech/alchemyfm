"""Regression test for the sort_order backfill in app.database._migrate_db.

sort_order was added via ALTER TABLE with a flat default of 0, so any station
that existed before the column was introduced starts out tied with every
other station at 0 -- swapping two tied values during a reorder is a no-op.
_migrate_db must detect an untouched, all-zero column and backfill distinct
values so reordering actually has something to change.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import Station, _migrate_db, engine


def test_migrate_backfills_tied_sort_order(db_session: Session) -> None:
    stations = [
        Station(
            name=name,
            slug=slug,
            icecast_mount=f"/{slug}",
            enabled=True,
            source_type="clap_query",
            source_ref="query",
            sort_order=0,
        )
        for name, slug in [
            ("Zeta", "zeta"),
            ("Alpha", "alpha"),
            ("Mu", "mu"),
        ]
    ]
    db_session.add_all(stations)
    db_session.commit()

    with engine.begin() as conn:
        conn.execute(text("UPDATE stations SET sort_order = 0"))

    _migrate_db()

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name, sort_order FROM stations ORDER BY sort_order ASC")
        ).all()

    assert [r[0] for r in rows] == ["Alpha", "Mu", "Zeta"]
    assert len({r[1] for r in rows}) == len(rows)
