#!/usr/bin/env python3
"""Build alchemy_fm_bridge.zip and sync plugin.json + PLUGIN_VERSION when source changed."""

from __future__ import annotations

import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO_ROOT / "audiomuse-plugins" / "alchemy_fm_bridge"
INIT_PY = PLUGIN_DIR / "__init__.py"
PLUGIN_JSON = PLUGIN_DIR / "plugin.json"
ZIP_PATH = REPO_ROOT / "audiomuse-plugins" / "alchemy_fm_bridge.zip"
SOURCE_URL = (
    "https://raw.githubusercontent.com/MMagTech/alchemyfm/master/"
    "audiomuse-plugins/alchemy_fm_bridge.zip"
)


def md5_file(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_plugin_version() -> str:
    text = INIT_PY.read_text(encoding="utf-8")
    match = re.search(r'^PLUGIN_VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise RuntimeError(f"PLUGIN_VERSION not found in {INIT_PY}")
    return match.group(1)


def write_plugin_version(version: str) -> None:
    text = INIT_PY.read_text(encoding="utf-8")
    updated, count = re.subn(
        r'^PLUGIN_VERSION\s*=\s*"[^"]+"',
        f'PLUGIN_VERSION = "{version}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise RuntimeError("Failed to update PLUGIN_VERSION")
    INIT_PY.write_text(updated, encoding="utf-8")


def bump_patch(version: str) -> str:
    parts = version.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise RuntimeError(f"Unsupported version format: {version}")
    major, minor, patch = (int(part) for part in parts)
    return f"{major}.{minor}.{patch + 1}"


def build_zip() -> None:
    ZIP_PATH.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(INIT_PY, arcname="__init__.py")


def load_plugin_json() -> dict:
    return json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))


def save_plugin_json(data: dict) -> None:
    PLUGIN_JSON.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def latest_catalog_entry(data: dict) -> dict | None:
    versions = data.get("versions")
    if not isinstance(versions, list) or not versions:
        return None
    first = versions[0]
    return first if isinstance(first, dict) else None


def main() -> int:
    changelog = (
        sys.argv[1].strip()
        if len(sys.argv) > 1
        else "Automated plugin release from CI after tests passed."
    )

    build_zip()
    checksum = md5_file(ZIP_PATH)
    data = load_plugin_json()
    latest = latest_catalog_entry(data)

    if latest and latest.get("checksum") == checksum:
        print(f"Plugin catalog already matches zip (checksum {checksum}); nothing to release.")
        return 0

    current_version = read_plugin_version()
    if latest and latest.get("version") == current_version:
        new_version = bump_patch(current_version)
        write_plugin_version(new_version)
        build_zip()
        checksum = md5_file(ZIP_PATH)
        current_version = new_version
        print(f"Bumped PLUGIN_VERSION to {new_version}")

    entry = {
        "version": current_version,
        "min_core_version": (latest or {}).get("min_core_version", "2.5.0"),
        "changelog": changelog,
        "imageUrl": "",
        "sourceUrl": SOURCE_URL,
        "checksum": checksum,
    }
    versions = data.setdefault("versions", [])
    if not isinstance(versions, list):
        raise RuntimeError("plugin.json versions must be a list")
    versions.insert(0, entry)
    save_plugin_json(data)

    print(f"Released plugin {current_version} (checksum {checksum})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
