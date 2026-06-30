import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.database import QueueItem, QueueItemStatus, SessionLocal, Station
from app.knowledge.cache import get_track_knowledge
from app.knowledge.database import KnowledgeJob, KnowledgeJobStatus, KnowledgeSessionLocal
from app.knowledge.settings import get_knowledge_settings, knowledge_feature_enabled, processing_enabled

logger = logging.getLogger(__name__)

STALE_JOB_MINUTES = 15


def running_jobs_count(db: Session) -> int:
    return (
        db.query(KnowledgeJob)
        .filter(KnowledgeJob.status == KnowledgeJobStatus.running)
        .count()
    )


def cancel_pending_jobs(db: Session, reason: str = "Stopped — mode is not Active") -> int:
    pending = (
        db.query(KnowledgeJob)
        .filter(KnowledgeJob.status == KnowledgeJobStatus.pending)
        .all()
    )
    now = datetime.utcnow()
    for job in pending:
        job.status = KnowledgeJobStatus.cancelled
        job.finished_at = now
        job.last_error = reason
    if pending:
        db.commit()
        logger.info("Cancelled %s pending knowledge job(s)", len(pending))
    return len(pending)


def repair_legacy_cancelled_jobs(db: Session) -> int:
    """Reclassify old pending jobs that were marked failed when mode changed."""
    legacy = (
        db.query(KnowledgeJob)
        .filter(
            KnowledgeJob.status == KnowledgeJobStatus.failed,
            KnowledgeJob.attempts == 0,
            KnowledgeJob.last_error.like("Cancelled%"),
        )
        .all()
    )
    for job in legacy:
        job.status = KnowledgeJobStatus.cancelled
    if legacy:
        db.commit()
        logger.info("Reclassified %s legacy cancelled knowledge job(s)", len(legacy))
    return len(legacy)


def stop_enrichment_jobs(db: Session, reason: str = "Stopped — mode is not Active") -> int:
    """Cancel pending work; running job (if any) is left to finish in the worker."""
    return cancel_pending_jobs(db, reason)


def _ensure_enrichment_job(
    kdb: Session,
    *,
    item_id: str,
    station_id: int,
    priority: bool = False,
) -> None:
    if not item_id or get_track_knowledge(kdb, item_id):
        return
    active = (
        kdb.query(KnowledgeJob)
        .filter(
            KnowledgeJob.item_id == item_id,
            KnowledgeJob.status.in_(
                [KnowledgeJobStatus.pending, KnowledgeJobStatus.running]
            ),
        )
        .first()
    )
    if active:
        if priority and active.status == KnowledgeJobStatus.pending:
            bump = datetime.utcnow() - timedelta(days=1)
            if active.scheduled_at > bump:
                active.scheduled_at = bump
        return
    scheduled_at = (
        datetime.utcnow() - timedelta(days=1) if priority else datetime.utcnow()
    )
    kdb.add(
        KnowledgeJob(
            item_id=item_id,
            station_id=station_id,
            status=KnowledgeJobStatus.pending,
            scheduled_at=scheduled_at,
        )
    )


def schedule_now_playing_knowledge(station_id: int, item_id: str) -> None:
    """Enqueue enrichment for the track that just went on air (priority)."""
    if not knowledge_feature_enabled() or not item_id:
        return
    kdb = KnowledgeSessionLocal()
    try:
        ksettings = get_knowledge_settings(kdb)
        if not processing_enabled(ksettings.mode):
            return
        _ensure_enrichment_job(
            kdb, item_id=item_id, station_id=station_id, priority=True
        )
        kdb.commit()
    except Exception:
        logger.exception(
            "Failed to schedule now-playing knowledge for %s", item_id
        )
        kdb.rollback()
    finally:
        kdb.close()


def reset_stale_running_jobs(db: Session) -> int:
    """Recover jobs left 'running' after a backend restart or crash."""
    cutoff = datetime.utcnow() - timedelta(minutes=STALE_JOB_MINUTES)
    stale = (
        db.query(KnowledgeJob)
        .filter(
            KnowledgeJob.status == KnowledgeJobStatus.running,
            KnowledgeJob.started_at < cutoff,
        )
        .all()
    )
    for job in stale:
        job.status = KnowledgeJobStatus.pending
        job.last_error = "Reset after interrupted run"
    if stale:
        db.commit()
        logger.info("Reset %s stale knowledge job(s) to pending", len(stale))
    return len(stale)


def schedule_knowledge_lookahead(station_id: int) -> None:
    if not knowledge_feature_enabled():
        return
    kdb = KnowledgeSessionLocal()
    rdb = SessionLocal()
    try:
        ksettings = get_knowledge_settings(kdb)
        if not processing_enabled(ksettings.mode):
            return
        lookahead = max(1, min(ksettings.lookahead_count, 20))
        items = (
            rdb.query(QueueItem)
            .filter(
                QueueItem.station_id == station_id,
                QueueItem.status == QueueItemStatus.queued,
            )
            .order_by(QueueItem.position.asc())
            .limit(lookahead)
            .all()
        )
        for item in items:
            if not item.item_id:
                continue
            _ensure_enrichment_job(
                kdb, item_id=item.item_id, station_id=station_id, priority=False
            )
        kdb.commit()
    except Exception:
        logger.exception("Failed to schedule knowledge lookahead for station %s", station_id)
        kdb.rollback()
    finally:
        kdb.close()
        rdb.close()


def schedule_all_stations_lookahead() -> None:
    """Enqueue uncached lookahead tracks for every enabled station."""
    if not knowledge_feature_enabled():
        return
    kdb = KnowledgeSessionLocal()
    rdb = SessionLocal()
    try:
        reset_stale_running_jobs(kdb)
        ksettings = get_knowledge_settings(kdb)
        if not processing_enabled(ksettings.mode):
            return
        stations = rdb.query(Station).filter(Station.enabled.is_(True)).all()
        for station in stations:
            schedule_knowledge_lookahead(station.id)
    except Exception:
        logger.exception("Failed to schedule knowledge lookahead for all stations")
    finally:
        kdb.close()
        rdb.close()


def enqueue_refresh(item_id: str, station_id: int = 0) -> None:
    if not knowledge_feature_enabled():
        return
    kdb = KnowledgeSessionLocal()
    try:
        kdb.query(KnowledgeJob).filter(
            KnowledgeJob.item_id == item_id,
            KnowledgeJob.status.in_([KnowledgeJobStatus.pending, KnowledgeJobStatus.running]),
        ).delete(synchronize_session=False)
        kdb.add(
            KnowledgeJob(
                item_id=item_id,
                station_id=station_id,
                status=KnowledgeJobStatus.pending,
            )
        )
        kdb.commit()
    finally:
        kdb.close()


def claim_next_job(db: Session) -> KnowledgeJob | None:
    job = (
        db.query(KnowledgeJob)
        .filter(KnowledgeJob.status == KnowledgeJobStatus.pending)
        .order_by(KnowledgeJob.scheduled_at.asc(), KnowledgeJob.id.asc())
        .first()
    )
    if not job:
        return None
    job.status = KnowledgeJobStatus.running
    job.started_at = datetime.utcnow()
    job.attempts += 1
    db.commit()
    db.refresh(job)
    return job


def finish_job(db: Session, job: KnowledgeJob, success: bool, error: str = "") -> None:
    job.finished_at = datetime.utcnow()
    job.status = KnowledgeJobStatus.done if success else KnowledgeJobStatus.failed
    job.last_error = error[:2000] if error else ""
    db.commit()
