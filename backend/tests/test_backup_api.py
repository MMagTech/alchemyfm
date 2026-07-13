"""Admin backup download endpoint: creates a fresh backup, prunes per the
configured retention count, and streams the zip back."""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from app.config import settings
from app.services.backup import list_backups


@pytest.fixture(autouse=True)
def _clean_backup_dir():
    import shutil

    path = Path(settings.backup_dir)
    if path.exists():
        shutil.rmtree(path)
    yield
    if path.exists():
        shutil.rmtree(path)


def test_download_backup_requires_admin(client):
    resp = client.post("/api/admin/broadcast/backup/download")
    assert resp.status_code == 401


def test_download_backup_returns_valid_zip(admin_client, sample_station):
    resp = admin_client.post("/api/admin/broadcast/backup/download")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(BytesIO(resp.content)) as zf:
        assert "radio.db" in zf.namelist()


def test_download_backup_prunes_to_configured_keep_count(admin_client):
    resp = admin_client.put(
        "/api/admin/broadcast", json={"backup_keep_count": 2}
    )
    assert resp.status_code == 200
    assert resp.json()["backup_keep_count"] == 2

    for _ in range(4):
        resp = admin_client.post("/api/admin/broadcast/backup/download")
        assert resp.status_code == 200

    assert len(list_backups()) == 2
