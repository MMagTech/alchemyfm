from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import require_internal_client
from app.config import settings
from app.database import Station, get_db
from app.services.queue import ensure_queue_fresh, mark_track_started

router = APIRouter(
    prefix="/internal/stations",
    tags=["internal"],
    dependencies=[Depends(require_internal_client)],
)


@router.api_route("/{slug}/track-started", methods=["GET", "POST"])
async def track_started(
    slug: str,
    artist: str = Query(default=""),
    title: str = Query(default=""),
    secret: str = Query(default=""),
    db: Session = Depends(get_db),
):
    if secret != settings.liquidsoap_callback_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    station = db.query(Station).filter(Station.slug == slug).first()
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")

    mark_track_started(db, station, artist, title)
    await ensure_queue_fresh(db, station)
    return {"ok": True}
