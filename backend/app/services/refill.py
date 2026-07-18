"""Tiered queue refill: import batches into a persistent pool, then expand by identity."""

import json
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.database import (
    ContinuationMode,
    SessionLocal,
    SourceType,
    Station,
    StationPoolItem,
)
from app.schemas import TrackRef
from app.services.audiomuse import audiomuse_client
from app.services.daypart import (
    local_hour,
    select_by_daypart,
    target_energy,
    target_mood,
)
from app.services.ordering import harmonic_order

logger = logging.getLogger(__name__)

BOOTSTRAP_FETCH_LIMIT = 500

LIVE_SOURCE_TYPES = frozenset(
    {
        SourceType.clap_query.value,
        SourceType.lyrics_query.value,
        SourceType.mood_centroid.value,
        SourceType.alchemy_anchor.value,
        SourceType.similar_seed.value,
        SourceType.journey.value,
    }
)


MAX_PER_ARTIST = 3


def filter_track_refs(
    refs: list[TrackRef],
    exclude: set[str],
    blocked_artists: set[str],
    target: int,
    *,
    max_per_artist: int = MAX_PER_ARTIST,
) -> list[TrackRef]:
    """Pick up to `target` refs, loosening soft rules rather than starving.

    Artist rules are preferences; repeating a track is not. So `exclude` (queued
    or just-played ids) is never relaxed, while the artist constraints give way
    one at a time. The first sweep is the old strict behaviour -- one track per
    artist -- so a healthy station picks exactly what it always did, and the
    later sweeps only run when that would return a short batch (thin library,
    narrow filters, or a pool dominated by a few artists).
    """
    picked: list[TrackRef] = []
    taken: set[str] = set()
    per_artist: dict[str, int] = {}

    def sweep(*, artist_cap: int | None, respect_separation: bool) -> None:
        for ref in refs:
            if len(picked) >= target:
                return
            if ref.item_id in exclude or ref.item_id in taken:
                continue
            artist_key = ref.artist.lower()
            if respect_separation and artist_key in blocked_artists:
                continue
            if artist_cap is not None and per_artist.get(artist_key, 0) >= artist_cap:
                continue
            picked.append(ref)
            taken.add(ref.item_id)
            per_artist[artist_key] = per_artist.get(artist_key, 0) + 1

    sweep(artist_cap=1, respect_separation=True)
    if len(picked) < target:
        sweep(artist_cap=max(1, max_per_artist), respect_separation=True)
    if len(picked) < target:
        sweep(artist_cap=None, respect_separation=True)
    if len(picked) < target:
        # Last resort: ignore artist separation. Still never repeats a track.
        sweep(artist_cap=None, respect_separation=False)
    return picked


async def fetch_programming_batch(station: Station, count: int) -> list[TrackRef]:
    """Best-effort batch from the station's AudioMuse programming source.

    Only safe to await while `station` is attached to a live session. When the
    caller releases the DB across the await, use programming_fetch_args() to
    snapshot the attributes first.
    """
    return await audiomuse_client.fetch_tracks(*programming_fetch_args(station, count))


def programming_fetch_args(station: Station, count: int) -> tuple[str, str, int, str | None]:
    """Snapshot the ORM attributes a programming fetch needs.

    An `async def` body runs at await time, not at call time. Callers that hand
    a coroutine to fetch_without_holding_db therefore execute it *after* the
    session was committed and closed -- and commit expires attributes
    (expire_on_commit defaults to True), so a lazy read there raises
    DetachedInstanceError and the whole programming tier is lost. Reading the
    values up front, while the session is still live, avoids that entirely.
    """
    return (
        station.source_type,
        station.source_ref,
        count,
        station.programming_json or None,
    )


def _station_profile(station: Station) -> dict:
    if not station.programming_json:
        return {}
    try:
        data = json.loads(station.programming_json)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _bootstrap_opener_refs(station: Station) -> list[TrackRef]:
    profile = _station_profile(station)
    bootstrap = profile.get("bootstrap") or {}
    if bootstrap.get("type") != "navidrome_playlist":
        return []
    refs: list[TrackRef] = []
    for item_id in bootstrap.get("resolved_ids") or []:
        if item_id:
            refs.append(TrackRef(item_id=str(item_id), title="Unknown", artist="Unknown"))
    return refs


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
    elif station.source_type == SourceType.similar_seed.value:
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
) -> tuple[list[TrackRef], str | None, Session, Station]:
    """
    Tiered refill. Returns (candidates, source_error, db, station).

    This runs on every single track change, on every station, all day,
    independent of whether anyone is listening. Each tier below can make an
    external AudioMuse network call, and holding one DB session/connection
    open across all of them (as this used to) was enough on its own to
    exhaust the connection pool under normal operation. Each network await
    now commits and releases the connection first, then reopens a fresh
    session and reattaches `station` to it afterward -- callers must use
    the returned (db, station), not the ones they passed in, since either
    may have been replaced.
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

    async def fetch_without_holding_db(coro):
        """Commit + release the connection before an external network
        call, then hand back a fresh session with `station` reattached --
        guaranteed even if the awaited call raises, so every tier below
        can keep using `db`/`station` normally afterward."""
        nonlocal db, station
        db.commit()
        db.close()
        try:
            return await coro
        finally:
            db = SessionLocal()
            station = db.merge(station)

    # Tier 0 — new recommendation batch (best effort; failure is OK)
    try:
        # Snapshot the station's attributes while the session is still live:
        # fetch_without_holding_db commits and closes before awaiting, and
        # commit expires attributes, so reading them inside the coroutine would
        # raise DetachedInstanceError and lose this tier on every refill.
        batch = await fetch_without_holding_db(
            audiomuse_client.fetch_tracks(*programming_fetch_args(station, max(target * 3, 60)))
        )
        if batch:
            import_batch_to_pool(db, station, batch)
            station.source_last_ok_at = datetime.utcnow()
            station.source_last_error = ""
        append_tier(batch, "programming-batch")
    except Exception as exc:
        source_error = str(exc)
        station.source_last_error = source_error[:500]
        logger.warning("Station %s: programming batch failed: %s", station.slug, exc)

    # Tier 1 — imported pool (repeats allowed only under source_only)
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

    if station.continuation_mode in (
        ContinuationMode.source_only,
        ContinuationMode.programming_only,
    ):
        return collected, source_error, db, station

    # Tier 2 — alchemy anchor (station identity)
    anchor_id = station.identity_anchor_id or (
        station.source_ref if station.source_type == SourceType.alchemy_anchor.value else None
    )
    if need_more() > 0 and anchor_id:
        try:
            anchor_batch = await fetch_without_holding_db(
                audiomuse_client.fetch_tracks(SourceType.alchemy_anchor.value, anchor_id, need_more() * 3)
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
            similar = await fetch_without_holding_db(
                audiomuse_client.fetch_similar_tracks(seed, need_more() * 3)
            )
            import_batch_to_pool(db, station, similar)
            append_tier(similar, "similar-seed")
        except Exception as exc:
            logger.warning("Station %s: similar-seed tier failed: %s", station.slug, exc)

    # Tier 4 — similar to last played (drift modes only)
    if need_more() > 0 and station.continuation_mode in (
        ContinuationMode.similar_to_last,
        ContinuationMode.no_repeats,
    ):
        last_id = last_played_item_id(db, station.id) or seed
        if last_id:
            try:
                drift = await fetch_without_holding_db(
                    audiomuse_client.fetch_similar_tracks(last_id, need_more() * 3)
                )
                import_batch_to_pool(db, station, drift)
                append_tier(drift, "similar-last")
            except Exception as exc:
                logger.warning("Station %s: similar-last tier failed: %s", station.slug, exc)

    return collected, source_error, db, station


def harmonic_ordering_enabled(station: Station) -> bool:
    """Whether this station opted into smooth (harmonic/tempo) refill ordering."""
    profile = _station_profile(station)
    ordering = profile.get("ordering")
    return bool(isinstance(ordering, dict) and ordering.get("harmonic"))


def daypart_level_now(station: Station) -> float | None:
    """Target energy level [0,1] for this station's current local hour, or None."""
    profile = _station_profile(station)
    daypart = profile.get("daypart")
    if not isinstance(daypart, dict) or not daypart.get("enabled"):
        return None
    hour = local_hour(str(daypart.get("timezone") or ""))
    return target_energy(str(daypart.get("preset") or ""), hour)


def daypart_mood_now(station: Station) -> str | None:
    """Mood tag to favour for this station's current local hour, or None."""
    profile = _station_profile(station)
    daypart = profile.get("daypart")
    if not isinstance(daypart, dict) or not daypart.get("enabled"):
        return None
    preset = str(daypart.get("mood_preset") or "").strip()
    if not preset or preset == "off":
        return None
    hour = local_hour(str(daypart.get("timezone") or ""))
    return target_mood(preset, hour)


async def shape_refill_batch(
    refs: list[TrackRef],
    seed_id: str | None,
    target: int,
    *,
    harmonic: bool,
    daypart_level: float | None,
    daypart_mood: str | None = None,
) -> list[TrackRef]:
    """Trim/sequence a refill batch by daypart energy and/or harmonic mixing.

    A single AudioMuse score call (tempo/key/scale/energy) feeds both concerns:
    daypart selects the tracks closest to the hour's target energy, then harmonic
    ordering chains them into a smooth key/tempo sequence. Best-effort — any
    failure or missing analysis falls back to the batch head trimmed to target,
    so a refill is never blocked. Caller must not hold a DB connection across
    this await.
    """
    if not refs or (not harmonic and daypart_level is None and not daypart_mood):
        return refs
    ids = [r.item_id for r in refs]
    if seed_id:
        ids = ids + [seed_id]
    try:
        scores = await audiomuse_client.fetch_scores(ids)
    except Exception as exc:
        logger.warning("Refill shaping: score fetch failed, keeping source order: %s", exc)
        return refs[:target] if len(refs) > target else refs

    result = refs
    if daypart_level is not None or daypart_mood:
        result = select_by_daypart(
            result,
            scores,
            target,
            energy_level=daypart_level,
            mood_tag=daypart_mood,
        )
    elif len(result) > target:
        result = result[:target]

    if harmonic:
        seed_score = scores.get(seed_id) if seed_id else None
        result = harmonic_order(result, scores, seed_score=seed_score)
    return result


async def fetch_bootstrap_batch(station: Station) -> list[TrackRef]:
    """Initial import — grab as much of the source as we can for the station pool."""
    limit = max(station.queue_target * 2, BOOTSTRAP_FETCH_LIMIT)
    opener = _bootstrap_opener_refs(station)
    programming = await fetch_programming_batch(station, limit)
    seen: set[str] = set()
    merged: list[TrackRef] = []
    for ref in opener + programming:
        if ref.item_id in seen:
            continue
        seen.add(ref.item_id)
        merged.append(ref)
    return merged
