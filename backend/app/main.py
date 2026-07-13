import asyncio
import logging
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy.orm import Session

from app.auth import admin_auth_enabled, admin_user_from_request
from app.config import settings
from app.database import SessionLocal, Station, init_db
from app.rate_limit import limiter
from app.schemas import BroadcastStatsRead, HealthResponse
from app.middleware import SecurityHeadersMiddleware
from app.routers import admin, admin_auth, admin_broadcast, admin_knowledge, admin_navidrome, internal, stations
from app.knowledge.database import init_knowledge_db
from app.knowledge.worker import start_knowledge_worker
from app.services.broadcast_settings import apply_broadcast_settings, get_broadcast_settings
from app.services.icecast import fetch_broadcast_totals
from app.services.navidrome import navidrome_client
from app.services.queue import ensure_queue_fresh, rebuild_all_station_m3u, sync_station_from_icecast
from app.knowledge.database import KnowledgeSessionLocal
from app.knowledge.scheduler import schedule_all_stations_lookahead, schedule_knowledge_lookahead
from app.knowledge.settings import get_knowledge_settings, knowledge_feature_enabled, processing_enabled

logger = logging.getLogger(__name__)

WEB_ROOT = Path("/web") if Path("/web").exists() else Path(__file__).resolve().parent.parent.parent / "web"

_HTML_NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}


def _html_page(name: str) -> FileResponse:
    path = WEB_ROOT / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, headers=_HTML_NO_CACHE)


_icecast_last_track: dict[str, tuple[str, str] | None] = {}


async def _queue_refresh_loop() -> None:
    while True:
        try:
            db: Session = SessionLocal()
            kdb = None
            knowledge_active = False
            if knowledge_feature_enabled():
                kdb = KnowledgeSessionLocal()
                try:
                    knowledge_active = processing_enabled(get_knowledge_settings(kdb).mode)
                except Exception:
                    logger.exception("Knowledge settings read failed")
            try:
                enabled = db.query(Station).filter(Station.enabled.is_(True)).all()
                for station in enabled:
                    _icecast_last_track[station.slug] = sync_station_from_icecast(
                        db, station, _icecast_last_track.get(station.slug)
                    )
                    await ensure_queue_fresh(db, station)
                    if knowledge_active:
                        schedule_knowledge_lookahead(station.id)
            finally:
                db.close()
                if kdb:
                    kdb.close()
        except Exception:
            logger.exception("Queue refresh loop error")
        await asyncio.sleep(settings.queue_refresh_interval_sec)


async def _backup_loop() -> None:
    from app.services.backup import create_backup, prune_old_backups

    interval_sec = max(1, settings.backup_interval_hours) * 3600
    while True:
        try:
            path = await asyncio.to_thread(create_backup)
            db = SessionLocal()
            try:
                bs = get_broadcast_settings(db)
                prune_old_backups(bs.backup_keep_count)
            finally:
                db.close()
            logger.info("Automatic backup created: %s", path.name)
        except Exception:
            logger.exception("Automatic backup failed")
        await asyncio.sleep(interval_sec)


def _configure_logging() -> None:
    """Log to stdout plus a persistent, size-capped rotating file under DATA_DIR.

    The file rotates in place: once ``log_max_bytes`` is reached it rolls to
    ``backend.log.1`` (up to ``log_backup_count`` backups), so total disk use is
    bounded (~max_bytes * (backup_count + 1)) and the oldest data is overwritten.
    """
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    has_console = any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in root.handlers
    )
    if not has_console:
        stream = logging.StreamHandler()
        stream.setFormatter(fmt)
        root.addHandler(stream)

    already_have_file = any(
        isinstance(h, RotatingFileHandler) for h in root.handlers
    )
    if not already_have_file:
        try:
            Path(settings.log_dir).mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                settings.log_file,
                maxBytes=settings.log_max_bytes,
                backupCount=settings.log_backup_count,
                encoding="utf-8",
            )
            file_handler.setFormatter(fmt)
            root.addHandler(file_handler)
            logger.info(
                "Logging to %s (max %d bytes x %d backups)",
                settings.log_file,
                settings.log_max_bytes,
                settings.log_backup_count,
            )
        except OSError as exc:
            logger.warning("Could not open log file %s: %s", settings.log_file, exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
    if not admin_auth_enabled():
        logger.warning(
            "ADMIN_PASSWORD is not set — admin UI and /api/admin are DISABLED. "
            "Set ADMIN_PASSWORD before exposing this service publicly."
        )
    init_db()
    init_knowledge_db()
    schedule_all_stations_lookahead()
    Path(settings.stations_root).mkdir(parents=True, exist_ok=True)
    db = SessionLocal()
    try:
        bs = get_broadcast_settings(db)
        apply_broadcast_settings(db, bs)
        rebuild_all_station_m3u(db)
    finally:
        db.close()
    task = asyncio.create_task(_queue_refresh_loop())
    backup_task = asyncio.create_task(_backup_loop())
    knowledge_task = start_knowledge_worker()
    yield
    task.cancel()
    backup_task.cancel()
    if knowledge_task:
        knowledge_task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    try:
        await backup_task
    except asyncio.CancelledError:
        pass
    if knowledge_task:
        try:
            await knowledge_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Alchemy FM",
    description="Station management and queue orchestration for live internet radio.",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

app.include_router(stations.router)
app.include_router(admin_auth.router)
app.include_router(admin.router)
app.include_router(admin_broadcast.router)
app.include_router(admin_knowledge.router)
app.include_router(admin_navidrome.router)
app.include_router(internal.router)


@app.get("/api/cover/{item_id}")
async def cover_art(item_id: str, size: int = Query(default=300, ge=64, le=1000)):
    """Proxy album art from Navidrome for the web UI."""
    result = await navidrome_client.fetch_cover_art(item_id, size)
    if not result:
        raise HTTPException(status_code=404, detail="Cover art not found")
    data, media_type = result
    return Response(content=data, media_type=media_type, headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/broadcast/stats", response_model=BroadcastStatsRead)
def broadcast_stats():
    db = SessionLocal()
    try:
        mounts = [
            s.icecast_mount
            for s in db.query(Station).filter(Station.enabled.is_(True)).all()
        ]
        bs = get_broadcast_settings(db)
        stream_bitrate = (
            bs.aac_bitrate if bs.encode_format == "aac" else bs.mp3_bitrate
        )
    finally:
        db.close()
    totals = fetch_broadcast_totals(mounts, stream_bitrate_kbps=stream_bitrate)
    return BroadcastStatsRead(**totals)


@app.get("/api/health", response_model=HealthResponse)
def health():
    db = SessionLocal()
    try:
        count = db.query(Station).filter(Station.enabled.is_(True)).count()
        bs = get_broadcast_settings(db)
        default_theme = bs.default_theme or "violet"
    finally:
        db.close()
    return {
        "status": "ok",
        "stations_enabled": count,
        "knowledge_feature": settings.knowledge_feature,
        "default_theme": default_theme,
    }


@app.get("/")
def home():
    index = WEB_ROOT / "index.html"
    if index.exists():
        return FileResponse(index, headers=_HTML_NO_CACHE)
    return {"message": "Alchemy FM API"}


@app.get("/station.html")
def station_page():
    try:
        return _html_page("station.html")
    except HTTPException:
        return {"error": "not found"}


@app.get("/admin/login.html")
def admin_login_page():
    try:
        return _html_page("admin-login.html")
    except HTTPException:
        return {"error": "not found"}


@app.get("/admin-login.html")
def admin_login_page_alias():
    return admin_login_page()


@app.get("/admin/knowledge.html")
def admin_knowledge_page(request: Request):
    if not settings.knowledge_feature:
        if admin_user_from_request(request):
            return RedirectResponse(url="/admin.html?knowledge=disabled", status_code=302)
        raise HTTPException(status_code=404, detail="Not found")
    if not admin_auth_enabled():
        raise HTTPException(
            status_code=503,
            detail="Admin is disabled. Set ADMIN_PASSWORD in your environment.",
        )
    if not admin_user_from_request(request):
        return RedirectResponse(
            url="/admin/login.html?next=/admin/knowledge.html", status_code=302
        )
    try:
        return _html_page("admin-knowledge.html")
    except HTTPException:
        return {"error": "not found"}


@app.get("/admin.html")
def admin_page(request: Request):
    if not admin_auth_enabled():
        raise HTTPException(
            status_code=503,
            detail="Admin is disabled. Set ADMIN_PASSWORD in your environment.",
        )
    if not admin_user_from_request(request):
        return RedirectResponse(url="/admin/login.html?next=/admin.html", status_code=302)
    try:
        return _html_page("admin.html")
    except HTTPException:
        return {"error": "not found"}


static_dir = WEB_ROOT / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
