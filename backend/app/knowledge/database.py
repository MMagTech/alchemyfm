import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, Integer, String, Text, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.config import settings


class KnowledgeBase(DeclarativeBase):
    pass


class KnowledgeMode(str, enum.Enum):
    off = "off"
    cached_only = "cached_only"
    active = "active"


class KnowledgeSettings(KnowledgeBase):
    __tablename__ = "knowledge_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[KnowledgeMode] = mapped_column(
        Enum(KnowledgeMode), default=KnowledgeMode.off
    )
    cache_ttl_days: Mapped[int] = mapped_column(Integer, default=180)
    lookahead_count: Mapped[int] = mapped_column(Integer, default=8)
    max_concurrent_jobs: Mapped[int] = mapped_column(Integer, default=1)
    max_facts_per_track: Mapped[int] = mapped_column(Integer, default=3)
    max_fact_chars: Mapped[int] = mapped_column(Integer, default=200)
    display_fact_count: Mapped[int] = mapped_column(Integer, default=3)
    rotation_interval_sec: Mapped[int] = mapped_column(Integer, default=15)
    min_confidence: Mapped[float] = mapped_column(Float, default=0.6)
    searxng_url: Mapped[str] = mapped_column(String(500), default="")
    ollama_url: Mapped[str] = mapped_column(String(500), default="")
    ollama_model: Mapped[str] = mapped_column(String(200), default="")


class TrackKnowledgeStatus(str, enum.Enum):
    ready = "ready"
    no_facts = "no_facts"
    failed = "failed"


class TrackKnowledge(KnowledgeBase):
    __tablename__ = "track_knowledge"

    item_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    status: Mapped[TrackKnowledgeStatus] = mapped_column(Enum(TrackKnowledgeStatus))
    title: Mapped[str] = mapped_column(String(500), default="")
    artist: Mapped[str] = mapped_column(String(500), default="")
    album: Mapped[str] = mapped_column(String(500), default="")
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    failure_reason: Mapped[str] = mapped_column(Text, default="")


class KnowledgeJobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


class KnowledgeJob(KnowledgeBase):
    __tablename__ = "knowledge_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[str] = mapped_column(String(100), index=True)
    station_id: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[KnowledgeJobStatus] = mapped_column(
        Enum(KnowledgeJobStatus), default=KnowledgeJobStatus.pending
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")


_knowledge_is_sqlite = settings.knowledge_database_url.startswith("sqlite")
_knowledge_connect_args = {"check_same_thread": False} if _knowledge_is_sqlite else {}

# Same hardening as radio.db's engine (app/database.py): a pool this small
# (SQLAlchemy's default 5 + 10 overflow) is exactly what let the
# track_started/listen/get_station bugs freeze the app, and knowledge.db's
# admin "test connection" endpoint has the identical held-session-across-a
# -network-await shape.
knowledge_engine = create_engine(
    settings.knowledge_database_url,
    connect_args=_knowledge_connect_args,
    pool_size=20,
    max_overflow=40,
)
KnowledgeSessionLocal = sessionmaker(
    bind=knowledge_engine, autoflush=False, autocommit=False
)

if _knowledge_is_sqlite:

    @event.listens_for(knowledge_engine, "connect")
    def _set_knowledge_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


def init_knowledge_db() -> None:
    if not settings.knowledge_feature:
        return
    KnowledgeBase.metadata.create_all(bind=knowledge_engine)
    _seed_settings()
    db = KnowledgeSessionLocal()
    try:
        from app.knowledge.scheduler import repair_legacy_cancelled_jobs

        repair_legacy_cancelled_jobs(db)
    finally:
        db.close()


def _seed_settings() -> None:
    db = KnowledgeSessionLocal()
    try:
        if db.query(KnowledgeSettings).filter(KnowledgeSettings.id == 1).first():
            return
        db.add(KnowledgeSettings(id=1))
        db.commit()
    finally:
        db.close()


def get_knowledge_db():
    db = KnowledgeSessionLocal()
    try:
        yield db
    finally:
        db.close()
