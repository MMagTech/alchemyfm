from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.database import SourceType, ContinuationMode


class TrackRef(BaseModel):
    item_id: str
    title: str
    artist: str


class TrackInfo(TrackRef):
    duration_sec: int = 0
    album: str = ""
    year: int | None = None


class KnowledgeSource(BaseModel):
    url: str
    title: str = ""


class KnowledgeFact(BaseModel):
    category: str
    text: str
    confidence: float
    sources: list[KnowledgeSource]


class KnowledgeBlock(BaseModel):
    status: Literal["ready"] = "ready"
    facts: list[KnowledgeFact]
    rotation_interval_sec: int = 15


class NowPlaying(BaseModel):
    title: str
    artist: str
    item_id: str | None = None
    cover_url: str | None = None
    artist_bio: str | None = None
    artist_bio_url: str | None = None
    knowledge: KnowledgeBlock | None = None
    hearted: bool | None = None


class StationSummary(BaseModel):
    slug: str
    name: str
    description: str
    artwork_url: str
    enabled: bool
    featured: bool = False
    now_playing: NowPlaying | None = None
    stream_url: str
    listeners: int = 0
    on_air: bool = False
    stream_epoch: int = 0
    knowledge_feature: bool = False
    artist_bio_enabled: bool = True


class StationDetail(StationSummary):
    up_next: list[TrackRef]
    recently_played: list[TrackRef]
    icecast_mount: str
    source_type: str
    source_ref: str


class StationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str | None = None
    description: str = Field(default="", max_length=120)
    artwork_url: str = ""
    icecast_mount: str = Field(min_length=2, max_length=100)
    enabled: bool = True
    source_type: SourceType
    source_ref: str = Field(min_length=1)
    queue_target: int = Field(default=30, ge=5, le=200)
    refresh_threshold: int = Field(default=10, ge=1, le=100)
    artist_separation_minutes: int = Field(default=90, ge=0, le=1440)
    continuation_mode: ContinuationMode = ContinuationMode.source_only
    identity_seed_item_id: str = ""
    identity_anchor_id: str = ""
    programming_json: str = ""
    bootstrap_queue: bool = True

    @field_validator("icecast_mount")
    @classmethod
    def normalize_mount(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith("/"):
            v = f"/{v}"
        return v


class StationUpdate(BaseModel):
    name: str | None = None
    description: str | None = Field(default=None, max_length=120)
    artwork_url: str | None = None
    icecast_mount: str | None = None
    enabled: bool | None = None
    featured: bool | None = None
    featured_order: int | None = None
    sort_order: int | None = None
    source_type: SourceType | None = None
    source_ref: str | None = None
    queue_target: int | None = Field(default=None, ge=5, le=200)
    refresh_threshold: int | None = Field(default=None, ge=1, le=100)
    artist_separation_minutes: int | None = Field(default=None, ge=0, le=1440)
    continuation_mode: ContinuationMode | None = None
    identity_seed_item_id: str | None = None
    identity_anchor_id: str | None = None
    programming_json: str | None = None


class StationAdmin(StationDetail):
    id: int
    featured_order: int = 0
    sort_order: int = 0
    queue_target: int
    refresh_threshold: int
    artist_separation_minutes: int
    continuation_mode: ContinuationMode
    identity_seed_item_id: str
    identity_anchor_id: str
    programming_json: str = ""
    pool_count: int
    source_last_error: str
    source_healthy: bool
    buffer_minutes: int
    queued_count: int
    created_at: datetime
    has_uploaded_artwork: bool = False
    external_artwork_url: str = ""


class HealthResponse(BaseModel):
    status: Literal["ok"]
    stations_enabled: int
    knowledge_feature: bool = False
    default_theme: str = "violet"
    git_sha: str = "unknown"
    started_at: str


class BroadcastStatsRead(BaseModel):
    listeners: int = 0
    outgoing_kbps: int = 0


class BroadcastSettingsRead(BaseModel):
    mp3_bitrate: int
    aac_bitrate: int
    sample_rate: int
    encode_format: str
    genre: str
    crossfade_sec: int
    max_listeners: int
    default_theme: str = "violet"
    icecast_restart_available: bool = False
    backup_keep_count: int = 7
    backup_auto_enabled: bool = True
    log_level: str = "INFO"

    model_config = {"from_attributes": True}


class IcecastRestartResponse(BaseModel):
    ok: bool = True
    container: str


class AppearanceSettingsRead(BaseModel):
    default_theme: str = "violet"
    artist_bio_enabled: bool = True
    default_navidrome_playlist_id: str = ""


class AppearanceSettingsUpdate(BaseModel):
    default_theme: str
    artist_bio_enabled: bool = True
    default_navidrome_playlist_id: str = ""

    @field_validator("default_theme")
    @classmethod
    def validate_default_theme(cls, v: str) -> str:
        from app.themes import VALID_THEMES

        if v not in VALID_THEMES:
            raise ValueError(f"default_theme must be one of: {', '.join(sorted(VALID_THEMES))}")
        return v


class BroadcastSettingsUpdate(BaseModel):
    mp3_bitrate: int | None = Field(default=None, ge=64, le=320)
    aac_bitrate: int | None = Field(default=None, ge=64, le=320)
    sample_rate: int | None = Field(default=None)
    encode_format: str | None = None
    genre: str | None = Field(default=None, max_length=100)
    crossfade_sec: int | None = Field(default=None, ge=0, le=8)
    max_listeners: int | None = Field(default=None, ge=1, le=10000)
    default_theme: str | None = None
    backup_keep_count: int | None = Field(default=None, ge=1, le=100)
    backup_auto_enabled: bool | None = None
    log_level: str | None = None

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str | None) -> str | None:
        if v is not None and v.upper() not in ("DEBUG", "INFO", "WARNING", "ERROR"):
            raise ValueError("log_level must be one of: DEBUG, INFO, WARNING, ERROR")
        return v.upper() if v is not None else v

    @field_validator("default_theme")
    @classmethod
    def validate_default_theme(cls, v: str | None) -> str | None:
        if v is not None:
            from app.themes import VALID_THEMES

            if v not in VALID_THEMES:
                raise ValueError(f"default_theme must be one of: {', '.join(sorted(VALID_THEMES))}")
        return v

    @field_validator("encode_format")
    @classmethod
    def validate_encode_format(cls, v: str | None) -> str | None:
        if v is not None and v not in ("mp3", "aac"):
            raise ValueError("encode_format must be mp3 or aac")
        return v

    @field_validator("aac_bitrate")
    @classmethod
    def validate_aac_bitrate(cls, v: int | None) -> int | None:
        if v is not None and v not in (96, 128, 160, 192, 256, 320):
            raise ValueError("aac_bitrate must be one of 96, 128, 160, 192, 256, 320")
        return v

    @field_validator("sample_rate")
    @classmethod
    def validate_sample_rate(cls, v: int | None) -> int | None:
        if v is not None and v not in (44100, 48000):
            raise ValueError("sample_rate must be 44100 or 48000")
        return v

    @field_validator("mp3_bitrate")
    @classmethod
    def validate_bitrate(cls, v: int | None) -> int | None:
        if v is not None and v not in (96, 128, 160, 192, 256, 320):
            raise ValueError("mp3_bitrate must be one of 96, 128, 160, 192, 256, 320")
        return v


class KnowledgeSettingsRead(BaseModel):
    feature_enabled: bool
    mode: str
    cache_ttl_days: int
    lookahead_count: int
    max_concurrent_jobs: int
    max_facts_per_track: int
    max_fact_chars: int
    display_fact_count: int
    rotation_interval_sec: int
    min_confidence: float
    searxng_url: str
    ollama_url: str
    ollama_model: str
    llm_provider: str = "ollama"
    llm_base_url: str = ""
    llm_model: str = ""
    providers_from_env: bool = True
    search_ok: bool | None = None
    search_message: str = ""
    searxng_ok: bool | None = None
    searxng_message: str = ""
    ollama_ok: bool | None = None
    ollama_message: str = ""
    llm_ok: bool | None = None
    llm_message: str = ""
    cache_entries: int = 0
    cache_ready: int = 0
    jobs_pending: int = 0
    jobs_running: int = 0
    jobs_failed: int = 0
    jobs_cancelled: int = 0
    last_job_error: str = ""


class KnowledgeSettingsUpdate(BaseModel):
    mode: str | None = None
    cache_ttl_days: int | None = Field(default=None, ge=1, le=3650)
    lookahead_count: int | None = Field(default=None, ge=1, le=20)
    max_facts_per_track: int | None = Field(default=None, ge=1, le=10)
    max_fact_chars: int | None = Field(default=None, ge=50, le=500)
    display_fact_count: int | None = Field(default=None, ge=1, le=10)
    rotation_interval_sec: int | None = Field(default=None, ge=5, le=120)
    min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class KnowledgePurgeResponse(BaseModel):
    deleted: int


class KnowledgeCacheEntry(BaseModel):
    item_id: str
    status: str
    title: str
    artist: str
    album: str
    year: int | None = None
    fact_count: int
    updated_at: datetime
    expires_at: datetime


class KnowledgeCacheList(BaseModel):
    items: list[KnowledgeCacheEntry]
    total: int
