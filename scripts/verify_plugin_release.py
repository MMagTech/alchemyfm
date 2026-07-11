#!/usr/bin/env python3
"""Fail CI when plugin catalog, zip, and source are out of sync."""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO_ROOT / "audiomuse-plugins" / "alchemy_fm_bridge"
INIT_PY = PLUGIN_DIR / "__init__.py"
PLUGIN_JSON = PLUGIN_DIR / "plugin.json"
ZIP_PATH = REPO_ROOT / "audiomuse-plugins" / "alchemy_fm_bridge.zip"

# Import shared helpers from release_plugin.py (same repo, no package install).
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from release_plugin import (  # noqa: E402
    init_bytes_in_zip,
    latest_catalog_entry,
    load_plugin_json,
    md5_file,
    normalized_source_bytes,
    read_plugin_version,
)


def version_in_init_bytes(data: bytes) -> str | None:
    match = re.search(rb'^PLUGIN_VERSION\s*=\s*"([^"]+)"', data, re.MULTILINE)
    return match.group(1).decode("utf-8") if match else None


def verify_plugin_release() -> list[str]:
    """Return human-readable errors; empty list means release artifacts are consistent."""
    errors: list[str] = []

    if not INIT_PY.is_file():
        return ["Missing plugin source: audiomuse-plugins/alchemy_fm_bridge/__init__.py"]
    if not PLUGIN_JSON.is_file():
        return ["Missing plugin catalog: audiomuse-plugins/alchemy_fm_bridge/plugin.json"]
    if not ZIP_PATH.is_file():
        return [
            "Missing audiomuse-plugins/alchemy_fm_bridge.zip — run: "
            "python scripts/release_plugin.py \"your changelog\""
        ]

    source_version = read_plugin_version()
    source_bytes = normalized_source_bytes(INIT_PY.read_bytes())
    bundled = init_bytes_in_zip()

    if bundled is None:
        errors.append("alchemy_fm_bridge.zip does not contain __init__.py")
    else:
        zip_version = version_in_init_bytes(bundled)
        if normalized_source_bytes(bundled) != source_bytes:
            errors.append(
                "alchemy_fm_bridge.zip is stale: bundled __init__.py does not match source. "
                "Run python scripts/release_plugin.py and commit the zip."
            )
        if zip_version and zip_version != source_version:
            errors.append(
                f"Zip contains PLUGIN_VERSION {zip_version} but source has {source_version}."
            )

    data = load_plugin_json()
    latest = latest_catalog_entry(data)
    if not latest:
        errors.append("plugin.json has no versions[] entry")
        return errors

    catalog_version = str(latest.get("version") or "").strip()
    catalog_checksum = str(latest.get("checksum") or "").strip()
    zip_checksum = md5_file(ZIP_PATH)

    if catalog_version != source_version:
        errors.append(
            f"plugin.json catalog version ({catalog_version or 'empty'}) != "
            f"PLUGIN_VERSION in source ({source_version}). "
            "Do not bump the catalog without rebuilding the zip."
        )

    if not catalog_checksum:
        errors.append(
            f"plugin.json catalog checksum is empty for version {catalog_version}. "
            "Run python scripts/release_plugin.py to fill it in."
        )
    elif catalog_checksum != zip_checksum:
        errors.append(
            f"plugin.json checksum ({catalog_checksum}) != zip MD5 ({zip_checksum}). "
            "Run python scripts/release_plugin.py and commit plugin.json + zip together."
        )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    errors = verify_plugin_release()
    if not errors:
        version = read_plugin_version()
        print(
            f"OK: plugin release consistent "
            f"(version {version}, zip {ZIP_PATH.stat().st_size} bytes, checksum match)."
        )
        return 0
    print("Plugin release verification FAILED:", file=sys.stderr)
    for err in errors:
        print(f"  - {err}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
