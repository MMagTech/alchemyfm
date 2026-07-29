import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.schemas import (
    AppearanceSettingsRead,
    AppearanceSettingsUpdate,
    BroadcastSettingsRead,
    BroadcastSettingsUpdate,
    IcecastRestartResponse,
)
from app.services.backup import create_backup, prune_old_backups
from app.services.broadcast_settings import (
    apply_broadcast_settings,
    get_broadcast_settings,
    icecast_restart_pending,
    record_icecast_restarted,
    update_broadcast_settings,
)
from app.services.icecast_restart import icecast_restart_enabled, restart_icecast_container
from app.themes import normalize_theme

router = APIRouter(
    prefix="/api/admin/broadcast",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)

# Fields that actually change what Liquidsoap/Icecast serve -- only these
# warrant regenerating station scripts and briefly restarting every station.
# Saving something like backup settings shouldn't interrupt playback.
_APPLY_TRIGGER_FIELDS = {
    "max_listeners",
    "encode_format",
    "mp3_bitrate",
    "aac_bitrate",
    "sample_rate",
    "genre",
    "crossfade_sec",
}


@router.get("", response_model=BroadcastSettingsRead)
def read_broadcast_settings(db: Session = Depends(get_db)):
    row = get_broadcast_settings(db)
    return BroadcastSettingsRead.model_validate(row).model_copy(
        update={
            "icecast_restart_available": icecast_restart_enabled(),
            "icecast_restart_pending": icecast_restart_pending(db),
        }
    )


@router.get("/appearance", response_model=AppearanceSettingsRead)
def read_appearance_settings(db: Session = Depends(get_db)):
    row = get_broadcast_settings(db)
    return AppearanceSettingsRead(
        default_theme=normalize_theme(row.default_theme),
        artist_bio_enabled=bool(row.artist_bio_enabled),
        default_navidrome_playlist_id=row.default_navidrome_playlist_id or "",
    )


@router.put("/appearance", response_model=AppearanceSettingsRead)
def save_appearance_settings(
    payload: AppearanceSettingsUpdate, db: Session = Depends(get_db)
):
    row = update_broadcast_settings(
        db,
        {
            "default_theme": payload.default_theme,
            "artist_bio_enabled": payload.artist_bio_enabled,
            "default_navidrome_playlist_id": payload.default_navidrome_playlist_id or "",
        },
    )
    return AppearanceSettingsRead(
        default_theme=normalize_theme(row.default_theme),
        artist_bio_enabled=bool(row.artist_bio_enabled),
        default_navidrome_playlist_id=row.default_navidrome_playlist_id or "",
    )


@router.put("", response_model=BroadcastSettingsRead)
def save_broadcast_settings(
    payload: BroadcastSettingsUpdate, db: Session = Depends(get_db)
):
    data = payload.model_dump(exclude_unset=True)
    row = update_broadcast_settings(db, data)
    if set(data.keys()) & _APPLY_TRIGGER_FIELDS:
        apply_broadcast_settings(db, row)
    if "log_level" in data:
        # Takes effect immediately -- unlike encode/listener settings, the log
        # level doesn't touch Liquidsoap/Icecast, so no station restart needed.
        logging.getLogger().setLevel(getattr(logging, row.log_level, logging.INFO))
    return BroadcastSettingsRead.model_validate(row).model_copy(
        update={
            "icecast_restart_available": icecast_restart_enabled(),
            "icecast_restart_pending": icecast_restart_pending(db),
        }
    )


@router.post("/restart-icecast", response_model=IcecastRestartResponse)
def restart_icecast(db: Session = Depends(get_db)):
    try:
        container_name = restart_icecast_container()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    # The restarted Icecast now enforces whatever icecast.xml holds.
    record_icecast_restarted(db)
    return IcecastRestartResponse(ok=True, container=container_name)


@router.post("/backup/download")
def download_backup(db: Session = Depends(get_db)):
    """Create a fresh backup (radio.db + knowledge.db + icecast.xml) and
    return it immediately, so what you download is always current."""
    try:
        path = create_backup()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    row = get_broadcast_settings(db)
    prune_old_backups(row.backup_keep_count)
    return FileResponse(path, filename=path.name, media_type="application/zip")
