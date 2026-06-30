"""Tiered queue refill: import batches into a persistent pool, then expand by identity."""

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.database import (
    ContinuationMode,
    SourceType,
    Station,
    StationPoolItem,
)
from app.schemas import TrackRef
from app.services.audiomuse import audiomuse_client

logger = logging.getLogger(__name__)

BOOTSTRAP_FETCH_LIMIT = 500


def filter_track_refs(
    refs: list[TrackRef],
    exclude: set[str],
    blocked_artists: set[str],
    target: int,
) -> list[TrackRef]:
    filtered: list[TrackRef] = []
    seen_artists: set[str] = set()
    for ref in refs:
        if ref.item_id in exclude:
            continue
        artist_key = ref.artist.lower()
        if artist_key in blocked_artists or artist_key in seen_artists:
            continue
        filtered.append(ref)
        seen_artists.add(artist_key)
        if len(filtered) >= target:
            break
    return filtered


async def fetch_programming_batch(station: Station, count: int) -> list[TrackRef]:
    """Best-effort batch from the station's AudioMuse programming source."""
    return await audiomuse_client.fetch_tracks(
        station.source_type,
        station.source_ref,
        count,
    )


def import_batch_to_pool(db: Session, station: Station, refs: list[TrackRef]) -> int:
    """Persist tracks into the station pool (survives source/playlist deletion)."""
    if not refs:
        return 0
    existing = {
        r[0]
        for r in db.query(StationPoolItem.item_id)
        .filter(StationPoolItem.station_id == station.id)
        .all()
    }
    added = 0
    now = datetime.utcnow()
    for ref in refs:
        if ref.item_id in existing:
            continue
        db.add(
            StationPoolItem(
                station_id=station.id,
                item_id=ref.item_id,
                title=ref.title,
                artist=ref.artist,
                imported_at=now,
            )
        )
        existing.add(ref.item_id)
        added += 1
    return added


def pool_track_refs(db: Session, station: Station) -> list[TrackRef]:
    rows = (
        db.query(StationPoolItem)
        .filter(StationPoolItem.station_id == station.id)
        .order_by(StationPoolItem.imported_at.asc(), StationPoolItem.id.asc())
        .all()
    )
    return [TrackRef(item_id=r.item_id, title=r.title, artist=r.artist) for r in rows]


def pool_count(db: Session, station_id: int) -> int:
    return db.query(StationPoolItem).filter(StationPoolItem.station_id == station_id).count()


def last_played_item_id(db: Session, station_id: int) -> str | None:
    from app.database import PlayHistory

    row = (
        db.query(PlayHistory.item_id)
        .filter(PlayHistory.station_id == station_id, PlayHistory.item_id != "")
        .order_by(PlayHistory.played_at.desc())
        .first()
    )
    return row[0] if row else None


def establish_station_identity(station: Station, batch: list[TrackRef]) -> None:
    """Lock sonic identity from the bootstrap batch."""
    if not batch:
        return

    if station.source_type == SourceType.alchemy_anchor.value:
        station.identity_anchor_id = station.source_ref
    if station.source_type == SourceType.similar_seed.value:
        station.identity_seed_item_id = station.source_ref
    elif not station.identity_seed_item_id:
        station.identity_seed_item_id = batch[len(batch) // 2].item_id

    if station.source_type == SourceType.alchemy_anchor.value and not station.identity_anchor_id:
        station.identity_anchor_id = station.source_ref


def _rotate_pool(pool: list[TrackRef], offset: int) -> list[TrackRef]:
    if not pool or offset <= 0:
        return pool
    offset %= len(pool)
    return pool[offset:] + pool[:offset]


async def collect_refill_candidates(
    db: Session,
    station: Station,
    target: int,
    exclude: set[str],
    blocked_artists: set[str],
    queued_ids: set[str],
    *,
    play_count: int,
) -> tuple[list[TrackRef], str | None]:
    """
    Tiered refill. Returns (candidates, source_error).
    Tiers: programming batch → pool reuse → anchor → similar seed → similar last.
    """
    collected: list[TrackRef] = []
    source_error: str | None = None
    local_exclude = set(exclude)

    def need_more() -> int:
        return target - len(collected)

    def append_tier(refs: list[TrackRef], label: str, *, allow_recent_repeats: bool = False) -> None:
        nonlocal local_exclude
        if not refs or need_more() <= 0:
            return
        ex = (queued_ids | {r.item_id for r in collected}) if allow_recent_repeats else local_exclude
        picked = filter_track_refs(refs, ex, blocked_artists, need_more())
        if picked:
            logger.info("Station %s: tier %s added %s tracks", station.slug, label, len(picked))
            collected.extend(picked)
            local_exclude |= {r.item_id for r in picked}

    # Tier 0 — new recommendation batch (best effort; failure is OK)
    try:
        batch = await fetch_programming_batch(station, max(target * 3, 60))
        if batch:
            import_batch_to_pool(db, station, batch)
            station.source_last_ok_at = datetime.utcnow()
            station.source_last_error = ""
        append_tier(batch, "programming-batch")
    except Exception as exc:
        source_error = str(exc)
        station.source_last_error = source_error[:500]
        logger.warning("Station %s: programming batch failed: %s", station.slug, exc)

    # Tier 1 — imported pool (repeats allowed under source_only)
    pool = _rotate_pool(pool_track_refs(db, station), play_count)
    append_tier(pool, "pool")
    if need_more() > 0 and station.continuation_mode == ContinuationMode.source_only:
        append_tier(pool, "pool-repeats", allow_recent_repeats=True)
        if need_more() > 0:
            picked = filter_track_refs(
                pool,
                queued_ids | {r.item_id for r in collected},
                set(),
                need_more(),
            )
            if picked:
                collected.extend(picked)

    if station.continuation_mode == ContinuationMode.source_only:
        return collected, source_error

    # Tier 2 — alchemy anchor (station identity)
    anchor_id = station.identity_anchor_id or (
        station.source_ref if station.source_type == SourceType.alchemy_anchor.value else None
    )
    if need_more() > 0 and anchor_id:
        try:
            anchor_batch = await audiomuse_client.fetch_tracks(
                SourceType.alchemy_anchor.value, anchor_id, need_more() * 3
            )
            import_batch_to_pool(db, station, anchor_batch)
            append_tier(anchor_batch, "anchor")
        except Exception as exc:
            logger.warning("Station %s: anchor tier failed: %s", station.slug, exc)

    # Tier 3 — similar to fixed seed (no drift)
    seed = station.identity_seed_item_id or (
        station.source_ref if station.source_type == SourceType.similar_seed.value else None
    )
    if need_more() > 0 and seed:
        try:
            similar = await audiomuse_client.fetch_similar_tracks(seed, need_more() * 3)
            import_batch_to_pool(db, station, similar)
            append_tier(similar, "similar-seed")
        except Exception as exc:
            logger.warning("Station %s: similar-seed tier failed: %s", station.slug, exc)

    # Tier 4 — similar to last played (only when explicitly allowed)
    if need_more() > 0 and station.continuation_mode == ContinuationMode.similar_to_last:
        last_id = last_played_item_id(db, station.id) or seed
        if last_id:
            try:
                drift = await audiomuse_client.fetch_similar_tracks(last_id, need_more() * 3)
                import_batch_to_pool(db, station, drift)
                append_tier(drift, "similar-last")
            except Exception as exc:
                logger.warning("Station %s: similar-last tier failed: %s", station.slug, exc)

    return collected, source_error


async def fetch_bootstrap_batch(station: Station) -> list[TrackRef]:
    """Initial import — grab as much of the source as we can for the station pool."""
    limit = max(station.queue_target * 2, BOOTSTRAP_FETCH_LIMIT)
    return await fetch_programming_batch(station, limit)
