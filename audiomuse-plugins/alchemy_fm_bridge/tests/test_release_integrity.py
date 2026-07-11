"""Ensure committed plugin zip matches source and catalog."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from verify_plugin_release import verify_plugin_release  # noqa: E402


class TestPluginReleaseIntegrity:
    def test_committed_zip_matches_catalog_and_source(self):
        errors = verify_plugin_release()
        assert not errors, "Plugin release out of sync:\n" + "\n".join(f"  - {e}" for e in errors)
