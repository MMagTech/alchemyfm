import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
import httpx
from sqlalchemy.orm import Session

from app.auth import admin_user_from_request
from app.services.broadcast_settings import get_broadcast_settings
from app.services.icecast_config import stream_media_type
from app.config import settings
from app.database import Station, get_db
from app.schemas import StationDetail, StationSummary
from app.services.stream_urls import public_stream_url
from app.services.icecast import _normalize_mount, fetch_all_mount_stats
from app.services.station_artwork import artwork_path
from app.services.navidrome import attach_artist_bio, attach_operator_heart
from app.services.stations import station_to_detail, station_to_summary

router = APIRouter(prefix="/api/stations", tags=["stations"])

logger = logging.getLogger(__name__)

# Active same-origin stream connections keyed by slug (diagnostics for
# duplicate-listener issues, e.g. iOS probe + playback connections).
_active_listen_connections: dict[str, int] = {}


def _probe_range_end(range_header: str | None) -> int | None:
    """Return the end byte for a tiny start-of-resource probe range.

    iOS/Safari sniffs a media resource with a small range like ``bytes=0-1``
    before opening the real (rangeless) playback connection. We only treat a
    small, finite range that starts at 0 as a probe; open-ended ranges
    (``bytes=0-``) belong to actual playback and must stream normally.
    """
    if not range_header:
        return None
    value = range_header.strip().lower()
    if not value.startswith("bytes="):
        return None
    spec = value[len("bytes="):].split(",")[0].strip()
    if "-" not in spec:
        return None
    start_s, end_s = spec.split("-", 1)
    if not end_s:
        return None
    try:
        start = int(start_s or "0")
        end = int(end_s)
    except ValueError:
        return None
    if start != 0 or end < 0 or (end - start) >= 1024:
        return None
    return end


def _icecast_internal_url(station: Station) -> str:
    mount = station.icecast_mount
    if not mount.startswith("/"):
        mount = f"/{mount}"
    return f"http://{settings.icecast_host}:{settings.icecast_port}{mount}"


@router.get("", response_model=list[StationSummary])
async def list_stations(
    request: Request, response: Response, db: Session = Depends(get_db)
):
    response.headers["Cache-Control"] = "no-store"
    stations = (
        db.query(Station)
        .filter(Station.enabled.is_(True))
        .order_by(Station.featured.desc(), Station.featured_order.asc(), Station.name.asc())
        .all()
    )
    mount_stats = fetch_all_mount_stats()
    broadcast = get_broadcast_settings(db)
    results = []
    for s in stations:
        mount = _normalize_mount(s.icecast_mount)
        parsed = mount_stats.get(mount)
        results.append(
            station_to_summary(
                db,
                s,
                request,
                listeners=parsed.listeners if parsed else 0,
                on_air=mount in mount_stats,
                mount_stats=mount_stats,
                list_mode=True,
                broadcast=broadcast,
            )
        )
    return results


@router.get("/{slug}/listen.m3u")
def listen_m3u(slug: str, request: Request, db: Session = Depends(get_db)):
    """M3U playlist for external players."""
    station = db.query(Station).filter(Station.slug == slug, Station.enabled.is_(True)).first()
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")
    url = public_stream_url(station, request)
    body = f"#EXTM3U\n#EXTINF:-1,{station.name}\n{url}\n"
    return PlainTextResponse(body, media_type="audio/x-mpegurl")


@router.head("/{slug}/listen")
async def listen_stream_head(slug: str, db: Session = Depends(get_db)):
    """Metadata-only response for Safari/iOS sniff — no upstream Icecast connect."""
    station = db.query(Station).filter(Station.slug == slug, Station.enabled.is_(True)).first()
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")
    media_type = stream_media_type(get_broadcast_settings(db).encode_format)
    return Response(
        status_code=200,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Type": media_type,
            "Cache-Control": "no-store",
        },
    )


@router.get("/{slug}/listen")
async def listen_stream(slug: str, request: Request, db: Session = Depends(get_db)):
    """Same-origin stream for in-browser playback + visualizer.

    iOS Safari typically opens a short probe/Range connection to sniff the
    resource before opening the real playback connection, and may hold both
    open. Each browser connection maps to one upstream Icecast listener, so a
    lingering probe inflates the listener count. We detect client disconnects
    between chunks and tear the upstream connection down immediately so ghost
    connections drop from Icecast quickly.
    """
    station = db.query(Station).filter(Station.slug == slug, Station.enabled.is_(True)).first()
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")

    range_header = request.headers.get("range")
    user_agent = request.headers.get("user-agent", "")
    media_type = stream_media_type(get_broadcast_settings(db).encode_format)

    # Answer iOS/Safari's tiny sniff probe with a finite 206 so it closes the
    # probe connection immediately, instead of leaving an endless 200 stream
    # open (which registers as a duplicate Icecast listener). No upstream
    # connection is opened for the probe at all.
    probe_end = _probe_range_end(range_header)
    if probe_end is not None:
        logger.debug(
            "listen probe slug=%s range=%s ua=%s", slug, range_header, user_agent[:80]
        )
        return Response(
            content=bytes(probe_end + 1),
            status_code=206,
            media_type=media_type,
            headers={
                "Content-Range": f"bytes 0-{probe_end}/*",
                "Accept-Ranges": "bytes",
                "Cache-Control": "no-store",
            },
        )

    upstream = _icecast_internal_url(station)
    client = httpx.AsyncClient(timeout=httpx.Timeout(15.0, read=None))
    req = client.build_request("GET", upstream)
    resp = await client.send(req, stream=True)
    if resp.status_code != 200:
        await resp.aclose()
        await client.aclose()
        raise HTTPException(status_code=502, detail="Stream unavailable")

    active = _active_listen_connections.get(slug, 0) + 1
    _active_listen_connections[slug] = active
    logger.debug(
        "listen open slug=%s active=%d range=%s ua=%s",
        slug,
        active,
        range_header or "-",
        user_agent[:80],
    )

    async def stream():
        try:
            async for chunk in resp.aiter_bytes(32768):
                if await request.is_disconnected():
                    break
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()
            remaining = _active_listen_connections.get(slug, 1) - 1
            if remaining > 0:
                _active_listen_connections[slug] = remaining
            else:
                _active_listen_connections.pop(slug, None)
            logger.debug("listen close slug=%s active=%d", slug, max(remaining, 0))

    return StreamingResponse(
        stream(),
        media_type=media_type,
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-store",
        },
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
async def get_station(
    slug: str, request: Request, response: Response, db: Session = Depends(get_db)
):
    response.headers["Cache-Control"] = "no-store"
    station = db.query(Station).filter(Station.slug == slug, Station.enabled.is_(True)).first()
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")
    mount = _normalize_mount(station.icecast_mount)
    mount_stats = fetch_all_mount_stats()
    parsed = mount_stats.get(mount)
    detail = station_to_detail(
        db,
        station,
        request,
        listeners=parsed.listeners if parsed else 0,
        on_air=mount in mount_stats,
        mount_stats=mount_stats,
    )
    if detail.now_playing and get_broadcast_settings(db).artist_bio_enabled:
        detail.now_playing = await attach_artist_bio(detail.now_playing)
    if detail.now_playing:
        detail.now_playing = await attach_operator_heart(
            detail.now_playing,
            is_admin=bool(admin_user_from_request(request)),
        )
    return detail
