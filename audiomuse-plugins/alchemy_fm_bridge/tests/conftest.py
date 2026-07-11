"""Shared pytest fixtures for the Alchemy FM Bridge plugin."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest
from flask import Flask

from bridge_loader import bridge, load_fixture
from support import fake_plugin_api


def _postgres_available() -> bool:
    url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not url:
        return False
    try:
        import psycopg2

        conn = psycopg2.connect(url)
        conn.close()
        return True
    except Exception:
        return False


requires_postgres = pytest.mark.skipif(
    not _postgres_available(),
    reason="TEST_DATABASE_URL not set or Postgres unreachable",
)


@pytest.fixture(scope="session")
def pg_connection():
    import psycopg2

    conn = psycopg2.connect(os.environ["TEST_DATABASE_URL"])
    conn.autocommit = False
    fake_plugin_api.ensure_cron_table(conn)
    bridge.migrate(conn)
    yield conn
    conn.close()


@pytest.fixture
def pg_db(pg_connection):
    fake_plugin_api.configure_connection_factory(lambda: pg_connection)
    fake_plugin_api.reset_settings()
    fake_plugin_api.set_score_fixtures({})
    fake_plugin_api.truncate_plugin_tables(pg_connection)
    pg_connection.commit()
    yield pg_connection
    fake_plugin_api.truncate_plugin_tables(pg_connection)
    pg_connection.commit()


@pytest.fixture
def flask_app(pg_db):
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.config["TESTING"] = True
    app.register_blueprint(bridge.bp)
    return app


@pytest.fixture
def client(flask_app):
    return flask_app.test_client()


@pytest.fixture
def audiomuse_mocks():
    """Patch AudioMuse HTTP helpers with fixture-backed responses."""

    def fake_get(path: str, *, params: dict[str, str] | None = None, timeout: float = 60.0):
        params = params or {}
        if path == "/api/mood_centroids":
            return load_fixture("audiomuse", "mood_centroids.json")
        if path == "/api/similar_tracks":
            if params.get("item_id") or params.get("mood"):
                return load_fixture("audiomuse", "similar_tracks.json")
        if path == "/api/anchors":
            return {"anchors": [{"id": 42, "name": "Test Anchor"}]}
        if path == "/api/search_tracks":
            return [
                {
                    "item_id": "seed-99",
                    "title": "Seed Song",
                    "author": "Seed Artist",
                }
            ]
        if path in ("/api/search-artists", "/api/search_artists"):
            return {"artists": ["Test Artist"]}
        return {}

    def fake_post(path: str, payload: dict[str, Any], *, timeout: float = 120.0):
        if path == "/api/clap/search":
            return load_fixture("audiomuse", "clap_search.json")
        if path == "/api/lyrics/search/text":
            return load_fixture("audiomuse", "lyrics_search.json")
        if path == "/api/alchemy":
            return load_fixture("audiomuse", "alchemy_anchor.json")
        return {}

    with (
        patch.object(bridge, "audiomuse_get", side_effect=fake_get),
        patch.object(bridge, "audiomuse_post", side_effect=fake_post),
    ):
        yield
