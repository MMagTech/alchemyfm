from fastapi import Request
from slugify import slugify
from sqlalchemy.orm import Session

from app.config import settings
from app.database import QueueItem, QueueItemStatus, Station
from app.schemas import (
    NowPlaying,
    StationAdmin,
    StationCreate,
    StationDetail,
    StationSummary,
    StationUpdate,
    TrackRef,
)
from app.services.stream_urls import public_stream_url
from app.services.queue import (
    bootstrap_station,
    delete_station_files,
    ensure_queue_fresh,
    get_now_playing,
    get_recently_played,
    get_up_next,
)
from app.services.station_artwork import artwork_exists, delete_artwork, public_artwork_url
from app.services.refill import pool_count
from app.services.stream_epoch import get_stream_epoch


def _unique_slug(db: Session, name: str, explicit: str | None = None) -> str:
    base = slugify(explicit or name) or "station"
    slug = base
    n = 2
    while db.query(Station).filter(Station.slug == slug).first():
        slug = f"{base}-{n}"
        n += 1
    return slug


def _public_description(text: str) -> str:
    return (text or "")[:120]


def station_to_summary(
    db: Session,
    station: Station,
    request: Request | None = None,
    listeners: int = 0,
    on_air: bool = False,
) -> StationSummary:
    return StationSummary(
        slug=station.slug,
        name=station.name,
        description=_public_description(station.description),
        artwork_url=public_artwork_url(station, request),
        enabled=station.enabled,
        now_playing=get_now_playing(db, station),
        stream_url=public_stream_url(station, request),
        listeners=listeners,
        on_air=on_air,
        stream_epoch=get_stream_epoch(),
        knowledge_feature=settings.knowledge_feature,
    )


def station_to_detail(
    db: Session,
    station: Station,
    request: Request | None = None,
    listeners: int = 0,
    on_air: bool = False,
) -> StationDetail:
    summary = station_to_summary(
        db, station, request, listeners=listeners, on_air=on_air
    )
    return StationDetail(
        **summary.model_dump(),
        up_next=get_up_next(db, station),
        recently_played=get_recently_played(db, station),
        icecast_mount=station.icecast_mount,
        source_type=station.source_type,
        source_ref=station.source_ref,
    )


def _queue_buffer_minutes(db: Session, station_id: int) -> int:
    items = (
        db.query(QueueItem)
        .filter(
            QueueItem.station_id == station_id,
            QueueItem.status == QueueItemStatus.queued,
        )
        .all()
    )
    total_sec = sum(item.duration_sec or 210 for item in items)
    return max(0, round(total_sec / 60))


def station_to_admin(db: Session, station: Station, queued_count: int) -> StationAdmin:
    detail = station_to_detail(db, station)
    return StationAdmin(
        **detail.model_dump(),
        id=station.id,
        queue_target=station.queue_target,
        refresh_threshold=station.refresh_threshold,
        artist_separation_minutes=station.artist_separation_minutes,
        continuation_mode=station.continuation_mode,
        identity_seed_item_id=station.identity_seed_item_id or "",
        identity_anchor_id=station.identity_anchor_id or "",
        pool_count=pool_count(db, station.id),
        source_last_error=station.source_last_error or "",
        source_healthy=not station.source_last_error,
        buffer_minutes=_queue_buffer_minutes(db, station.id),
        queued_count=queued_count,
        created_at=station.created_at,
        has_uploaded_artwork=artwork_exists(station.slug),
        external_artwork_url=station.artwork_url or "",
    )


async def create_station_record(db: Session, payload: StationCreate) -> Station:
    slug = _unique_slug(db, payload.name, payload.slug)
    mount = payload.icecast_mount
    if db.query(Station).filter(Station.icecast_mount == mount).first():
        raise ValueError(f"Icecast mount {mount} is already in use")

    station = Station(
        name=payload.name,
        slug=slug,
        description=payload.description,
        artwork_url=payload.artwork_url,
        icecast_mount=mount,
        enabled=payload.enabled,
        source_type=payload.source_type.value,
        source_ref=payload.source_ref,
        queue_target=payload.queue_target,
        refresh_threshold=payload.refresh_threshold,
        artist_separation_minutes=payload.artist_separation_minutes,
        continuation_mode=payload.continuation_mode,
        identity_seed_item_id=payload.identity_seed_item_id,
        identity_anchor_id=payload.identity_anchor_id,
    )
    db.add(station)
    db.commit()
    db.refresh(station)

    if payload.bootstrap_queue and station.enabled:
        await bootstrap_station(db, station)
        if payload.identity_seed_item_id:
            station.identity_seed_item_id = payload.identity_seed_item_id
        if payload.identity_anchor_id:
            station.identity_anchor_id = payload.identity_anchor_id
        db.commit()
    return station


def update_station_record(db: Session, station: Station, payload: StationUpdate) -> Station:
    data = payload.model_dump(exclude_unset=True)
    if "icecast_mount" in data and data["icecast_mount"]:
        existing = (
            db.query(Station)
            .filter(Station.icecast_mount == data["icecast_mount"], Station.id != station.id)
            .first()
        )
        if existing:
            raise ValueError(f"Icecast mount {data['icecast_mount']} is already in use")
    for key, value in data.items():
        if key == "source_type" and value is not None:
            value = value.value if hasattr(value, "value") else value
        setattr(station, key, value)
    if data.get("artwork_url"):
        delete_artwork(station.slug)
    db.commit()
    db.refresh(station)
    return station
