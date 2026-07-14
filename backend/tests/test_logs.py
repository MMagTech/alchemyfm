"""Log file service: listing, tailing, clearing, and Liquidsoap's manual
rotation (no native rotation support, unlike backend.log or Icecast).

backend.log is deliberately left alone by the fixture here: the app's own
lifespan (triggered by any test using the `client`/`admin_client` fixtures
elsewhere in the suite) attaches a real, open RotatingFileHandler to it for
the rest of the pytest process, and deleting/rotating a file another handle
has open fails on Windows. Tests that need a "file doesn't exist yet" or
"exact content" case use liquidsoap/icecast-access/icecast-error instead,
which nothing else in the suite touches.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import settings
from app.services import logs

_OTHER_LOGS = ("liquidsoap", "icecast-access", "icecast-error")


@pytest.fixture(autouse=True)
def _clean_other_logs():
    def _cleanup():
        for name in _OTHER_LOGS:
            path = logs.log_path(name)
            for candidate in path.parent.glob(path.name + "*"):
                candidate.unlink(missing_ok=True)

    Path(settings.log_dir).mkdir(parents=True, exist_ok=True)
    _cleanup()
    yield
    _cleanup()


def test_list_logs_reports_missing_files_as_absent():
    entries = {e["name"]: e for e in logs.list_logs()}
    assert set(entries) == set(logs.LOG_FILES)
    for name in _OTHER_LOGS:
        assert entries[name]["exists"] is False
        assert entries[name]["size"] == 0


def test_list_logs_reports_real_size():
    logs.log_path("liquidsoap").write_bytes(b"hello\nworld\n")
    entries = {e["name"]: e for e in logs.list_logs()}
    assert entries["liquidsoap"]["exists"] is True
    assert entries["liquidsoap"]["size"] == len(b"hello\nworld\n")


def test_tail_log_returns_last_n_lines():
    path = logs.log_path("liquidsoap")
    path.write_text("".join(f"line{i}\n" for i in range(10)))
    tail = logs.tail_log("liquidsoap", lines=3)
    assert tail == "line7\nline8\nline9\n"


def test_tail_log_missing_file_returns_empty_string():
    assert logs.tail_log("liquidsoap") == ""


def test_clear_log_truncates_in_place():
    path = logs.log_path("liquidsoap")
    path.write_text("some content\n")
    logs.clear_log("liquidsoap")
    assert path.exists()
    assert path.read_text() == ""


def test_clear_log_missing_file_is_a_no_op():
    logs.clear_log("liquidsoap")  # must not raise


def test_rotate_liquidsoap_log_is_a_no_op_below_size_cap():
    path = logs.log_path("liquidsoap")
    path.write_text("small\n")
    logs.rotate_liquidsoap_log_if_needed()
    assert path.read_text() == "small\n"
    assert not path.with_suffix(".log.1").exists()


def test_rotate_liquidsoap_log_preserves_an_already_open_append_handle():
    """Every enabled station is a long-running Liquidsoap process holding
    this file open for the station's entire lifetime -- rotation must not
    break its existing file descriptor (see the docstring on the function
    under test for why a plain rename would silently break this)."""
    path = logs.log_path("liquidsoap")
    original_max = logs.LIQUIDSOAP_LOG_MAX_BYTES
    logs.LIQUIDSOAP_LOG_MAX_BYTES = 100
    try:
        fd = path.open("a")
        fd.write("A" * 150 + "\n")
        fd.flush()

        logs.rotate_liquidsoap_log_if_needed()

        assert path.stat().st_size == 0
        backup = path.with_suffix(".log.1")
        assert backup.exists()
        assert "A" * 150 in backup.read_text()

        # The station process never restarts across a rotation -- it just
        # keeps writing through the same handle it already had open.
        fd.write("B" * 20 + "\n")
        fd.flush()
        fd.close()

        assert path.read_text() == "B" * 20 + "\n"
    finally:
        logs.LIQUIDSOAP_LOG_MAX_BYTES = original_max


def test_rotate_liquidsoap_log_keeps_bounded_backup_count():
    path = logs.log_path("liquidsoap")
    original_max = logs.LIQUIDSOAP_LOG_MAX_BYTES
    logs.LIQUIDSOAP_LOG_MAX_BYTES = 10
    try:
        for round_num in range(5):
            with path.open("a") as f:
                f.write(f"round{round_num}-" + "x" * 20 + "\n")
            logs.rotate_liquidsoap_log_if_needed()

        backups = sorted(Path(settings.log_dir).glob("liquidsoap.log.*"))
        assert len(backups) == logs.LIQUIDSOAP_LOG_BACKUP_COUNT
    finally:
        logs.LIQUIDSOAP_LOG_MAX_BYTES = original_max
