from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import require_internal_client
from app.config import settings
from app.database import SessionLocal, Station
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
):
    """Liquidsoap hits this on every track change, on every station, all day,
    independent of whether anyone is listening.

    Doesn't use Depends(get_db): ensure_queue_fresh can trigger a queue
    refill, which releases and reopens the DB session several times around
    slow external AudioMuse/Navidrome calls (see queue.py/refill.py) --
    holding one request-scoped session open across all of that, at this
    frequency, was enough on its own to exhaust the connection pool and
    freeze every other endpoint.
    """
    if secret != settings.liquidsoap_callback_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    db = SessionLocal()
    try:
        station = db.query(Station).filter(Station.slug == slug).first()
        if not station:
            raise HTTPException(status_code=404, detail="Station not found")

        mark_track_started(db, station, artist, title)
        db, station = await ensure_queue_fresh(db, station)
    finally:
        db.close()
    return {"ok": True}
