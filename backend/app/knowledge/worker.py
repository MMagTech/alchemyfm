import asyncio
import logging

from app.config import settings
from app.knowledge.cache import purge_track, save_track_knowledge
from app.knowledge.database import (
    KnowledgeJobStatus,
    KnowledgeSessionLocal,
    TrackKnowledgeStatus,
)
from app.knowledge.enrich import enrich_item
from app.knowledge.scheduler import (
    claim_next_job,
    finish_job,
    reset_stale_running_jobs,
    running_jobs_count,
)
from app.knowledge.settings import (
    get_knowledge_settings,
    knowledge_feature_enabled,
    processing_enabled,
    release_ollama_gpu,
)

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
POLL_SEC = 3
_gpu_release_armed = True
_stale_jobs_reset = False
_finishing_running_job = False


async def _process_one_job() -> bool:
    global _stale_jobs_reset, _finishing_running_job
    kdb = KnowledgeSessionLocal()
    try:
        if not _stale_jobs_reset:
            reset_stale_running_jobs(kdb)
            _stale_jobs_reset = True
        ksettings = get_knowledge_settings(kdb)
        if not processing_enabled(ksettings.mode):
            if running_jobs_count(kdb):
                _finishing_running_job = True
            return False
        _finishing_running_job = False
        job = claim_next_job(kdb)
        if not job:
            return False
        item_id = job.item_id
        status, payload, error = await enrich_item(item_id, ksettings)
        track = payload.get("track") or {}
        save_track_knowledge(
            kdb,
            item_id=item_id,
            status=status,
            title=track.get("title") or "",
            artist=track.get("artist") or "",
            album=track.get("album") or "",
            year=track.get("year"),
            payload=payload,
            ttl_days=ksettings.cache_ttl_days,
            failure_reason=error,
        )
        if status == TrackKnowledgeStatus.failed and job.attempts < MAX_ATTEMPTS:
            job.status = KnowledgeJobStatus.pending
            job.last_error = error
            kdb.commit()
            return True
        finish_job(kdb, job, success=status != TrackKnowledgeStatus.failed, error=error)
        _finishing_running_job = False
        return True
    except Exception:
        logger.exception("Knowledge worker job error")
        kdb.rollback()
        return False
    finally:
        kdb.close()


async def _release_gpu_when_idle(ksettings) -> None:
    global _gpu_release_armed, _finishing_running_job
    if not _gpu_release_armed:
        return
    kdb = KnowledgeSessionLocal()
    try:
        if running_jobs_count(kdb) > 0 or _finishing_running_job:
            return
    finally:
        kdb.close()
    await release_ollama_gpu(ksettings)
    _gpu_release_armed = False
    _finishing_running_job = False


async def knowledge_worker_loop() -> None:
    global _gpu_release_armed
    while True:
        try:
            if knowledge_feature_enabled():
                kdb = KnowledgeSessionLocal()
                try:
                    ksettings = get_knowledge_settings(kdb)
                    active = processing_enabled(ksettings.mode)
                finally:
                    kdb.close()
                if active:
                    _gpu_release_armed = True
                    worked = await _process_one_job()
                    if not worked:
                        await asyncio.sleep(POLL_SEC)
                    continue
                await _release_gpu_when_idle(ksettings)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Knowledge worker loop error")
        await asyncio.sleep(POLL_SEC)


def request_ollama_gpu_release() -> None:
    """Call when admin switches away from Active — worker unloads on next tick."""
    global _gpu_release_armed
    _gpu_release_armed = True


def start_knowledge_worker() -> asyncio.Task | None:
    if not settings.knowledge_feature:
        return None
    return asyncio.create_task(knowledge_worker_loop())
