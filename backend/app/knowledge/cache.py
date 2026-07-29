import json
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.knowledge.database import TrackKnowledge, TrackKnowledgeStatus
from app.knowledge.settings import display_enabled, get_knowledge_settings

logger = logging.getLogger(__name__)

ALLOWED_CATEGORIES = frozenset(
    {"song_fact", "artist_fact", "album_fact", "producer_fact", "sample_fact"}
)


def _parse_payload(row: TrackKnowledge) -> dict:
    try:
        return json.loads(row.payload_json or "{}")
    except json.JSONDecodeError:
        return {}


def is_cache_valid(row: TrackKnowledge | None, now: datetime | None = None) -> bool:
    if not row:
        return False
    now = now or datetime.utcnow()
    return row.expires_at > now


def get_track_knowledge(
    db: Session, item_id: str, now: datetime | None = None
) -> TrackKnowledge | None:
    if not item_id:
        return None
    row = db.query(TrackKnowledge).filter(TrackKnowledge.item_id == item_id).first()
    if not is_cache_valid(row, now):
        return None
    return row


def facts_for_api(db: Session, item_id: str | None) -> dict | None:
    """Return knowledge block for now_playing, or None if hidden / unavailable."""
    if not item_id:
        return None
    settings = get_knowledge_settings(db)
    if not display_enabled(settings.mode):
        return None
    row = get_track_knowledge(db, item_id)
    if not row or row.status != TrackKnowledgeStatus.ready:
        return None
    payload = _parse_payload(row)
    facts = payload.get("facts") or []
    if not facts:
        return None
    limit = min(settings.display_fact_count, settings.max_facts_per_track, len(facts))
    trimmed = facts[:limit]
    return {
        "status": "ready",
        "facts": trimmed,
        "rotation_interval_sec": settings.rotation_interval_sec,
    }


def save_track_knowledge(
    db: Session,
    *,
    item_id: str,
    status: TrackKnowledgeStatus,
    title: str,
    artist: str,
    album: str,
    year: int | None,
    payload: dict,
    ttl_days: int,
    failure_reason: str = "",
) -> None:
    from app.knowledge.settings import expiry_from_now

    now = datetime.utcnow()
    expires = expiry_from_now(ttl_days)
    row = db.query(TrackKnowledge).filter(TrackKnowledge.item_id == item_id).first()
    payload_json = json.dumps(payload, ensure_ascii=False)
    if row:
        row.status = status
        row.title = title
        row.artist = artist
        row.album = album
        row.year = year
        row.payload_json = payload_json
        row.updated_at = now
        row.expires_at = expires
        row.failure_reason = failure_reason
    else:
        db.add(
            TrackKnowledge(
                item_id=item_id,
                status=status,
                title=title,
                artist=artist,
                album=album,
                year=year,
                payload_json=payload_json,
                created_at=now,
                updated_at=now,
                expires_at=expires,
                failure_reason=failure_reason,
            )
        )
    db.commit()


def purge_all_cache(db: Session) -> int:
    """Clear all cached facts and the whole job table."""
    from app.knowledge.database import KnowledgeJob

    count = db.query(TrackKnowledge).count()
    db.query(TrackKnowledge).delete()
    # Everything, not just in-flight work. Leaving `failed` rows behind kept a
    # stale "last job error" and a Jobs Failed count that never reset, so an old
    # failure was indistinguishable from a new one. Nothing ever pruned `done`
    # rows either, so they grew unbounded -- one per enrichment, forever.
    db.query(KnowledgeJob).delete(synchronize_session=False)
    db.commit()
    return count


def purge_track(db: Session, item_id: str) -> None:
    from app.knowledge.database import KnowledgeJob, KnowledgeJobStatus

    db.query(TrackKnowledge).filter(TrackKnowledge.item_id == item_id).delete()
    db.query(KnowledgeJob).filter(KnowledgeJob.item_id == item_id).delete()
    db.commit()


def validate_facts(facts: list[dict], min_confidence: float, max_chars: int) -> list[dict]:
    clean: list[dict] = []
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        category = fact.get("category")
        text = str(fact.get("text") or "").strip()
        confidence = float(fact.get("confidence") or 0)
        sources = fact.get("sources") or []
        if category not in ALLOWED_CATEGORIES:
            continue
        if not text or confidence < min_confidence:
            continue
        if len(text) > max_chars:
            text = text[: max_chars - 1].rstrip() + "…"
        valid_sources = []
        for src in sources:
            if not isinstance(src, dict):
                continue
            url = str(src.get("url") or "").strip()
            if url.startswith("http"):
                valid_sources.append(
                    {
                        "url": url,
                        "title": str(src.get("title") or url)[:200],
                    }
                )
        if not valid_sources:
            continue
        clean.append(
            {
                "category": category,
                "text": text,
                "confidence": round(confidence, 3),
                "sources": valid_sources[:3],
            }
        )
    clean.sort(key=lambda f: f["confidence"], reverse=True)
    return clean
