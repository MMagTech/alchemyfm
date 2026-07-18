import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.database import BroadcastSettings

logger = logging.getLogger(__name__)

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
    "backup_auto_enabled": True,
    "log_level": settings.log_level,
}


def computed_source_slots(db: Session) -> int:
    """Icecast <sources> limit for the current station count, with enough
    headroom that ordinary station creation never has to wait on a restart."""
    from app.database import Station

    enabled = db.query(Station).filter(Station.enabled.is_(True)).count()
    return max(16, enabled + 10)


def apply_broadcast_settings(db: Session, row: BroadcastSettings) -> str | None:
    """Regenerate Liquidsoap scripts and Icecast config from global settings.

    Returns a warning message when the running Icecast is still enforcing a
    smaller source limit than the one just written (so newly created stations
    cannot connect until it restarts), else None.
    """
    from app.services.icecast_config import write_icecast_config
    from app.services.liquidsoap import regenerate_liquidsoap_config

    source_slots = computed_source_slots(db)
    write_icecast_config(row.max_listeners, source_slots=source_slots)
    regenerate_liquidsoap_config(db)
    return _sync_icecast_source_limit(db, row, source_slots)


def _sync_icecast_source_limit(
    db: Session, row: BroadcastSettings, source_slots: int
) -> str | None:
    """Icecast only reads icecast.xml at startup. When the written <sources>
    limit outgrows the one the running Icecast holds, sources for new stations
    are refused with 403s forever and nothing surfaces it — so restart Icecast
    to apply the new limit, or return a loud warning when we can't."""
    from app.services.icecast_restart import (
        icecast_restart_enabled,
        restart_icecast_container,
    )

    applied = int(row.icecast_applied_sources or 0)
    if applied >= source_slots:
        return None

    warning = (
        f"Icecast is running with a source limit of {applied or 'unknown'} but "
        f"{source_slots} slots are now needed — stations beyond the running "
        "limit cannot come on air until Icecast restarts. Use the admin "
        "'Restart Icecast' action (or restart the container) to apply the new limit."
    )
    if not icecast_restart_enabled():
        logger.warning("%s (automatic restart unavailable)", warning)
        return warning
    try:
        restart_icecast_container()
    except RuntimeError as exc:
        logger.warning("%s (automatic restart failed: %s)", warning, exc)
        return warning
    row.icecast_applied_sources = source_slots
    db.commit()
    logger.info(
        "Restarted Icecast to raise the source limit to %s", source_slots
    )
    return None


def record_icecast_restarted(db: Session) -> None:
    """After any successful Icecast restart, the running limit == the written
    file. Record it so limit-grow checks stay accurate."""
    row = get_broadcast_settings(db)
    row.icecast_applied_sources = computed_source_slots(db)
    db.commit()


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
