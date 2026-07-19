import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.config import settings
from app.knowledge.cache import purge_all_cache, purge_track
from app.knowledge.database import (
    KnowledgeJob,
    KnowledgeJobStatus,
    KnowledgeMode,
    KnowledgeSessionLocal,
    TrackKnowledge,
    TrackKnowledgeStatus,
    get_knowledge_db,
)
from app.knowledge.llm import test_connection as test_llm
from app.knowledge.scheduler import (
    enqueue_refresh,
    schedule_all_stations_lookahead,
    stop_enrichment_jobs,
)
from app.knowledge.searxng import test_connection as test_searxng
from app.knowledge.sources import test_connection as test_search_sources
from app.knowledge.settings import (
    effective_llm_base_url,
    effective_llm_model,
    effective_llm_provider,
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
    KnowledgeCacheFact,
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
        llm_provider=effective_llm_provider(row),
        llm_base_url=effective_llm_base_url(row),
        llm_model=effective_llm_model(row),
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
async def read_knowledge_settings(test: bool = Query(default=False)):
    """Doesn't use Depends(get_knowledge_db): the test=true branch awaits
    SearXNG/LLM connection checks, which are real network calls (Ollama
    especially, if it's cold-loading a model; a cloud provider likewise, over
    the internet). Holding a request-scoped session open across those -- on an
    engine that, unlike radio.db's, never got its pool widened -- risks
    exhausting the knowledge DB pool for the same reason the
    track_started/listen/get_station fixes exist.
    """
    db = KnowledgeSessionLocal()
    try:
        _require_feature()
        row = get_knowledge_settings(db)
        base = _build_settings_read(db, row, _last_failed_job(db))
        searxng_url = effective_searxng_url(row)
        llm_provider = effective_llm_provider(row)
    finally:
        db.close()

    if not test:
        return base
    search_ok, search_msg = await test_search_sources(searxng_url)
    searxng_ok, searxng_msg = (None, "")
    if searxng_url:
        searxng_ok, searxng_msg = await test_searxng(searxng_url)
    # test_llm() resolves the backend from env only -- no DB row -- so it is
    # safe to await here, after the session above has been closed.
    llm_ok, llm_msg = await test_llm()
    # Keep the legacy ollama_* fields populated only when Ollama is the backend.
    ollama_ok, ollama_msg = (llm_ok, llm_msg) if llm_provider == "ollama" else (None, "")
    return base.model_copy(
        update={
            "search_ok": search_ok,
            "search_message": search_msg or "",
            "searxng_ok": searxng_ok,
            "searxng_message": searxng_msg or "",
            "ollama_ok": ollama_ok,
            "ollama_message": ollama_msg or "",
            "llm_ok": llm_ok,
            "llm_message": llm_msg or "",
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
    status: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=200),
    db: Session = Depends(get_knowledge_db),
):
    _require_feature()
    query = db.query(TrackKnowledge)
    if status:
        try:
            query = query.filter(TrackKnowledge.status == TrackKnowledgeStatus(status))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid status") from exc
    if q and q.strip():
        # Status filters alone can't find one track in a cache of hundreds.
        term = f"%{q.strip()}%"
        query = query.filter(
            or_(
                TrackKnowledge.title.ilike(term),
                TrackKnowledge.artist.ilike(term),
            )
        )
    total = query.count()
    rows = (
        query.order_by(TrackKnowledge.updated_at.desc())
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
                facts=[
                    KnowledgeCacheFact(
                        category=str(f.get("category") or ""),
                        text=str(f.get("text") or ""),
                        confidence=float(f.get("confidence") or 0),
                    )
                    for f in facts
                    if isinstance(f, dict)
                ],
                failure_reason=row.failure_reason or "",
                updated_at=row.updated_at,
                expires_at=row.expires_at,
            )
        )
    return KnowledgeCacheList(items=items, total=total)
