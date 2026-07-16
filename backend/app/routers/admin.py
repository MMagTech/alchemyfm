from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import QueueItem, QueueItemStatus, SessionLocal, Station, get_db
from app.schemas import StationAdmin, StationCreate, StationUpdate
from app.services.broadcast_settings import apply_broadcast_settings, get_broadcast_settings
from app.services.icecast import fetch_all_mount_stats
from app.services.liquidsoap import regenerate_liquidsoap_config
from app.services.queue import bootstrap_station, delete_station_files, extend_queue, rebuild_m3u_from_db
from app.services.station_artwork import delete_artwork, save_artwork
from app.services.stations import create_station_record, station_to_admin, update_station_record

router = APIRouter(
    prefix="/api/admin/stations",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


@router.get("", response_model=list[StationAdmin])
async def admin_list_stations():
    # Fetch Icecast mount stats once, up front, and share it across every
    # station below -- station_to_admin/get_now_playing fetch it themselves
    # when it's not passed in, which with N stations and no sharing meant N
    # separate Icecast round-trips for one page load of the admin list.
    mount_stats = await fetch_all_mount_stats()
    db = SessionLocal()
    try:
        stations = (
            db.query(Station)
            .order_by(
                Station.featured.desc(),
                Station.featured_order.asc(),
                Station.sort_order.asc(),
                Station.name.asc(),
            )
            .all()
        )
        result: list[StationAdmin] = []
        for station in stations:
            queued = (
                db.query(QueueItem)
                .filter(
                    QueueItem.station_id == station.id,
                    QueueItem.status == QueueItemStatus.queued,
                )
                .count()
            )
            result.append(await station_to_admin(db, station, queued, mount_stats=mount_stats))
        return result
    finally:
        db.close()


@router.post("", response_model=StationAdmin, status_code=201)
async def admin_create_station(payload: StationCreate):
    # create_station_record may bootstrap the new station's queue, which
    # releases and reopens the session around external AudioMuse/Navidrome
    # calls -- so this doesn't use Depends(get_db) (see admin_refresh_queue).
    db = SessionLocal()
    try:
        try:
            db, station = await create_station_record(db, payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        apply_broadcast_settings(db, get_broadcast_settings(db))
        queued = (
            db.query(QueueItem)
            .filter(QueueItem.station_id == station.id, QueueItem.status == QueueItemStatus.queued)
            .count()
        )
        return await station_to_admin(db, station, queued)
    finally:
        db.close()


@router.put("/{station_id}", response_model=StationAdmin)
async def admin_update_station(
    station_id: int, payload: StationUpdate, db: Session = Depends(get_db)
):
    station = db.query(Station).filter(Station.id == station_id).first()
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")
    try:
        station = update_station_record(db, station, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    regenerate_liquidsoap_config(db)
    queued = (
        db.query(QueueItem)
        .filter(QueueItem.station_id == station.id, QueueItem.status == QueueItemStatus.queued)
        .count()
    )
    return await station_to_admin(db, station, queued)


@router.post("/{station_id}/artwork", response_model=StationAdmin)
async def admin_upload_artwork(
    station_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    station = db.query(Station).filter(Station.id == station_id).first()
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")
    data = await file.read()
    try:
        save_artwork(station.slug, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    queued = (
        db.query(QueueItem)
        .filter(QueueItem.station_id == station.id, QueueItem.status == QueueItemStatus.queued)
        .count()
    )
    return await station_to_admin(db, station, queued)


@router.delete("/{station_id}/artwork", response_model=StationAdmin)
async def admin_delete_artwork(station_id: int, db: Session = Depends(get_db)):
    station = db.query(Station).filter(Station.id == station_id).first()
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")
    delete_artwork(station.slug)
    station.artwork_url = ""
    db.commit()
    queued = (
        db.query(QueueItem)
        .filter(QueueItem.station_id == station.id, QueueItem.status == QueueItemStatus.queued)
        .count()
    )
    return await station_to_admin(db, station, queued)


@router.delete("/{station_id}", status_code=204)
def admin_delete_station(station_id: int, db: Session = Depends(get_db)):
    station = db.query(Station).filter(Station.id == station_id).first()
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")
    slug = station.slug
    db.delete(station)
    db.commit()
    delete_station_files(slug)
    apply_broadcast_settings(db, get_broadcast_settings(db))


@router.post("/{station_id}/rebuild-m3u", response_model=StationAdmin)
async def admin_rebuild_m3u(station_id: int, db: Session = Depends(get_db)):
    station = db.query(Station).filter(Station.id == station_id).first()
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")
    rebuild_m3u_from_db(db, station)
    queued = (
        db.query(QueueItem)
        .filter(QueueItem.station_id == station.id, QueueItem.status == QueueItemStatus.queued)
        .count()
    )
    return await station_to_admin(db, station, queued)


@router.post("/{station_id}/refresh-queue", response_model=StationAdmin)
async def admin_refresh_queue(station_id: int):
    # extend_queue releases and reopens the session around slow external
    # AudioMuse/Navidrome calls, so this doesn't use Depends(get_db) -- that
    # would leave FastAPI closing the original (now-superseded) session
    # while whatever extend_queue swapped db to afterward leaks.
    db = SessionLocal()
    try:
        station = db.query(Station).filter(Station.id == station_id).first()
        if not station:
            raise HTTPException(status_code=404, detail="Station not found")
        need = max(station.queue_target - station.refresh_threshold, 1)
        try:
            _added, db, station = await extend_queue(db, station, need)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        queued = (
            db.query(QueueItem)
            .filter(QueueItem.station_id == station.id, QueueItem.status == QueueItemStatus.queued)
            .count()
        )
        return await station_to_admin(db, station, queued)
    finally:
        db.close()


@router.post("/{station_id}/bootstrap", response_model=StationAdmin)
async def admin_bootstrap(station_id: int):
    # bootstrap_station releases and reopens the session around external
    # AudioMuse/Navidrome calls, so this doesn't use Depends(get_db) (see
    # admin_refresh_queue).
    db = SessionLocal()
    try:
        station = db.query(Station).filter(Station.id == station_id).first()
        if not station:
            raise HTTPException(status_code=404, detail="Station not found")
        try:
            db, station = await bootstrap_station(db, station)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        queued = (
            db.query(QueueItem)
            .filter(QueueItem.station_id == station.id, QueueItem.status == QueueItemStatus.queued)
            .count()
        )
        return await station_to_admin(db, station, queued)
    finally:
        db.close()
