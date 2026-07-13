"""Broadcast Settings API: encode format switch (mp3/aac), and the migration
that normalizes any pre-existing 'vorbis' setting back to mp3 since Vorbis
has no decoder anywhere in iOS Safari/WKWebView and was dropped as an option.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import engine
from app.services.icecast_config import encode_format_liquidsoap, stream_media_type


def test_get_broadcast_settings_exposes_aac_bitrate(admin_client):
    resp = admin_client.get("/api/admin/broadcast")
    assert resp.status_code == 200
    data = resp.json()
    assert data["encode_format"] == "mp3"
    assert "aac_bitrate" in data
    assert "vorbis_bitrate" not in data


def test_vorbis_is_rejected_as_encode_format(admin_client):
    resp = admin_client.put(
        "/api/admin/broadcast",
        json={"encode_format": "vorbis"},
    )
    assert resp.status_code == 422


def test_switch_to_aac_persists(admin_client):
    resp = admin_client.put(
        "/api/admin/broadcast",
        json={"encode_format": "aac", "aac_bitrate": 160},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["encode_format"] == "aac"
    assert data["aac_bitrate"] == 160

    resp = admin_client.get("/api/admin/broadcast")
    assert resp.json()["encode_format"] == "aac"


def test_encode_format_liquidsoap_uses_fdkaac_for_aac():
    class FakeSettings:
        encode_format = "aac"
        aac_bitrate = 128
        sample_rate = 44100
        mp3_bitrate = 192

    line = encode_format_liquidsoap(FakeSettings())
    assert "%fdkaac" in line
    assert "bitrate=128" in line
    assert stream_media_type("aac") == "audio/aac"
    assert stream_media_type("mp3") == "audio/mpeg"


def test_migrate_normalizes_legacy_vorbis_setting(db_session: Session) -> None:
    from app.database import _migrate_db
    from app.services.broadcast_settings import get_broadcast_settings

    get_broadcast_settings(db_session)  # ensure the id=1 row exists
    with engine.begin() as conn:
        conn.execute(text("UPDATE broadcast_settings SET encode_format = 'vorbis' WHERE id = 1"))

    _migrate_db()

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT encode_format FROM broadcast_settings WHERE id = 1")
        ).first()
    assert row[0] == "mp3"
