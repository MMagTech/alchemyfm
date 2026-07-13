"""Back up radio.db, knowledge.db (if present), and icecast.xml.

SQLite databases are backed up via the online backup API (sqlite3's
Connection.backup) rather than a plain file copy -- radio.db is written to
continuously while the app runs, and a naive copy risks capturing a
partial/torn write. The backup API produces a consistent snapshot without
requiring writers to pause.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

BACKUP_FILENAME_PREFIX = "alchemyfm-backup-"


def backup_dir() -> Path:
    path = Path(settings.backup_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _sqlite_path_from_url(url: str) -> Path | None:
    if not url.startswith("sqlite:///"):
        return None
    return Path(url[len("sqlite:///") :])


def _backup_sqlite_file(source_path: Path, dest_path: Path) -> None:
    source_conn = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
    try:
        dest_conn = sqlite3.connect(dest_path)
        try:
            source_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        source_conn.close()


def create_backup() -> Path:
    """Write a timestamped zip with whichever of radio.db / knowledge.db /
    icecast.xml currently exist. Raises RuntimeError if none are found."""
    # Microsecond resolution so two backups triggered within the same second
    # (e.g. a manual download right as the scheduled backup fires) never
    # silently overwrite one another.
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    zip_path = backup_dir() / f"{BACKUP_FILENAME_PREFIX}{timestamp}.zip"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        entries: list[tuple[Path, str]] = []

        radio_db_path = _sqlite_path_from_url(settings.database_url)
        if radio_db_path and radio_db_path.exists():
            dest = tmp_dir / "radio.db"
            _backup_sqlite_file(radio_db_path, dest)
            entries.append((dest, "radio.db"))

        knowledge_db_path = _sqlite_path_from_url(settings.knowledge_database_url)
        if knowledge_db_path and knowledge_db_path.exists():
            dest = tmp_dir / "knowledge.db"
            _backup_sqlite_file(knowledge_db_path, dest)
            entries.append((dest, "knowledge.db"))

        icecast_xml = Path(settings.data_dir) / "icecast" / "icecast.xml"
        if icecast_xml.exists():
            dest = tmp_dir / "icecast.xml"
            shutil.copy2(icecast_xml, dest)
            entries.append((dest, "icecast.xml"))

        if not entries:
            raise RuntimeError("Nothing found to back up (no radio.db, knowledge.db, or icecast.xml).")

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for src, arcname in entries:
                zf.write(src, arcname)

    logger.info("Created backup %s (%s)", zip_path.name, ", ".join(a for _, a in entries))
    return zip_path


def list_backups() -> list[Path]:
    """Oldest first."""
    return sorted(backup_dir().glob(f"{BACKUP_FILENAME_PREFIX}*.zip"), key=lambda p: p.name)


def latest_backup_path() -> Path | None:
    backups = list_backups()
    return backups[-1] if backups else None


def prune_old_backups(keep_count: int) -> list[Path]:
    """Delete the oldest backups beyond keep_count. Returns the deleted paths."""
    keep_count = max(1, keep_count)
    backups = list_backups()
    to_delete = backups[: max(0, len(backups) - keep_count)]
    for path in to_delete:
        path.unlink(missing_ok=True)
        logger.info("Pruned old backup %s", path.name)
    return to_delete
