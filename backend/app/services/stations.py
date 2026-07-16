from fastapi import Request
from slugify import slugify
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import BroadcastSettings, QueueItem, QueueItemStatus, Station
from app.services.broadcast_settings import get_broadcast_settings
from app.schemas import (
    StationAdmin,
    StationCreate,
    StationDetail,
    StationSummary,
    StationUpdate,
)
from app.services.stream_urls import public_stream_url
from app.services.queue import (
    bootstrap_station,
    delete_station_files,
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


async def station_to_summary(
    db: Session,
    station: Station,
    request: Request | None = None,
    listeners: int = 0,
    on_air: bool = False,
    *,
    mount_stats: dict | None = None,
    list_mode: bool = False,
    broadcast: BroadcastSettings | None = None,
) -> StationSummary:
    settings_row = broadcast or get_broadcast_settings(db)
    return StationSummary(
        slug=station.slug,
        name=station.name,
        description=_public_description(station.description),
        artwork_url=public_artwork_url(station, request),
        enabled=station.enabled,
        featured=station.featured,
        now_playing=await get_now_playing(
            db,
            station,
            mount_stats=mount_stats,
            include_knowledge=not list_mode,
        ),
        stream_url=public_stream_url(station, request),
        listeners=listeners,
        on_air=on_air,
        stream_epoch=get_stream_epoch(),
        knowledge_feature=settings.knowledge_feature,
        artist_bio_enabled=bool(settings_row.artist_bio_enabled),
    )


async def station_to_detail(
    db: Session,
    station: Station,
    request: Request | None = None,
    listeners: int = 0,
    on_air: bool = False,
    *,
    mount_stats: dict | None = None,
) -> StationDetail:
    summary = await station_to_summary(
        db,
        station,
        request,
        listeners=listeners,
        on_air=on_air,
        mount_stats=mount_stats,
        list_mode=False,
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


async def station_to_admin(
    db: Session, station: Station, queued_count: int, *, mount_stats: dict | None = None
) -> StationAdmin:
    detail = await station_to_detail(db, station, mount_stats=mount_stats)
    return StationAdmin(
        **detail.model_dump(),
        id=station.id,
        featured_order=station.featured_order,
        sort_order=station.sort_order,
        queue_target=station.queue_target,
        refresh_threshold=station.refresh_threshold,
        artist_separation_minutes=station.artist_separation_minutes,
        continuation_mode=station.continuation_mode,
        identity_seed_item_id=station.identity_seed_item_id or "",
        identity_anchor_id=station.identity_anchor_id or "",
        programming_json=station.programming_json or "",
        pool_count=pool_count(db, station.id),
        source_last_error=station.source_last_error or "",
        source_healthy=not station.source_last_error,
        buffer_minutes=_queue_buffer_minutes(db, station.id),
        queued_count=queued_count,
        created_at=station.created_at,
        has_uploaded_artwork=artwork_exists(station.slug),
        external_artwork_url=station.artwork_url or "",
    )


async def create_station_record(db: Session, payload: StationCreate) -> tuple[Session, Station]:
    """Returns (db, station) -- bootstrap_station below may release and reopen
    the DB session around external AudioMuse/Navidrome calls, so callers must
    continue with the returned (db, station), not the ones they passed in."""
    slug = _unique_slug(db, payload.name, payload.slug)
    mount = payload.icecast_mount
    if db.query(Station).filter(Station.icecast_mount == mount).first():
        raise ValueError(f"Icecast mount {mount} is already in use")

    # New stations (including those deployed by the AudioMuse plugin, which never
    # sends sort_order) always append to the end of the admin-defined order.
    max_sort_order = db.query(func.max(Station.sort_order)).scalar()
    next_sort_order = (max_sort_order + 1) if max_sort_order is not None else 0

    station = Station(
        name=payload.name,
        slug=slug,
        description=payload.description,
        artwork_url=payload.artwork_url,
        icecast_mount=mount,
        enabled=payload.enabled,
        sort_order=next_sort_order,
        source_type=payload.source_type.value,
        source_ref=payload.source_ref,
        queue_target=payload.queue_target,
        refresh_threshold=payload.refresh_threshold,
        artist_separation_minutes=payload.artist_separation_minutes,
        continuation_mode=payload.continuation_mode,
        identity_seed_item_id=payload.identity_seed_item_id,
        identity_anchor_id=payload.identity_anchor_id,
        programming_json=payload.programming_json or "",
    )
    db.add(station)
    db.commit()
    db.refresh(station)

    if payload.bootstrap_queue and station.enabled:
        db, station = await bootstrap_station(db, station)
        if payload.identity_seed_item_id:
            station.identity_seed_item_id = payload.identity_seed_item_id
        if payload.identity_anchor_id:
            station.identity_anchor_id = payload.identity_anchor_id
        db.commit()
    return db, station


# Featured cap scales with catalog size so it always fills whole rows of 3:
# fewer than 6 enabled stations and the Featured section doesn't show at all
# (gated client-side), 6-14 enabled stations allows one row (3), 15+ unlocks
# a second row (6) and stays there regardless of how large the catalog gets.
FEATURED_CAP_EXPANSION_THRESHOLD = 15
FEATURED_CAP_BASE = 3
FEATURED_CAP_EXPANDED = 6


def _max_featured_stations(db: Session) -> int:
    enabled_count = db.query(Station).filter(Station.enabled.is_(True)).count()
    if enabled_count >= FEATURED_CAP_EXPANSION_THRESHOLD:
        return FEATURED_CAP_EXPANDED
    return FEATURED_CAP_BASE


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
    if data.get("featured") and not station.featured:
        featured_count = (
            db.query(Station)
            .filter(Station.featured.is_(True), Station.id != station.id)
            .count()
        )
        max_featured = _max_featured_stations(db)
        if featured_count >= max_featured:
            raise ValueError(f"At most {max_featured} stations can be featured at once")
        if "featured_order" not in data:
            max_order = (
                db.query(Station.featured_order)
                .filter(Station.featured.is_(True))
                .order_by(Station.featured_order.desc())
                .first()
            )
            data["featured_order"] = (max_order[0] + 1) if max_order else 0
    for key, value in data.items():
        if key == "source_type" and value is not None:
            value = value.value if hasattr(value, "value") else value
        setattr(station, key, value)
    if data.get("artwork_url"):
        delete_artwork(station.slug)
    db.commit()
    db.refresh(station)
    return station
