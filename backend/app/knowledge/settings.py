from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.knowledge.database import KnowledgeMode, KnowledgeSettings


DEFAULTS = {
    "mode": KnowledgeMode.off,
    "cache_ttl_days": 180,
    "lookahead_count": 8,
    "max_concurrent_jobs": 1,
    "max_facts_per_track": 3,
    "max_fact_chars": 200,
    "display_fact_count": 3,
    "rotation_interval_sec": 15,
    "min_confidence": 0.6,
}


def knowledge_feature_enabled() -> bool:
    return bool(settings.knowledge_feature)


def processing_enabled(mode: KnowledgeMode) -> bool:
    return mode == KnowledgeMode.active


def display_enabled(mode: KnowledgeMode) -> bool:
    return mode in (KnowledgeMode.cached_only, KnowledgeMode.active)


def effective_searxng_url(_row: KnowledgeSettings | None = None) -> str:
    return (settings.searxng_url or "").rstrip("/")


def effective_ollama_url(_row: KnowledgeSettings | None = None) -> str:
    return (settings.ollama_url or "").rstrip("/")


def effective_ollama_model(_row: KnowledgeSettings | None = None) -> str:
    return settings.ollama_model or "llama3.2:3b"


def effective_llm_provider(_row: KnowledgeSettings | None = None) -> str:
    """"ollama" (local) or "openai" (OpenAI-compatible cloud). Env-only."""
    provider = (settings.knowledge_llm_provider or "ollama").strip().lower()
    return provider if provider in ("ollama", "openai") else "ollama"


def effective_llm_base_url(_row: KnowledgeSettings | None = None) -> str:
    return (settings.knowledge_llm_base_url or "").strip().rstrip("/")


def effective_llm_model(_row: KnowledgeSettings | None = None) -> str:
    return (settings.knowledge_llm_model or "").strip()


def effective_llm_api_key() -> str:
    """Secret — read from env only, never stored in the DB or returned by the API."""
    return (settings.knowledge_llm_api_key or "").strip()


def effective_model_label(row: KnowledgeSettings | None = None) -> str:
    """Model name recorded in the cache payload, provider-aware."""
    if effective_llm_provider(row) == "openai":
        return effective_llm_model(row) or "openai"
    return effective_ollama_model(row)


def get_knowledge_settings(db: Session) -> KnowledgeSettings:
    row = db.query(KnowledgeSettings).filter(KnowledgeSettings.id == 1).first()
    if row:
        return row
    row = KnowledgeSettings(id=1, **{k: v for k, v in DEFAULTS.items()})
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_knowledge_settings(db: Session, data: dict) -> KnowledgeSettings:
    row = get_knowledge_settings(db)
    for key, value in data.items():
        if value is not None and hasattr(row, key):
            setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row


def expiry_from_now(ttl_days: int) -> datetime:
    days = max(1, int(ttl_days))
    return datetime.utcnow() + timedelta(days=days)


async def release_ollama_gpu(row: KnowledgeSettings) -> None:
    from app.knowledge.ollama import release_gpu

    await release_gpu(effective_ollama_url(row), effective_ollama_model(row))
