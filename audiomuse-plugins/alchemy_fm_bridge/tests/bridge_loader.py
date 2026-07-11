"""Load the plugin module once for tests (after fake plugin.api is installed)."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

TESTS_ROOT = Path(__file__).resolve().parent
PLUGIN_ROOT = TESTS_ROOT.parent
FIXTURES_ROOT = TESTS_ROOT / "fixtures"


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

from support import fake_plugin_api  # noqa: E402

fake_plugin_api.install()

spec = importlib.util.spec_from_file_location("alchemy_fm_bridge", PLUGIN_ROOT / "__init__.py")
assert spec and spec.loader
bridge = importlib.util.module_from_spec(spec)
sys.modules["alchemy_fm_bridge"] = bridge
spec.loader.exec_module(bridge)


def load_fixture(*parts: str) -> Any:
    path = FIXTURES_ROOT.joinpath(*parts)
    return json.loads(path.read_text(encoding="utf-8"))
