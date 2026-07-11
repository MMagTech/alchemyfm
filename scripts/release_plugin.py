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
MAX_CATALOG_VERSIONS = 2


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


def init_bytes_in_zip() -> bytes | None:
    if not ZIP_PATH.exists():
        return None
    with zipfile.ZipFile(ZIP_PATH, "r") as archive:
        try:
            return archive.read("__init__.py")
        except KeyError:
            return None


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


def trim_catalog_versions(data: dict) -> None:
    """Keep only the newest N catalog entries (current + previous release)."""
    versions = data.get("versions")
    if not isinstance(versions, list):
        return
    if len(versions) > MAX_CATALOG_VERSIONS:
        data["versions"] = versions[:MAX_CATALOG_VERSIONS]


def catalog_is_current() -> bool:
    """True when committed zip already bundles the current __init__.py at PLUGIN_VERSION."""
    source_bytes = INIT_PY.read_bytes()
    bundled = init_bytes_in_zip()
    if bundled != source_bytes:
        return False
    data = load_plugin_json()
    latest = latest_catalog_entry(data)
    if not latest:
        return False
    version = read_plugin_version()
    checksum = md5_file(ZIP_PATH)
    return latest.get("version") == version and latest.get("checksum") == checksum


def main() -> int:
    changelog = (
        sys.argv[1].strip()
        if len(sys.argv) > 1
        else "Automated plugin release from CI after tests passed."
    )

    if catalog_is_current():
        print(
            f"Plugin catalog already matches source "
            f"(version {read_plugin_version()}, checksum {md5_file(ZIP_PATH)})."
        )
        return 0

    build_zip()
    source_bytes = INIT_PY.read_bytes()
    if init_bytes_in_zip() != source_bytes:
        build_zip()

    checksum = md5_file(ZIP_PATH)
    data = load_plugin_json()
    latest = latest_catalog_entry(data)
    current_version = read_plugin_version()

    if latest and latest.get("version") == current_version and init_bytes_in_zip() == source_bytes:
        # Same version — refresh checksum/metadata only (e.g. rebuilt zip on Linux CI).
        latest["checksum"] = checksum
        latest["changelog"] = changelog
        trim_catalog_versions(data)
        save_plugin_json(data)
        print(f"Refreshed catalog checksum for plugin {current_version} ({checksum}).")
        from verify_plugin_release import verify_plugin_release

        errors = verify_plugin_release()
        if errors:
            print("Release script finished but verification failed:", file=sys.stderr)
            for err in errors:
                print(f"  - {err}", file=sys.stderr)
            return 1
        return 0

    if latest and latest.get("version") == current_version:
        current_version = bump_patch(current_version)
        write_plugin_version(current_version)
        build_zip()
        checksum = md5_file(ZIP_PATH)

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
    trim_catalog_versions(data)
    save_plugin_json(data)

    print(f"Released plugin {current_version} (checksum {checksum})")

    from verify_plugin_release import verify_plugin_release

    errors = verify_plugin_release()
    if errors:
        print("Release script finished but verification failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
