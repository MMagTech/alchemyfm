from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
import httpx
from sqlalchemy.orm import Session

from app.services.broadcast_settings import get_broadcast_settings
from app.services.icecast_config import stream_media_type
from app.config import settings
from app.database import Station, get_db
from app.schemas import StationDetail, StationSummary
from app.services.stream_urls import public_stream_url
from app.services.icecast import _normalize_mount, fetch_all_mount_stats, fetch_mount_listeners
from app.services.station_artwork import artwork_path
from app.services.stations import station_to_detail, station_to_summary

router = APIRouter(prefix="/api/stations", tags=["stations"])


def _icecast_internal_url(station: Station) -> str:
    mount = station.icecast_mount
    if not mount.startswith("/"):
        mount = f"/{mount}"
    return f"http://{settings.icecast_host}:{settings.icecast_port}{mount}"


@router.get("", response_model=list[StationSummary])
def list_stations(request: Request, db: Session = Depends(get_db)):
    stations = (
        db.query(Station)
        .filter(Station.enabled.is_(True))
        .order_by(Station.name.asc())
        .all()
    )
    mount_stats = fetch_all_mount_stats()
    return [
        station_to_summary(
            db,
            s,
            request,
            listeners=mount_stats.get(_normalize_mount(s.icecast_mount)).listeners
            if mount_stats.get(_normalize_mount(s.icecast_mount))
            else 0,
            on_air=_normalize_mount(s.icecast_mount) in mount_stats,
        )
        for s in stations
    ]


@router.get("/{slug}/listen.m3u")
def listen_m3u(slug: str, request: Request, db: Session = Depends(get_db)):
    """M3U playlist for external players."""
    station = db.query(Station).filter(Station.slug == slug, Station.enabled.is_(True)).first()
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")
    url = public_stream_url(station, request)
    body = f"#EXTM3U\n#EXTINF:-1,{station.name}\n{url}\n"
    return PlainTextResponse(body, media_type="audio/x-mpegurl")


@router.get("/{slug}/listen")
async def listen_stream(slug: str, db: Session = Depends(get_db)):
    """Same-origin stream for in-browser playback + visualizer."""
    station = db.query(Station).filter(Station.slug == slug, Station.enabled.is_(True)).first()
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")
    upstream = _icecast_internal_url(station)

    client = httpx.AsyncClient(timeout=httpx.Timeout(15.0, read=None))
    req = client.build_request("GET", upstream)
    resp = await client.send(req, stream=True)
    if resp.status_code != 200:
        await resp.aclose()
        await client.aclose()
        raise HTTPException(status_code=502, detail="Stream unavailable")

    async def stream():
        try:
            async for chunk in resp.aiter_bytes(8192):
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(
        stream(),
        media_type=stream_media_type(get_broadcast_settings(db).encode_format),
    )


@router.get("/{slug}/artwork")
def get_station_artwork(slug: str, db: Session = Depends(get_db)):
    station = db.query(Station).filter(Station.slug == slug).first()
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")
    path = artwork_path(slug)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Artwork not found")
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/{slug}", response_model=StationDetail)
def get_station(slug: str, request: Request, db: Session = Depends(get_db)):
    station = db.query(Station).filter(Station.slug == slug, Station.enabled.is_(True)).first()
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")
    mount = _normalize_mount(station.icecast_mount)
    mount_stats = fetch_all_mount_stats()
    parsed = mount_stats.get(mount)
    return station_to_detail(
        db,
        station,
        request,
        listeners=parsed.listeners if parsed else 0,
        on_air=mount in mount_stats,
    )
