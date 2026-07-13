from sqlalchemy.orm import Session

from app.database import BroadcastSettings

DEFAULTS = {
    "mp3_bitrate": 192,
    "aac_bitrate": 128,
    "sample_rate": 44100,
    "encode_format": "mp3",
    "genre": "Radio",
    "crossfade_sec": 0,
    "max_listeners": 100,
    "default_theme": "violet",
    "artist_bio_enabled": True,
    "backup_keep_count": 7,
}


def apply_broadcast_settings(db: Session, row: BroadcastSettings) -> None:
    """Regenerate Liquidsoap scripts and Icecast config from global settings."""
    from app.database import Station
    from app.services.icecast_config import write_icecast_config
    from app.services.liquidsoap import regenerate_liquidsoap_config

    enabled = db.query(Station).filter(Station.enabled.is_(True)).count()
    write_icecast_config(row.max_listeners, source_slots=max(10, enabled + 2))
    regenerate_liquidsoap_config(db)


def get_broadcast_settings(db: Session) -> BroadcastSettings:
    row = db.query(BroadcastSettings).filter(BroadcastSettings.id == 1).first()
    if row:
        return row
    row = BroadcastSettings(id=1, **DEFAULTS)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_broadcast_settings(db: Session, data: dict) -> BroadcastSettings:
    row = get_broadcast_settings(db)
    for key, value in data.items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row
