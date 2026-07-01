from fastapi import APIRouter, Depends, HTTPException
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
from app.services.broadcast_settings import (
    apply_broadcast_settings,
    get_broadcast_settings,
    update_broadcast_settings,
)
from app.services.icecast_restart import icecast_restart_enabled, restart_icecast_container

router = APIRouter(
    prefix="/api/admin/broadcast",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


@router.get("", response_model=BroadcastSettingsRead)
def read_broadcast_settings(db: Session = Depends(get_db)):
    row = get_broadcast_settings(db)
    return BroadcastSettingsRead.model_validate(row).model_copy(
        update={"icecast_restart_available": icecast_restart_enabled()}
    )


@router.get("/appearance", response_model=AppearanceSettingsRead)
def read_appearance_settings(db: Session = Depends(get_db)):
    row = get_broadcast_settings(db)
    theme = row.default_theme or "violet"
    return AppearanceSettingsRead(default_theme=theme)


@router.put("/appearance", response_model=AppearanceSettingsRead)
def save_appearance_settings(
    payload: AppearanceSettingsUpdate, db: Session = Depends(get_db)
):
    row = update_broadcast_settings(db, {"default_theme": payload.default_theme})
    return AppearanceSettingsRead(default_theme=row.default_theme or "violet")


@router.put("", response_model=BroadcastSettingsRead)
def save_broadcast_settings(
    payload: BroadcastSettingsUpdate, db: Session = Depends(get_db)
):
    data = payload.model_dump(exclude_unset=True)
    row = update_broadcast_settings(db, data)
    if not set(data.keys()).issubset({"default_theme"}):
        apply_broadcast_settings(db, row)
    return BroadcastSettingsRead.model_validate(row).model_copy(
        update={"icecast_restart_available": icecast_restart_enabled()}
    )


@router.post("/restart-icecast", response_model=IcecastRestartResponse)
def restart_icecast():
    try:
        container_name = restart_icecast_container()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return IcecastRestartResponse(ok=True, container=container_name)
