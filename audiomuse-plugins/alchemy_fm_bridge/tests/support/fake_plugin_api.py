"""Minimal AudioMuse plugin.api shim for offline tests."""

from __future__ import annotations

import logging
import os
import sys
import types
from typing import Any, Callable

import psycopg2
from psycopg2.extensions import connection as PgConnection

PLUGIN_ID = "alchemy_fm_bridge"
TABLE_PREFIX = f"plugin_{PLUGIN_ID}_"

_settings: dict[str, Any] = {
    "alchemyfm_url": "http://alchemyfm.test",
    "alchemyfm_username": "admin",
    "alchemyfm_password": "test-password",
    "audiomuse_api_token": "",
    "audiomuse_api_url": "",
}

_score_fixtures: dict[str, dict[str, Any]] = {}
_connection_factory: Callable[[], PgConnection] | None = None

logger = logging.getLogger("alchemy_fm_bridge.test")


def table(name: str) -> str:
    return f"{TABLE_PREFIX}{name}"


def get_setting(key: str, default: Any = None) -> Any:
    return _settings.get(key, default)


def set_setting(key: str, value: Any) -> None:
    _settings[key] = value


def get_db() -> PgConnection:
    if _connection_factory is None:
        raise RuntimeError("fake_plugin_api: connection factory not configured")
    return _connection_factory()


def get_score_data_by_ids(item_ids: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item_id in item_ids:
        base = _score_fixtures.get(
            item_id,
            {
                "item_id": item_id,
                "title": f"Track {item_id}",
                "author": "Test Artist",
                "tempo": 120.0,
                "energy": 0.55,
                "mood_vector": "rock:0.8, energetic:0.6",
            },
        )
        rows.append(dict(base))
    return rows


def render_page(body: str, title: str = "") -> str:
    safe_title = title or "Test"
    return f"<!DOCTYPE html><html><head><title>{safe_title}</title></head><body>{body}</body></html>"


def manage_plugins_url() -> str:
    return "/plugins"


def reset_settings() -> None:
    _settings.clear()
    _settings.update(
        {
            "alchemyfm_url": "http://alchemyfm.test",
            "alchemyfm_username": "admin",
            "alchemyfm_password": "test-password",
            "audiomuse_api_token": "",
            "audiomuse_api_url": "",
        }
    )


def set_score_fixtures(fixtures: dict[str, dict[str, Any]]) -> None:
    _score_fixtures.clear()
    _score_fixtures.update(fixtures)


def configure_connection_factory(factory: Callable[[], PgConnection]) -> None:
    global _connection_factory
    _connection_factory = factory


def install() -> None:
    plugin_pkg = types.ModuleType("plugin")
    plugin_pkg.api = sys.modules[__name__]  # type: ignore[attr-defined]
    config_mod = types.ModuleType("plugin.api.config")
    config_mod.AUDIOMUSE_CONTROL_HOST = ""
    config_mod.AUDIOMUSE_CONTROL_PORT = ""
    config_mod.AUDIOMUSE_API_TOKEN = ""
    plugin_pkg.api.config = config_mod  # type: ignore[attr-defined]
    sys.modules["plugin"] = plugin_pkg
    sys.modules["plugin.api"] = sys.modules[__name__]
    sys.modules["plugin.api.config"] = config_mod


def ensure_cron_table(conn: PgConnection) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS cron (
            id SERIAL PRIMARY KEY,
            name TEXT,
            task_type TEXT UNIQUE,
            cron_expr TEXT,
            enabled BOOLEAN DEFAULT FALSE
        )
        """
    )
    conn.commit()
    cur.close()


def truncate_plugin_tables(conn: PgConnection) -> None:
    cur = conn.cursor()
    for suffix in ("channels", "channel_pool", "auditions", "profiles"):
        cur.execute(f"TRUNCATE TABLE {table(suffix)} RESTART IDENTITY CASCADE")
    conn.commit()
    cur.close()


def connect_from_env() -> PgConnection:
    url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("TEST_DATABASE_URL is required for plugin database tests")
    return psycopg2.connect(url)
