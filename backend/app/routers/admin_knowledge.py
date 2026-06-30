import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.config import settings
from app.knowledge.cache import purge_all_cache, purge_track
from app.knowledge.database import (
    KnowledgeJob,
    KnowledgeJobStatus,
    KnowledgeMode,
    TrackKnowledge,
    TrackKnowledgeStatus,
    get_knowledge_db,
)
from app.knowledge.ollama import test_connection as test_ollama
from app.knowledge.scheduler import (
    enqueue_refresh,
    schedule_all_stations_lookahead,
    stop_enrichment_jobs,
)
from app.knowledge.searxng import test_connection as test_searxng
from app.knowledge.sources import test_connection as test_search_sources
from app.knowledge.settings import (
    effective_ollama_model,
    effective_ollama_url,
    effective_searxng_url,
    get_knowledge_settings,
    knowledge_feature_enabled,
    processing_enabled,
    update_knowledge_settings,
)
from app.knowledge.worker import request_ollama_gpu_release
from app.schemas import (
    KnowledgeCacheEntry,
    KnowledgeCacheList,
    KnowledgePurgeResponse,
    KnowledgeSettingsRead,
    KnowledgeSettingsUpdate,
)

router = APIRouter(
    prefix="/api/admin/knowledge",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


def _require_feature() -> None:
    if not knowledge_feature_enabled():
        raise HTTPException(status_code=404, detail="Knowledge feature is not enabled")


def _build_settings_read(db: Session, row, last_err) -> KnowledgeSettingsRead:
    return KnowledgeSettingsRead(
        feature_enabled=True,
        mode=row.mode.value,
        cache_ttl_days=row.cache_ttl_days,
        lookahead_count=row.lookahead_count,
        max_concurrent_jobs=row.max_concurrent_jobs,
        max_facts_per_track=row.max_facts_per_track,
        max_fact_chars=row.max_fact_chars,
        display_fact_count=row.display_fact_count,
        rotation_interval_sec=row.rotation_interval_sec,
        min_confidence=row.min_confidence,
        searxng_url=effective_searxng_url(row),
        ollama_url=effective_ollama_url(row),
        ollama_model=effective_ollama_model(row),
        providers_from_env=True,
        cache_entries=db.query(TrackKnowledge).count(),
        cache_ready=db.query(TrackKnowledge)
        .filter(TrackKnowledge.status == TrackKnowledgeStatus.ready)
        .count(),
        jobs_pending=db.query(KnowledgeJob)
        .filter(KnowledgeJob.status == KnowledgeJobStatus.pending)
        .count(),
        jobs_running=db.query(KnowledgeJob)
        .filter(KnowledgeJob.status == KnowledgeJobStatus.running)
        .count(),
        jobs_failed=db.query(KnowledgeJob)
        .filter(KnowledgeJob.status == KnowledgeJobStatus.failed)
        .count(),
        jobs_cancelled=db.query(KnowledgeJob)
        .filter(KnowledgeJob.status == KnowledgeJobStatus.cancelled)
        .count(),
        last_job_error=(last_err.last_error if last_err else "") or "",
    )


def _last_failed_job(db: Session) -> KnowledgeJob | None:
    return (
        db.query(KnowledgeJob)
        .filter(KnowledgeJob.status == KnowledgeJobStatus.failed)
        .order_by(KnowledgeJob.finished_at.desc())
        .first()
    )


@router.get("", response_model=KnowledgeSettingsRead)
async def read_knowledge_settings(
    test: bool = Query(default=False),
    db: Session = Depends(get_knowledge_db),
):
    _require_feature()
    row = get_knowledge_settings(db)
    base = _build_settings_read(db, row, _last_failed_job(db))
    if not test:
        return base
    searxng_url = effective_searxng_url(row)
    search_ok, search_msg = await test_search_sources(searxng_url)
    searxng_ok, searxng_msg = (None, "")
    if searxng_url:
        searxng_ok, searxng_msg = await test_searxng(searxng_url)
    ollama_ok, ollama_msg = await test_ollama(
        effective_ollama_url(row), effective_ollama_model(row)
    )
    return base.model_copy(
        update={
            "search_ok": search_ok,
            "search_message": search_msg or "",
            "searxng_ok": searxng_ok,
            "searxng_message": searxng_msg or "",
            "ollama_ok": ollama_ok,
            "ollama_message": ollama_msg or "",
        }
    )


@router.put("", response_model=KnowledgeSettingsRead)
async def save_knowledge_settings(
    payload: KnowledgeSettingsUpdate,
    db: Session = Depends(get_knowledge_db),
):
    _require_feature()
    data = payload.model_dump(exclude_unset=True)
    if "mode" in data and data["mode"] is not None:
        try:
            data["mode"] = KnowledgeMode(data["mode"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid mode") from exc
    row = update_knowledge_settings(db, data)
    if processing_enabled(row.mode):
        schedule_all_stations_lookahead()
    else:
        stop_enrichment_jobs(db)
        request_ollama_gpu_release()
    return _build_settings_read(db, row, _last_failed_job(db))


@router.post("/purge", response_model=KnowledgePurgeResponse)
def purge_knowledge_cache(db: Session = Depends(get_knowledge_db)):
    _require_feature()
    deleted = purge_all_cache(db)
    return KnowledgePurgeResponse(deleted=deleted)


@router.post("/refresh/{item_id}")
def refresh_track_knowledge(item_id: str, db: Session = Depends(get_knowledge_db)):
    _require_feature()
    purge_track(db, item_id)
    enqueue_refresh(item_id)
    return {"ok": True, "item_id": item_id}


@router.get("/cache", response_model=KnowledgeCacheList)
def list_knowledge_cache(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_knowledge_db),
):
    _require_feature()
    total = db.query(TrackKnowledge).count()
    rows = (
        db.query(TrackKnowledge)
        .order_by(TrackKnowledge.updated_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    items: list[KnowledgeCacheEntry] = []
    for row in rows:
        try:
            payload = json.loads(row.payload_json or "{}")
        except json.JSONDecodeError:
            payload = {}
        facts = payload.get("facts") or []
        items.append(
            KnowledgeCacheEntry(
                item_id=row.item_id,
                status=row.status.value,
                title=row.title,
                artist=row.artist,
                album=row.album,
                year=row.year,
                fact_count=len(facts),
                updated_at=row.updated_at,
                expires_at=row.expires_at,
            )
        )
    return KnowledgeCacheList(items=items, total=total)
