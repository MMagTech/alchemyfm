"""Admin Logs API: list, tail, download (single + zip-all), and clear.

backend.log is left alone by the fixture: the app's own lifespan (triggered
by the client/admin_client fixtures) attaches a real, open RotatingFileHandler
to it for the rest of the pytest process, and touching a file another handle
has open fails on Windows. Tests needing an empty/missing/exact-content log
use liquidsoap instead, which nothing else in the suite writes to.
"""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from app.config import settings
from app.services.logs import log_path


@pytest.fixture(autouse=True)
def _clean_liquidsoap_log():
    def _cleanup():
        path = log_path("liquidsoap")
        for candidate in path.parent.glob(path.name + "*"):
            candidate.unlink(missing_ok=True)

    Path(settings.log_dir).mkdir(parents=True, exist_ok=True)
    _cleanup()
    yield
    _cleanup()


def test_read_logs_requires_admin(client):
    resp = client.get("/api/admin/logs")
    assert resp.status_code == 401


def test_read_logs_lists_all_known_logs(admin_client):
    resp = admin_client.get("/api/admin/logs")
    assert resp.status_code == 200
    names = {entry["name"] for entry in resp.json()["logs"]}
    assert names == {"backend", "liquidsoap", "icecast-access", "icecast-error"}


def test_tail_unknown_log_returns_404(admin_client):
    resp = admin_client.get("/api/admin/logs/nonexistent/tail")
    assert resp.status_code == 404


def test_tail_missing_log_file_returns_empty(admin_client):
    resp = admin_client.get("/api/admin/logs/liquidsoap/tail")
    assert resp.status_code == 200
    assert resp.text == ""


def test_tail_returns_last_n_lines(admin_client):
    log_path("liquidsoap").write_bytes(b"".join(f"line{i}\n".encode() for i in range(10)))
    resp = admin_client.get("/api/admin/logs/liquidsoap/tail?lines=3")
    assert resp.status_code == 200
    assert resp.text == "line7\nline8\nline9\n"


def test_download_missing_log_returns_404(admin_client):
    resp = admin_client.get("/api/admin/logs/liquidsoap/download")
    assert resp.status_code == 404


def test_download_existing_log_streams_file(admin_client):
    log_path("liquidsoap").write_bytes(b"hello world\n")
    resp = admin_client.get("/api/admin/logs/liquidsoap/download")
    assert resp.status_code == 200
    assert resp.content == b"hello world\n"


def test_download_all_bundles_only_existing_logs(admin_client):
    log_path("liquidsoap").write_bytes(b"liquidsoap content\n")

    resp = admin_client.get("/api/admin/logs/download-all")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(BytesIO(resp.content)) as zf:
        names = set(zf.namelist())
        assert "liquidsoap.log" in names
        assert "backend.log" in names  # always present -- the app is logging live


def test_clear_log_requires_admin(client):
    resp = client.post("/api/admin/logs/liquidsoap/clear")
    assert resp.status_code == 401


def test_clear_log_truncates_existing_file(admin_client):
    path = log_path("liquidsoap")
    path.write_bytes(b"noise to clear\n")
    resp = admin_client.post("/api/admin/logs/liquidsoap/clear")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert path.read_bytes() == b""


def test_clear_unknown_log_returns_404(admin_client):
    resp = admin_client.post("/api/admin/logs/nonexistent/clear")
    assert resp.status_code == 404
