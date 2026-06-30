import logging
from dataclasses import dataclass

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class IcecastNowPlaying:
    artist: str
    title: str
    raw_title: str


@dataclass
class IcecastMountStats:
    artist: str
    title: str
    raw_title: str
    listeners: int
    outgoing_kbps: int = 0


def _parse_stream_title(raw: str) -> tuple[str, str]:
    raw = raw.strip()
    if " - " in raw:
        artist, title = raw.split(" - ", 1)
        return artist.strip(), title.strip()
    return "", raw


def _normalize_mount(mount: str) -> str:
    mount = mount.strip()
    if not mount.startswith("/"):
        mount = f"/{mount}"
    return mount.rstrip("/") or mount


def _mount_from_listenurl(listenurl: str) -> str | None:
    listenurl = listenurl.strip()
    if not listenurl:
        return None
    path = listenurl.split("://", 1)[-1]
    path = path.split("/", 1)[-1] if "/" in path else path
    path = path.split("?")[0].strip("/")
    if not path:
        return None
    return f"/{path}"


def _icecast_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _parse_kbitrate(source: dict) -> int:
    for key in ("outgoing_kbitrate", "bitrate"):
        val = source.get(key)
        if val is None or val == "":
            continue
        try:
            return int(float(val))
        except (TypeError, ValueError):
            continue
    return 0


def _parse_source(source: dict) -> IcecastMountStats | None:
    listenurl = source.get("listenurl") or ""
    mount = _mount_from_listenurl(listenurl)
    if not mount:
        return None
    ice_artist = _icecast_text(source.get("artist"))
    ice_title = _icecast_text(source.get("title"))
    if ice_artist and ice_title:
        artist, title = ice_artist, ice_title
    elif ice_title:
        artist, title = _parse_stream_title(ice_title)
    else:
        artist, title = ice_artist, ""
    raw = f"{artist} - {title}" if artist and title else (ice_title or ice_artist)
    listeners = int(source.get("listeners") or 0)
    return IcecastMountStats(
        artist=artist,
        title=title,
        raw_title=raw,
        listeners=listeners,
        outgoing_kbps=_parse_kbitrate(source),
    )


def _fetch_icestats_sources() -> list[dict]:
    url = f"http://{settings.icecast_host}:{settings.icecast_port}/status-json.xsl"
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(url)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        logger.debug("Icecast status fetch failed: %s", exc)
        return []

    sources = (data.get("icestats") or {}).get("source")
    if not sources:
        return []
    if isinstance(sources, dict):
        return [sources]
    return list(sources)


def _mount_keys_from_source(source: dict) -> list[str]:
    keys: list[str] = []
    for raw in (source.get("listenurl"), source.get("server_name"), source.get("mount")):
        if raw is None or raw == "":
            continue
        text = str(raw).strip()
        if text.startswith("http"):
            mount = _mount_from_listenurl(text)
        else:
            mount = _normalize_mount(text)
        if mount and mount not in keys:
            keys.append(mount)
    return keys


def fetch_all_mount_stats() -> dict[str, IcecastMountStats]:
    """Listener counts and metadata keyed by mount path (e.g. /hip_hop)."""
    stats: dict[str, IcecastMountStats] = {}
    for source in _fetch_icestats_sources():
        try:
            parsed = _parse_source(source)
        except Exception:
            logger.warning("Failed to parse Icecast source: %s", source, exc_info=True)
            continue
        if not parsed:
            continue
        for mount in _mount_keys_from_source(source):
            stats[mount] = parsed
    return stats


def fetch_mount_listeners(mount: str) -> int:
    mount = _normalize_mount(mount)
    parsed = fetch_all_mount_stats().get(mount)
    return parsed.listeners if parsed else 0


def mount_on_air(mount: str) -> bool:
    """True when Icecast reports an active source on this mount."""
    mount = _normalize_mount(mount)
    return mount in fetch_all_mount_stats()


def fetch_mount_now_playing(mount: str) -> IcecastNowPlaying | None:
    """Read the current stream title from Icecast status-json."""
    mount = _normalize_mount(mount)
    parsed = fetch_all_mount_stats().get(mount)
    if not parsed or not (parsed.artist or parsed.title):
        return None
    return IcecastNowPlaying(
        artist=parsed.artist,
        title=parsed.title,
        raw_title=parsed.raw_title,
    )


def fetch_broadcast_totals(mounts: list[str], stream_bitrate_kbps: int) -> dict[str, int]:
    """Sum live listeners and estimated outbound kbps for station mounts."""
    mount_stats = fetch_all_mount_stats()
    listeners = 0
    outgoing_kbps = 0
    for mount in mounts:
        parsed = mount_stats.get(_normalize_mount(mount))
        if not parsed:
            continue
        listeners += parsed.listeners
        # Icecast bitrate fields are per-stream encode rate, not total egress.
        outgoing_kbps += parsed.listeners * stream_bitrate_kbps
    return {"listeners": listeners, "outgoing_kbps": outgoing_kbps}
