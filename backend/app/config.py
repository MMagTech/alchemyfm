from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:////data/radio.db"
    data_dir: str = "/data"

    audiomuse_url: str = "http://localhost:8000"
    audiomuse_api_token: str = ""

    navidrome_url: str = "http://localhost:4533"
    navidrome_user: str = ""
    navidrome_password: str = ""

    icecast_host: str = "icecast"
    icecast_port: int = 8000
    icecast_public_host: str = "localhost"
    icecast_public_port: int = 8000
    icecast_public_scheme: str = ""
    icecast_source_password: str = "hackme"

    liquidsoap_callback_secret: str = "change-me"
    queue_refresh_interval_sec: int = 30

    # Admin UI + /api/admin/* — required before exposing this app publicly
    admin_username: str = "admin"
    admin_password: str = ""
    restrict_internal_routes: bool = True
    trust_proxy_headers: bool = False

    # Restart Icecast from admin (requires Docker socket in the backend container)
    icecast_docker_restart: bool = False
    icecast_compose_service: str = "icecast"
    docker_compose_project: str = ""

    # Optional music knowledge enrichment (OFF by default — no UI or worker when false)
    knowledge_feature: bool = False
    knowledge_database_url: str = "sqlite:////data/knowledge.db"
    searxng_url: str = ""
    ollama_url: str = ""
    ollama_model: str = ""

    @property
    def stations_root(self) -> str:
        return f"{self.data_dir.rstrip('/')}/stations"

    def stream_public_url(self, mount: str) -> str:
        mount = mount if mount.startswith("/") else f"/{mount}"
        scheme = (self.icecast_public_scheme or "").strip().lower().rstrip(":/")
        if scheme not in ("http", "https"):
            scheme = "https" if self.icecast_public_port == 443 else "http"
        host = self.icecast_public_host
        port = self.icecast_public_port
        default_port = 443 if scheme == "https" else 80
        if port == default_port:
            return f"{scheme}://{host}{mount}"
        return f"{scheme}://{host}:{port}{mount}"


settings = Settings()
