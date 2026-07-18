import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    event,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


class SourceType(str, enum.Enum):
    """Programming sources — Navidrome is only used for streams and cover art."""

    alchemy_anchor = "alchemy_anchor"
    similar_seed = "similar_seed"
    clap_query = "clap_query"
    lyrics_query = "lyrics_query"
    mood_centroid = "mood_centroid"
    journey = "journey"


class ContinuationMode(str, enum.Enum):
    """How to refill the queue when the primary source has no fresh tracks."""

    source_only = "source_only"  # stay in source pool; allow repeats before leaving
    programming_only = "programming_only"  # re-query programming + pool once; no similar tiers
    no_repeats = "no_repeats"  # similar expansion like similar_to_last but never pool repeats
    similar_to_seed = "similar_to_seed"  # similar_tracks from a fixed seed in the source
    similar_to_last = "similar_to_last"  # similar_tracks from last played (can drift)


class QueueItemStatus(str, enum.Enum):
    queued = "queued"
    playing = "playing"
    played = "played"
    skipped = "skipped"


class Station(Base):
    __tablename__ = "stations"
    # Never reuse a deleted station's id (SQLite ROWID reuse otherwise) -- the
    # AudioMuse plugin caches this id per channel, so a reused id after a
    # delete or a database restore can silently point cached plugin state at
    # the wrong station.
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    artwork_url: Mapped[str] = mapped_column(String(500), default="")
    icecast_mount: Mapped[str] = mapped_column(String(100), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    featured: Mapped[bool] = mapped_column(Boolean, default=False)
    featured_order: Mapped[int] = mapped_column(Integer, default=0)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    programming_json: Mapped[str] = mapped_column(Text, default="")

    queue_target: Mapped[int] = mapped_column(Integer, default=30)
    refresh_threshold: Mapped[int] = mapped_column(Integer, default=10)
    artist_separation_minutes: Mapped[int] = mapped_column(Integer, default=90)
    continuation_mode: Mapped[ContinuationMode] = mapped_column(
        Enum(ContinuationMode), default=ContinuationMode.source_only
    )

    identity_seed_item_id: Mapped[str] = mapped_column(String(100), default="")
    identity_anchor_id: Mapped[str] = mapped_column(String(100), default="")
    source_last_error: Mapped[str] = mapped_column(Text, default="")
    source_last_ok_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    queue_items: Mapped[list["QueueItem"]] = relationship(
        back_populates="station", cascade="all, delete-orphan"
    )
    play_history: Mapped[list["PlayHistory"]] = relationship(
        back_populates="station", cascade="all, delete-orphan"
    )
    pool_items: Mapped[list["StationPoolItem"]] = relationship(
        back_populates="station", cascade="all, delete-orphan"
    )


class StationPoolItem(Base):
    """Imported tracks owned by the station — playback does not depend on live playlists."""

    __tablename__ = "station_pool_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"), index=True)
    item_id: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(500), default="")
    artist: Mapped[str] = mapped_column(String(500), default="")
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    station: Mapped["Station"] = relationship(back_populates="pool_items")


class QueueItem(Base):
    __tablename__ = "queue_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"), index=True)
    item_id: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(500), default="")
    artist: Mapped[str] = mapped_column(String(500), default="")
    duration_sec: Mapped[int] = mapped_column(Integer, default=0)
    stream_url: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[QueueItemStatus] = mapped_column(
        Enum(QueueItemStatus), default=QueueItemStatus.queued
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    station: Mapped["Station"] = relationship(back_populates="queue_items")


class PlayHistory(Base):
    __tablename__ = "play_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"), index=True)
    item_id: Mapped[str] = mapped_column(String(100), default="")
    title: Mapped[str] = mapped_column(String(500), default="")
    artist: Mapped[str] = mapped_column(String(500), default="")
    played_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    station: Mapped["Station"] = relationship(back_populates="play_history")


class BroadcastSettings(Base):
    """Global Liquidsoap / Icecast encode settings — single row (id=1)."""

    __tablename__ = "broadcast_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mp3_bitrate: Mapped[int] = mapped_column(Integer, default=192)
    aac_bitrate: Mapped[int] = mapped_column(Integer, default=128)
    sample_rate: Mapped[int] = mapped_column(Integer, default=44100)
    encode_format: Mapped[str] = mapped_column(String(16), default="mp3")
    genre: Mapped[str] = mapped_column(String(100), default="Radio")
    crossfade_sec: Mapped[int] = mapped_column(Integer, default=0)
    max_listeners: Mapped[int] = mapped_column(Integer, default=100)
    default_theme: Mapped[str] = mapped_column(String(32), default="violet")
    artist_bio_enabled: Mapped[bool] = mapped_column(default=True)
    default_navidrome_playlist_id: Mapped[str] = mapped_column(String(100), default="")
    backup_keep_count: Mapped[int] = mapped_column(Integer, default=7)
    backup_auto_enabled: Mapped[bool] = mapped_column(default=True)
    log_level: Mapped[str] = mapped_column(String(16), default="INFO")


_is_sqlite = settings.database_url.startswith("sqlite")
connect_args = {"check_same_thread": False} if _is_sqlite else {}

# A handful of request paths legitimately hold a session open across a slow
# external network call (Navidrome/AudioMuse enrichment during a queue
# refill) rather than releasing it immediately -- narrowing every one of
# those down is real, ongoing work, not something to rush. In the meantime,
# a much larger pool than SQLAlchemy's default (5 + 10 overflow = 15) means
# a handful of naturally slow requests can't exhaust the pool and freeze
# every other endpoint (admin included) while they're in flight.
engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    pool_size=20,
    max_overflow=40,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        # WAL lets readers and writers proceed concurrently instead of one
        # writer blocking everyone (SQLite's default rollback-journal mode);
        # busy_timeout makes a connection that *does* need to wait for a
        # lock retry for a bit instead of failing immediately with
        # "database is locked".
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _migrate_db()


def _migrate_db() -> None:
    """Lightweight column adds for existing SQLite databases."""
    if not settings.database_url.startswith("sqlite"):
        return
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(stations)"))}
        if "continuation_mode" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE stations ADD COLUMN continuation_mode "
                    "VARCHAR(32) NOT NULL DEFAULT 'source_only'"
                )
            )
            conn.commit()
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(stations)"))}
        for col, ddl in (
            ("identity_seed_item_id", "VARCHAR(100) NOT NULL DEFAULT ''"),
            ("identity_anchor_id", "VARCHAR(100) NOT NULL DEFAULT ''"),
            ("source_last_error", "TEXT NOT NULL DEFAULT ''"),
            ("source_last_ok_at", "DATETIME"),
            ("programming_json", "TEXT NOT NULL DEFAULT ''"),
            ("featured", "BOOLEAN NOT NULL DEFAULT 0"),
            ("featured_order", "INTEGER NOT NULL DEFAULT 0"),
            ("sort_order", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if col not in cols:
                conn.execute(text(f"ALTER TABLE stations ADD COLUMN {col} {ddl}"))
                conn.commit()
        # sort_order landed with a flat default of 0 for every existing row, so
        # reordering (which swaps two stations' sort_order values) is a no-op
        # until they're distinct. Backfill once, alphabetically, so ties never
        # start out identical; harmless to re-check on every boot since it only
        # fires while every station still shares the same value.
        row_count, distinct_orders = conn.execute(
            text("SELECT COUNT(*), COUNT(DISTINCT sort_order) FROM stations")
        ).first()
        if row_count and row_count > 1 and distinct_orders <= 1:
            for idx, (station_id,) in enumerate(
                conn.execute(text("SELECT id FROM stations ORDER BY name ASC")).fetchall()
            ):
                conn.execute(
                    text("UPDATE stations SET sort_order = :idx WHERE id = :id"),
                    {"idx": idx, "id": station_id},
                )
            conn.commit()
        # Retrofit AUTOINCREMENT onto an existing stations table (SQLite can't
        # ALTER this in place, so rebuild the table once). See the comment on
        # Station.__table_args__ for why this matters.
        create_sql = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='stations'")
        ).scalar()
        if create_sql and "AUTOINCREMENT" not in create_sql.upper():
            conn.execute(text("DROP TABLE IF EXISTS stations_autoincrement_rebuild"))
            conn.execute(text("ALTER TABLE stations RENAME TO stations_autoincrement_rebuild"))
            old_indexes = conn.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND tbl_name='stations_autoincrement_rebuild' AND sql IS NOT NULL"
                )
            ).fetchall()
            for (idx_name,) in old_indexes:
                conn.execute(text(f'DROP INDEX IF EXISTS "{idx_name}"'))
            Station.__table__.create(bind=conn)
            col_list = ", ".join(c.name for c in Station.__table__.columns)
            # created_at is NOT NULL at the ORM level but has no DB-level
            # default (SQLAlchemy's default= only applies on ORM inserts), so
            # coalesce it in case any legacy row was ever written without one.
            select_list = ", ".join(
                "COALESCE(created_at, CURRENT_TIMESTAMP)" if c.name == "created_at" else c.name
                for c in Station.__table__.columns
            )
            conn.execute(
                text(
                    f"INSERT INTO stations ({col_list}) "
                    f"SELECT {select_list} FROM stations_autoincrement_rebuild"
                )
            )
            conn.execute(text("DROP TABLE stations_autoincrement_rebuild"))
            conn.commit()
        conn.execute(
            text(
                "UPDATE stations SET enabled = 0, "
                "source_last_error = 'Legacy source — delete and redeploy from Channel Designer v3.' "
                "WHERE source_type IN ('clustering_playlist', 'navidrome_playlist') "
                "AND (programming_json IS NULL OR programming_json = '')"
            )
        )
        conn.commit()
        try:
            cols = {row[1] for row in conn.execute(text("PRAGMA table_info(broadcast_settings)"))}
            for col, ddl in (
                ("encode_format", "VARCHAR(16) NOT NULL DEFAULT 'mp3'"),
                ("vorbis_bitrate", "INTEGER NOT NULL DEFAULT 128"),
                ("aac_bitrate", "INTEGER NOT NULL DEFAULT 128"),
                ("max_listeners", "INTEGER NOT NULL DEFAULT 100"),
                ("default_theme", "VARCHAR(32) NOT NULL DEFAULT 'violet'"),
                ("artist_bio_enabled", "INTEGER NOT NULL DEFAULT 1"),
                ("default_navidrome_playlist_id", "VARCHAR(100) NOT NULL DEFAULT ''"),
                ("backup_keep_count", "INTEGER NOT NULL DEFAULT 7"),
                ("backup_auto_enabled", "INTEGER NOT NULL DEFAULT 1"),
                ("log_level", f"VARCHAR(16) NOT NULL DEFAULT '{settings.log_level}'"),
            ):
                if col not in cols:
                    conn.execute(text(f"ALTER TABLE broadcast_settings ADD COLUMN {col} {ddl}"))
                    conn.commit()
            if not conn.execute(text("SELECT 1 FROM broadcast_settings WHERE id = 1")).fetchone():
                conn.execute(
                    text(
                        "INSERT INTO broadcast_settings "
                        "(id, mp3_bitrate, aac_bitrate, sample_rate, encode_format, genre, "
                        "crossfade_sec, max_listeners, default_theme) "
                        "VALUES (1, 192, 128, 44100, 'mp3', 'Radio', 0, 100, 'violet')"
                    )
                )
                conn.commit()
            # Ogg Vorbis is unsupported on iOS Safari/WKWebView (no decoder ever
            # shipped) -- normalize any station still set to it back to mp3
            # rather than leave every listener on a dead format after upgrade.
            conn.execute(
                text("UPDATE broadcast_settings SET encode_format = 'mp3' WHERE encode_format = 'vorbis'")
            )
            conn.commit()
        except Exception:
            pass
        try:
            cols = {row[1] for row in conn.execute(text("PRAGMA table_info(broadcast_settings)"))}
            if "default_theme" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE broadcast_settings ADD COLUMN "
                        "default_theme VARCHAR(32) NOT NULL DEFAULT 'violet'"
                    )
                )
                conn.commit()
        except Exception:
            pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
