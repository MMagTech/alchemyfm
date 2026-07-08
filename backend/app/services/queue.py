import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import PlayHistory, QueueItem, QueueItemStatus, Station, StationPoolItem
from app.services.icecast import fetch_mount_now_playing
from app.services.navidrome import navidrome_client
from app.services.refill import (
    collect_refill_candidates,
    establish_station_identity,
    fetch_bootstrap_batch,
    filter_track_refs,
    import_batch_to_pool,
)
from app.schemas import KnowledgeBlock, NowPlaying, TrackRef, TrackInfo

logger = logging.getLogger(__name__)

def station_dir(slug: str) -> Path:
    path = Path(settings.stations_root) / slug
    path.mkdir(parents=True, exist_ok=True)
    return path


def queue_m3u_path(slug: str) -> Path:
    return station_dir(slug) / "queue.m3u"


def _format_m3u_line(track: TrackInfo) -> str:
    label = f"{track.artist} - {track.title}".replace(",", " ")
    duration = track.duration_sec or -1
    return f"#EXTINF:{duration},{label}\n{navidrome_client.stream_url(track.item_id)}"


def rebuild_m3u_from_db(db: Session, station: Station) -> None:
    """Rewrite queue.m3u from pending queue items with fresh Navidrome stream URLs."""
    items = (
        db.query(QueueItem)
        .filter(
            QueueItem.station_id == station.id,
            QueueItem.status.in_([QueueItemStatus.queued, QueueItemStatus.playing]),
        )
        .order_by(QueueItem.position.asc(), QueueItem.id.asc())
        .all()
    )
    lines = ["#EXTM3U"]
    for item in items:
        item.stream_url = navidrome_client.stream_url(item.item_id)
        track = TrackInfo(
            item_id=item.item_id,
            title=item.title,
            artist=item.artist,
            duration_sec=item.duration_sec,
        )
        lines.append(_format_m3u_line(track))
    db.commit()
    queue_m3u_path(station.slug).write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def rebuild_all_station_m3u(db: Session) -> None:
    for station in db.query(Station).filter(Station.enabled.is_(True)).all():
        rebuild_m3u_from_db(db, station)
        logger.info("Rebuilt queue.m3u for station %s", station.slug)


def _recent_item_ids(db: Session, station_id: int, limit: int = 200) -> set[str]:
    rows = (
        db.query(PlayHistory.item_id)
        .filter(PlayHistory.station_id == station_id, PlayHistory.item_id != "")
        .order_by(PlayHistory.played_at.desc())
        .limit(limit)
        .all()
    )
    return {r[0] for r in rows}


def _blocked_artists(db: Session, station: Station) -> set[str]:
    if station.artist_separation_minutes <= 0:
        return set()
    cutoff = datetime.utcnow() - timedelta(minutes=station.artist_separation_minutes)
    rows = (
        db.query(PlayHistory.artist)
        .filter(PlayHistory.station_id == station.id, PlayHistory.played_at >= cutoff)
        .all()
    )
    return {r[0].lower() for r in rows if r[0]}


def _next_position(db: Session, station_id: int) -> int:
    current = (
        db.query(func.max(QueueItem.position))
        .filter(QueueItem.station_id == station_id)
        .scalar()
    )
    return (current or 0) + 1


async def extend_queue(db: Session, station: Station, count: int | None = None) -> int:
    """Tiered refill into the station queue. Returns number added."""
    if not station.enabled:
        return 0

    target = count or station.queue_target
    blocked_ids = _recent_item_ids(db, station.id)
    blocked_artists = _blocked_artists(db, station)

    queued_ids = {
        r[0]
        for r in db.query(QueueItem.item_id)
        .filter(
            QueueItem.station_id == station.id,
            QueueItem.status.in_([QueueItemStatus.queued, QueueItemStatus.playing]),
        )
        .all()
    }
    exclude = blocked_ids | queued_ids

    play_count = (
        db.query(func.count(PlayHistory.id)).filter(PlayHistory.station_id == station.id).scalar()
    ) or 0

    filtered, _err = await collect_refill_candidates(
        db,
        station,
        target,
        exclude,
        blocked_artists,
        queued_ids,
        play_count=play_count,
    )
    db.commit()

    if not filtered:
        logger.warning("No new tracks to add for station %s", station.slug)
        return 0

    enriched = await navidrome_client.enrich_tracks(filtered)
    position = _next_position(db, station.id)
    for idx, track in enumerate(enriched):
        db.add(
            QueueItem(
                station_id=station.id,
                item_id=track.item_id,
                title=track.title,
                artist=track.artist,
                duration_sec=track.duration_sec,
                stream_url=navidrome_client.stream_url(track.item_id),
                position=position + idx,
                status=QueueItemStatus.queued,
            )
        )
    db.commit()
    rebuild_m3u_from_db(db, station)
    logger.info("Added %s tracks to station %s", len(enriched), station.slug)
    from app.knowledge.scheduler import schedule_knowledge_lookahead

    schedule_knowledge_lookahead(station.id)
    return len(enriched)


async def ensure_queue_fresh(db: Session, station: Station) -> None:
    remaining = (
        db.query(QueueItem)
        .filter(
            QueueItem.station_id == station.id,
            QueueItem.status == QueueItemStatus.queued,
        )
        .count()
    )
    if remaining < station.refresh_threshold:
        need = station.queue_target - remaining
        if need > 0:
            await extend_queue(db, station, need)


def _normalize_metadata(s: str) -> str:
    """Loose match for Icecast titles vs queue rows (spacing, commas, quotes)."""
    s = s.strip().lower()
    for ch in ("\u2018", "\u2019", "\u2032", "`", "\u201c", "\u201d"):
        s = s.replace(ch, "'")
    s = re.sub(r"[,.\-–—:;!?]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _normalize_title(s: str) -> str:
    return _normalize_metadata(s)


def _artist_variants(artist: str) -> set[str]:
    norm = _normalize_metadata(artist)
    variants = {norm}
    if ", " in artist:
        last, first = artist.split(", ", 1)
        variants.add(_normalize_metadata(f"{first} {last}"))
    if norm.endswith(" the"):
        base = norm[: -len(" the")].strip()
        if base:
            variants.add(f"the {base}")
    if norm.startswith("the "):
        base = norm[4:].strip()
        if base:
            variants.add(f"{base} the")
    return variants


def _titles_match(a: str, b: str) -> bool:
    return _normalize_title(a) == _normalize_title(b)


def _artists_match(a: str, b: str) -> bool:
    if not a.strip() or not b.strip():
        return True
    return bool(_artist_variants(a) & _artist_variants(b))


def _same_track(
    a_artist: str,
    a_title: str,
    a_item_id: str,
    b_artist: str,
    b_title: str,
    b_item_id: str,
) -> bool:
    if a_item_id and b_item_id and a_item_id == b_item_id:
        return True
    return _titles_match(a_title, b_title) and _artists_match(a_artist, b_artist)


def _find_queue_item_for_metadata(
    db: Session, station: Station, artist: str, title: str
) -> QueueItem | None:
    """Find the queue row that matches what's on air (search full queue, not just head)."""
    if not title.strip():
        return None

    pending = (
        db.query(QueueItem)
        .filter(
            QueueItem.station_id == station.id,
            QueueItem.status.in_([QueueItemStatus.queued, QueueItemStatus.playing]),
        )
        .order_by(QueueItem.position.asc(), QueueItem.id.asc())
        .all()
    )
    if not pending:
        return None

    for item in pending:
        if _titles_match(item.title, title) and _artists_match(artist, item.artist):
            return item
    for item in pending:
        if _titles_match(item.title, title):
            return item

    # On-air track may already be marked played before Icecast metadata is polled.
    recent_played = (
        db.query(QueueItem)
        .filter(
            QueueItem.station_id == station.id,
            QueueItem.status == QueueItemStatus.played,
            QueueItem.item_id != "",
        )
        .order_by(QueueItem.position.desc(), QueueItem.id.desc())
        .limit(40)
        .all()
    )
    for item in recent_played:
        if _titles_match(item.title, title) and _artists_match(artist, item.artist):
            return item
    for item in recent_played:
        if _titles_match(item.title, title):
            return item
    return None


def _lookup_item_id_from_catalog(
    db: Session, station: Station, artist: str, title: str
) -> str | None:
    """Resolve Navidrome id from any prior queue row (m3u replays played tracks)."""
    if not title.strip():
        return None

    exact = (
        db.query(QueueItem.item_id, QueueItem.artist, QueueItem.title)
        .filter(
            QueueItem.station_id == station.id,
            QueueItem.item_id != "",
            QueueItem.title == title.strip(),
        )
        .order_by(QueueItem.id.desc())
        .all()
    )
    for item_id, row_artist, _row_title in exact:
        if _artists_match(artist, row_artist):
            return item_id
    if exact:
        return exact[0][0]

    norm_title = _normalize_title(title)
    recent = (
        db.query(QueueItem.item_id, QueueItem.artist, QueueItem.title)
        .filter(
            QueueItem.station_id == station.id,
            QueueItem.item_id != "",
        )
        .order_by(QueueItem.id.desc())
        .limit(600)
        .all()
    )
    for item_id, row_artist, row_title in recent:
        if _normalize_title(row_title) == norm_title and _artists_match(artist, row_artist):
            return item_id
    for item_id, _row_artist, row_title in recent:
        if _normalize_title(row_title) == norm_title:
            return item_id
    return None


def _lookup_item_id(db: Session, station: Station, artist: str, title: str) -> str | None:
    matched = _find_queue_item_for_metadata(db, station, artist, title)
    if matched:
        return matched.item_id

    playing = (
        db.query(QueueItem)
        .filter(
            QueueItem.station_id == station.id,
            QueueItem.status == QueueItemStatus.playing,
            QueueItem.item_id != "",
        )
        .order_by(QueueItem.id.desc())
        .first()
    )
    if playing and _titles_match(playing.title, title):
        return playing.item_id

    played = (
        db.query(QueueItem)
        .filter(
            QueueItem.station_id == station.id,
            QueueItem.status == QueueItemStatus.played,
            QueueItem.item_id != "",
        )
        .order_by(QueueItem.position.desc(), QueueItem.id.desc())
        .limit(80)
        .all()
    )
    for item in played:
        if _titles_match(item.title, title) and _artists_match(artist, item.artist):
            return item.item_id
        if _titles_match(item.title, title):
            return item.item_id

    recent = (
        db.query(PlayHistory)
        .filter(PlayHistory.station_id == station.id)
        .order_by(PlayHistory.played_at.desc())
        .limit(100)
        .all()
    )
    for row in recent:
        if row.item_id and _titles_match(row.title, title) and _artists_match(artist, row.artist):
            return row.item_id
        if row.item_id and _titles_match(row.title, title):
            return row.item_id
    return _lookup_item_id_from_catalog(db, station, artist, title)


def _attach_knowledge(np: NowPlaying) -> NowPlaying:
    if not settings.knowledge_feature or not np.item_id:
        return np
    from app.knowledge.cache import facts_for_api
    from app.knowledge.database import KnowledgeSessionLocal

    kdb = KnowledgeSessionLocal()
    try:
        block = facts_for_api(kdb, np.item_id)
        if block:
            np.knowledge = KnowledgeBlock(**block)
    finally:
        kdb.close()
    return np


def _now_playing_from_item(
    item_id: str | None,
    artist: str,
    title: str,
    *,
    include_knowledge: bool = True,
) -> NowPlaying:
    np = NowPlaying(
        title=title,
        artist=artist,
        item_id=item_id or None,
        cover_url=navidrome_client.cover_art_url(item_id) if item_id else None,
    )
    if include_knowledge:
        return _attach_knowledge(np)
    return np


def get_now_playing(
    db: Session,
    station: Station,
    *,
    mount_stats: dict | None = None,
    include_knowledge: bool = True,
) -> NowPlaying | None:
    # Icecast title is what's actually on the wire — prefer it over DB state.
    ice = fetch_mount_now_playing(station.icecast_mount, mount_stats=mount_stats)
    if ice and (ice.artist or ice.title):
        item_id = _lookup_item_id(db, station, ice.artist, ice.title)
        artist, title = ice.artist, ice.title
        if item_id and not artist.strip():
            matched = _find_queue_item_for_metadata(db, station, artist, title)
            if matched:
                artist = matched.artist
        return _now_playing_from_item(
            item_id, artist, title, include_knowledge=include_knowledge
        )

    playing = (
        db.query(QueueItem)
        .filter(
            QueueItem.station_id == station.id,
            QueueItem.status == QueueItemStatus.playing,
        )
        .order_by(QueueItem.id.desc())
        .first()
    )
    if playing:
        return _now_playing_from_item(
            playing.item_id,
            playing.artist,
            playing.title,
            include_knowledge=include_knowledge,
        )

    last = (
        db.query(PlayHistory)
        .filter(PlayHistory.station_id == station.id)
        .order_by(PlayHistory.played_at.desc())
        .first()
    )
    if last:
        item_id = last.item_id or _lookup_item_id_from_catalog(
            db, station, last.artist, last.title
        )
        return _now_playing_from_item(
            item_id, last.artist, last.title, include_knowledge=include_knowledge
        )
    return None


def sync_station_from_icecast(
    db: Session, station: Station, last_track: tuple[str, str] | None
) -> tuple[str, str] | None:
    """Update queue/history when Icecast reports a new track."""
    ice = fetch_mount_now_playing(station.icecast_mount)
    if not ice or not ice.title.strip():
        return last_track
    current = (ice.artist, ice.title)
    if last_track and _same_track(last_track[0], last_track[1], "", current[0], current[1], ""):
        return last_track

    mark_track_started(db, station, ice.artist, ice.title)
    return current


def get_up_next(db: Session, station: Station, limit: int = 8) -> list[TrackRef]:
    items = (
        db.query(QueueItem)
        .filter(
            QueueItem.station_id == station.id,
            QueueItem.status == QueueItemStatus.queued,
        )
        .order_by(QueueItem.position.asc(), QueueItem.id.asc())
        .limit(limit)
        .all()
    )
    return [TrackRef(item_id=i.item_id, title=i.title, artist=i.artist) for i in items]


def get_recently_played(db: Session, station: Station, limit: int = 10) -> list[TrackRef]:
    rows = (
        db.query(PlayHistory)
        .filter(PlayHistory.station_id == station.id)
        .order_by(PlayHistory.played_at.desc())
        .limit(limit * 3)
        .all()
    )
    result: list[TrackRef] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key = (
            row.item_id or "",
            _normalize_title(row.title),
            _normalize_metadata(row.artist),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(TrackRef(item_id=row.item_id, title=row.title, artist=row.artist))
        if len(result) >= limit:
            break
    return result


def mark_track_started(db: Session, station: Station, artist: str, title: str) -> None:
    """Sync queue state when a track begins (from Liquidsoap or Icecast)."""
    if not title.strip():
        head = (
            db.query(QueueItem)
            .filter(
                QueueItem.station_id == station.id,
                QueueItem.status == QueueItemStatus.queued,
            )
            .order_by(QueueItem.position.asc(), QueueItem.id.asc())
            .first()
        )
        if not head:
            return
        artist, title = head.artist, head.title

    matched = _find_queue_item_for_metadata(db, station, artist, title)

    current_playing = (
        db.query(QueueItem)
        .filter(
            QueueItem.station_id == station.id,
            QueueItem.status == QueueItemStatus.playing,
        )
        .order_by(QueueItem.id.desc())
        .first()
    )
    matched_item_id = matched.item_id if matched else ""
    if current_playing and _same_track(
        artist,
        title,
        matched_item_id,
        current_playing.artist,
        current_playing.title,
        current_playing.item_id,
    ):
        return

    knowledge_item_id = ""
    pending = (
        db.query(QueueItem)
        .filter(
            QueueItem.station_id == station.id,
            QueueItem.status.in_([QueueItemStatus.queued, QueueItemStatus.playing]),
        )
        .order_by(QueueItem.position.asc(), QueueItem.id.asc())
        .all()
    )

    if matched:
        knowledge_item_id = matched.item_id
        passed = True
        for item in pending:
            if item.id == matched.id:
                item.status = QueueItemStatus.playing
                passed = False
            elif passed:
                item.status = QueueItemStatus.played
            else:
                break
        db.add(
            PlayHistory(
                station_id=station.id,
                item_id=matched.item_id,
                title=matched.title,
                artist=matched.artist,
            )
        )
    else:
        for item in pending:
            if item.status == QueueItemStatus.playing:
                item.status = QueueItemStatus.played
        logger.warning(
            "No queue match for on-air track '%s - %s' on station %s",
            artist,
            title,
            station.slug,
        )
        resolved_id = _lookup_item_id_from_catalog(db, station, artist, title) or ""
        knowledge_item_id = resolved_id
        db.add(
            PlayHistory(
                station_id=station.id,
                item_id=resolved_id,
                title=title,
                artist=artist,
            )
        )
    db.commit()
    rebuild_m3u_from_db(db, station)
    if knowledge_item_id:
        from app.knowledge.scheduler import schedule_now_playing_knowledge

        schedule_now_playing_knowledge(station.id, knowledge_item_id)


async def bootstrap_station(db: Session, station: Station) -> None:
    from datetime import datetime

    from app.services.liquidsoap import regenerate_liquidsoap_config

    db.query(QueueItem).filter(QueueItem.station_id == station.id).delete()
    db.query(StationPoolItem).filter(StationPoolItem.station_id == station.id).delete()

    station_dir(station.slug)
    queue_m3u_path(station.slug).write_text("#EXTM3U\n", encoding="utf-8")

    batch = await fetch_bootstrap_batch(station)
    if not batch:
        raise ValueError(f"No tracks imported for station {station.slug} — check source settings")

    import_batch_to_pool(db, station, batch)
    station.identity_seed_item_id = ""
    station.identity_anchor_id = ""
    establish_station_identity(station, batch)
    station.source_last_ok_at = datetime.utcnow()
    station.source_last_error = ""
    db.commit()

    to_queue = filter_track_refs(batch, set(), set(), station.queue_target)
    enriched = await navidrome_client.enrich_tracks(to_queue)
    position = 1
    for idx, track in enumerate(enriched):
        db.add(
            QueueItem(
                station_id=station.id,
                item_id=track.item_id,
                title=track.title,
                artist=track.artist,
                duration_sec=track.duration_sec,
                stream_url=navidrome_client.stream_url(track.item_id),
                position=position + idx,
                status=QueueItemStatus.queued,
            )
        )
    db.commit()
    rebuild_m3u_from_db(db, station)
    logger.info(
        "Bootstrapped station %s: pool=%s tracks, queue=%s",
        station.slug,
        len(batch),
        len(enriched),
    )
    regenerate_liquidsoap_config(db)
    from app.knowledge.scheduler import schedule_knowledge_lookahead

    schedule_knowledge_lookahead(station.id)


def delete_station_files(slug: str) -> None:
    path = Path(settings.stations_root) / slug
    if path.exists():
        for child in path.iterdir():
            child.unlink(missing_ok=True)
        path.rmdir()
