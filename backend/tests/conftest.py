"""Backend test fixtures."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

_test_db_path = Path(tempfile.gettempdir()) / "alchemyfm_pytest.db"

# Configure test environment before importing the app.
os.environ["DATABASE_URL"] = f"sqlite:///{_test_db_path.as_posix()}"
os.environ["DATA_DIR"] = str(Path(tempfile.gettempdir()) / "alchemyfm-pytest-data")
os.environ["ADMIN_PASSWORD"] = "test-admin-secret"
os.environ["KNOWLEDGE_FEATURE"] = "false"
os.environ["ICECAST_DOCKER_RESTART"] = "false"
os.environ["RESTRICT_INTERNAL_ROUTES"] = "true"
os.environ["LIQUIDSOAP_CALLBACK_SECRET"] = "test-callback-secret"

from app.database import Base, SessionLocal, Station, engine, init_db  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.services.navidrome import navidrome_client  # noqa: E402

# Repo-root .env is loaded at import time — override for deterministic tests.
settings.admin_username = "admin"
settings.admin_password = "test-admin-secret"
settings.liquidsoap_callback_secret = "test-callback-secret"
settings.restrict_internal_routes = True

TEST_ADMIN_USERNAME = settings.admin_username
TEST_ADMIN_PASSWORD = settings.admin_password
TEST_CALLBACK_SECRET = settings.liquidsoap_callback_secret


@pytest.fixture(scope="session", autouse=True)
def _init_test_database() -> Generator[None, None, None]:
    if _test_db_path.exists():
        try:
            _test_db_path.unlink()
        except PermissionError:
            pass
    init_db()
    yield


@pytest.fixture(autouse=True)
def _clean_tables() -> Generator[None, None, None]:
    init_db()
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
    yield


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    finally:
        session.close()


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def sample_station(db_session: Session) -> Station:
    station = Station(
        name="Test Jazz",
        slug="test-jazz",
        description="pytest station",
        icecast_mount="/test-jazz",
        enabled=True,
        source_type="clap_query",
        source_ref="smooth jazz",
        programming_json='{"programming":{"type":"clap_query","query":"smooth jazz"}}',
        queue_target=30,
        refresh_threshold=10,
    )
    db_session.add(station)
    db_session.commit()
    db_session.refresh(station)
    return station


@pytest.fixture
def admin_auth() -> tuple[str, str]:
    return ("admin", settings.admin_password)


@pytest.fixture
def admin_client(client: TestClient) -> TestClient:
    """Authenticated admin session (cookie) for /api/admin/* routes."""
    resp = client.post(
        "/api/admin/login",
        json={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    return client


@pytest.fixture
def internal_client(client: TestClient) -> Generator[TestClient, None, None]:
    """Bypass IP guard — TestClient reports host ``testclient``, not a RFC1918 address."""
    from app.auth import require_internal_client

    app.dependency_overrides[require_internal_client] = lambda: None
    yield client
    app.dependency_overrides.pop(require_internal_client, None)


@pytest.fixture(autouse=True)
def _mock_navidrome_stream_url() -> Generator[None, None, None]:
    """CI has no Navidrome credentials; queue rebuild/bootstrap only need a URL string."""
    with patch.object(
        navidrome_client,
        "stream_url",
        side_effect=lambda item_id: f"http://navidrome.test/stream/{item_id}",
    ):
        yield


@pytest.fixture(autouse=True)
def _mock_extend_queue() -> Generator[None, None, None]:
    """Avoid live AudioMuse calls when internal callbacks trigger ensure_queue_fresh."""
    with patch("app.services.queue.extend_queue", new=AsyncMock(return_value=None)):
        yield


@pytest.fixture
def bootstrap_mocks():
    """Mock AudioMuse/Navidrome during queue bootstrap and refresh."""
    from app.schemas import TrackInfo, TrackRef

    batch = [TrackRef(item_id="boot-1", title="Bootstrap Track", artist="Test Artist")]
    enriched = [
        TrackInfo(
            item_id="boot-1",
            title="Bootstrap Track",
            artist="Test Artist",
            duration_sec=180,
        )
    ]
    with (
        patch("app.services.queue.fetch_bootstrap_batch", new=AsyncMock(return_value=batch)),
        patch("app.services.navidrome.navidrome_client.enrich_tracks", new=AsyncMock(return_value=enriched)),
    ):
        yield batch
