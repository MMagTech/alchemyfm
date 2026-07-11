"""Backend test fixtures."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

_test_db_path = Path(tempfile.gettempdir()) / "alchemyfm_pytest.db"

# Configure test environment before importing the app.
os.environ["DATABASE_URL"] = f"sqlite:///{_test_db_path}"
os.environ["DATA_DIR"] = str(Path(tempfile.gettempdir()) / "alchemyfm-pytest-data")
os.environ["ADMIN_PASSWORD"] = "test-admin-secret"
os.environ["KNOWLEDGE_FEATURE"] = "false"
os.environ["ICECAST_DOCKER_RESTART"] = "false"
os.environ["RESTRICT_INTERNAL_ROUTES"] = "true"

from app.database import Base, SessionLocal, Station, engine, init_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _init_test_database() -> Generator[None, None, None]:
    if _test_db_path.exists():
        _test_db_path.unlink()
    init_db()
    yield
    if _test_db_path.exists():
        _test_db_path.unlink()


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
