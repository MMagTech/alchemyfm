"""Log file listing, tailing, download, clear, and rotation for the admin
Logs page. Covers backend.log (already rotated in place by main.py's
RotatingFileHandler), Icecast's access.log/error.log (rotated natively by
Icecast itself via <logsize>), and liquidsoap.log (no native rotation --
rotated here on the same size-capped-with-backups policy as the others)."""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path

from app.config import settings

# name -> filename under settings.log_dir
LOG_FILES = {
    "backend": "backend.log",
    "liquidsoap": "liquidsoap.log",
    "icecast-access": "access.log",
    "icecast-error": "error.log",
}

LIQUIDSOAP_LOG_MAX_BYTES = 5_000_000  # match backend.log's ~5MB-per-file cap
LIQUIDSOAP_LOG_BACKUP_COUNT = 3


def log_path(name: str) -> Path:
    if name not in LOG_FILES:
        raise KeyError(name)
    return Path(settings.log_dir) / LOG_FILES[name]


def list_logs() -> list[dict]:
    entries = []
    for name in LOG_FILES:
        path = log_path(name)
        entries.append({
            "name": name,
            "size": path.stat().st_size if path.exists() else 0,
            "exists": path.exists(),
        })
    return entries


def tail_log(name: str, lines: int = 200) -> str:
    path = log_path(name)
    if not path.exists():
        return ""
    # Simple tail: fine for a UI viewer on files capped at a few MB -- no
    # need for a chunked reverse-read here.
    with path.open("r", encoding="utf-8", errors="replace") as f:
        return "".join(f.readlines()[-lines:])


def clear_log(name: str) -> None:
    """Truncate in place rather than delete-and-recreate, since a running
    process (backend/Liquidsoap/Icecast) may already have the file open --
    deleting it would leave that process writing to an unlinked inode that
    never shows up again until restart."""
    path = log_path(name)
    if path.exists():
        with path.open("w", encoding="utf-8"):
            pass


def create_logs_zip() -> Path:
    """Bundle every log file that currently exists into one timestamped zip
    in a temp dir -- mirrors create_backup()'s approach, but the caller is
    responsible for cleaning up the returned file (used once, then streamed
    back as a download response)."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="alchemyfm-logs-"))
    zip_path = tmp_dir / "alchemyfm-logs.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, filename in LOG_FILES.items():
            path = log_path(name)
            if path.exists():
                zf.write(path, filename)
    return zip_path


def rotate_liquidsoap_log_if_needed() -> None:
    """Liquidsoap has no built-in rotation, unlike backend.log (Python's
    RotatingFileHandler) and the Icecast logs (Icecast's own <logsize>) --
    roll it manually on the same size-capped-with-backups policy.

    Every enabled station is its own long-running Liquidsoap process holding
    this file open for append for its entire lifetime -- renaming it out
    from under them (like backend.log's rotation does) would leave every
    process still writing to the old, now-unlisted inode forever, so the
    "current" log would stay permanently empty while disk usage grows
    unbounded anyway. Copy the content out to the backup slot instead, then
    truncate the original file in place: existing append-mode file
    descriptors keep writing to the same inode, now starting from byte 0.
    """
    path = log_path("liquidsoap")
    try:
        if not path.exists() or path.stat().st_size < LIQUIDSOAP_LOG_MAX_BYTES:
            return
        oldest = path.with_suffix(path.suffix + f".{LIQUIDSOAP_LOG_BACKUP_COUNT}")
        if oldest.exists():
            oldest.unlink()
        for i in range(LIQUIDSOAP_LOG_BACKUP_COUNT - 1, 0, -1):
            src = path.with_suffix(path.suffix + f".{i}")
            if src.exists():
                src.rename(path.with_suffix(path.suffix + f".{i + 1}"))
        shutil.copyfile(path, path.with_suffix(path.suffix + ".1"))
        with path.open("r+b") as f:
            f.truncate(0)
    except OSError:
        pass
