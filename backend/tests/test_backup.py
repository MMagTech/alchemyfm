"""Backup service: valid snapshots, retention pruning, and safety under
concurrent writes (radio.db is written to continuously while the app runs,
so a naive file copy could capture a torn/inconsistent state)."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from app.config import settings
from app.services.backup import (
    _sqlite_path_from_url,
    create_backup,
    latest_backup_path,
    list_backups,
    prune_old_backups,
)


@pytest.fixture(autouse=True)
def _clean_backup_dir():
    import shutil

    path = Path(settings.backup_dir)
    if path.exists():
        shutil.rmtree(path)
    yield
    if path.exists():
        shutil.rmtree(path)


def test_sqlite_path_from_url_parses_database_url():
    path = _sqlite_path_from_url(settings.database_url)
    assert path is not None
    assert path.name == "alchemyfm_pytest.db"
    assert path.exists()


def test_create_backup_contains_radio_db_and_is_openable(sample_station):
    zip_path = create_backup()
    assert zip_path.exists()

    import zipfile

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        assert "radio.db" in names

        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            extracted = Path(tmp) / "radio.db"
            extracted.write_bytes(zf.read("radio.db"))

            conn = sqlite3.connect(extracted)
            try:
                row = conn.execute(
                    "SELECT name FROM stations WHERE slug = ?", (sample_station.slug,)
                ).fetchone()
            finally:
                conn.close()
            assert row is not None
            assert row[0] == sample_station.name


def test_prune_old_backups_keeps_only_most_recent():
    paths = [create_backup() for _ in range(5)]

    assert len(list_backups()) == 5
    deleted = prune_old_backups(keep_count=2)
    remaining = list_backups()
    assert len(remaining) == 2
    assert len(deleted) == 3
    # The two kept are the two most recently created.
    assert [p.name for p in remaining] == [paths[3].name, paths[4].name]


def test_latest_backup_path_returns_most_recent():
    assert latest_backup_path() is None
    first = create_backup()
    assert latest_backup_path() == first
    second = create_backup()
    assert second is not None
    assert second != first


def test_backup_is_consistent_under_concurrent_writes(sample_station):
    """The online backup API must not capture a torn/corrupted snapshot
    while another connection is actively writing to the same database file."""
    db_path = _sqlite_path_from_url(settings.database_url)
    stop = threading.Event()
    errors: list[Exception] = []

    def writer():
        conn = sqlite3.connect(db_path, timeout=5)
        try:
            i = 0
            while not stop.is_set():
                try:
                    conn.execute(
                        "UPDATE stations SET description = ? WHERE id = ?",
                        (f"desc-{i}", sample_station.id),
                    )
                    conn.commit()
                    i += 1
                except sqlite3.OperationalError:
                    pass  # transient lock contention is fine, just retry
        except Exception as exc:  # pragma: no cover - defensive
            errors.append(exc)
        finally:
            conn.close()

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    try:
        zip_path = create_backup()
    finally:
        stop.set()
        thread.join(timeout=5)

    assert not errors

    import zipfile

    with zipfile.ZipFile(zip_path) as zf:
        data = zf.read("radio.db")

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        extracted = Path(tmp) / "radio.db"
        extracted.write_bytes(data)
        conn = sqlite3.connect(extracted)
        try:
            # integrity_check is SQLite's own corruption detector.
            result = conn.execute("PRAGMA integrity_check").fetchone()
            assert result == ("ok",)
            row = conn.execute(
                "SELECT description FROM stations WHERE id = ?", (sample_station.id,)
            ).fetchone()
            assert row is not None
        finally:
            conn.close()
