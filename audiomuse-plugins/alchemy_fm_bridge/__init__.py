"""AudioMuse-native channel designer — preview programming, then deploy to Alchemy FM."""

from __future__ import annotations

import base64
import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from flask import Blueprint, request, redirect

from plugin.api import (
    get_db,
    get_score_data_by_ids,
    get_setting,
    set_setting,
    render_page,
    manage_plugins_url,
    logger,
    table,
)

PLUGIN_VERSION = "2.1.0"

ALCHEMY_FM_USER_AGENT = (
    "AlchemyFmBridge/2.0 AudioMuse-Plugin (+https://github.com/MMagTech/alchemyfm)"
)

# ---------------------------------------------------------------------------
# Errors + HTTP clients
# ---------------------------------------------------------------------------


def _friendly_http_error(status: int, body: str) -> str:
    lower = body.lower()
    if (
        "browser's signature" in lower
        or "cloudflare" in lower
        or "cf-ray" in lower
        or "just a moment" in lower
    ):
        return (
            "Cloudflare blocked the AudioMuse server from reaching Alchemy FM. "
            "The plugin calls the API from your AudioMuse container, not your browser. "
            "Fix: add a Cloudflare WAF skip rule for path /api/admin/* (or from your "
            "AudioMuse server IP), use a direct/LAN URL that bypasses Cloudflare in "
            "plugin settings, or turn off Bot Fight Mode for admin API routes."
        )
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict):
            if parsed.get("detail"):
                return str(parsed["detail"])
            if parsed.get("error"):
                return str(parsed["error"])
    except json.JSONDecodeError:
        pass
    if "<html" in lower:
        return f"Alchemy FM returned HTTP {status} (HTML error page — often Cloudflare or a reverse proxy)."
    text = re.sub(r"<[^>]+>", " ", body)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:500] or f"HTTP {status}"


class ChannelDesignerError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class AlchemyFmClient:
    def __init__(self, base_url: str, username: str, password: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
        self.headers = {
            "Authorization": f"Basic {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": ALCHEMY_FM_USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        }

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, headers=self.headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ChannelDesignerError(
                _friendly_http_error(exc.code, detail), status=exc.code
            ) from exc
        except urllib.error.URLError as exc:
            raise ChannelDesignerError(
                f"Could not reach Alchemy FM at {self.base_url}: {exc.reason}"
            ) from exc

    def test_connection(self) -> list[dict[str, Any]]:
        stations = self._request("GET", "/api/admin/stations")
        if not isinstance(stations, list):
            raise ChannelDesignerError("Unexpected response from /api/admin/stations")
        return stations

    def find_station_by_slug(self, slug: str) -> dict[str, Any] | None:
        slug = slug.strip().lower()
        for station in self.test_connection():
            if str(station.get("slug", "")).lower() == slug:
                return station
        return None

    def create_station(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = self._request("POST", "/api/admin/stations", payload)
        if not isinstance(result, dict):
            raise ChannelDesignerError("Unexpected response when creating station")
        return result

    def update_station(self, station_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        result = self._request("PUT", f"/api/admin/stations/{station_id}", payload)
        if not isinstance(result, dict):
            raise ChannelDesignerError("Unexpected response when updating station")
        return result

    def bootstrap_station(self, station_id: int) -> dict[str, Any]:
        result = self._request("POST", f"/api/admin/stations/{station_id}/bootstrap")
        if not isinstance(result, dict):
            raise ChannelDesignerError("Unexpected response when bootstrapping station")
        return result

    def push_station(
        self,
        payload: dict[str, Any],
        *,
        slug: str | None = None,
        bootstrap: bool = True,
    ) -> tuple[dict[str, Any], str]:
        target_slug = (slug or payload.get("slug") or "").strip().lower()
        existing = self.find_station_by_slug(target_slug) if target_slug else None
        if existing:
            station_id = int(existing["id"])
            update_fields = {
                key: value
                for key, value in payload.items()
                if key not in ("slug", "bootstrap_queue")
            }
            station = self.update_station(station_id, update_fields)
            action = "updated"
        else:
            station = self.create_station(payload)
            station_id = int(station["id"])
            action = "created"
        if bootstrap and payload.get("bootstrap_queue", True):
            station = self.bootstrap_station(station_id)
        return station, action


def _audiomuse_base_url() -> str:
    return request.host_url.rstrip("/")


def _audiomuse_token() -> str:
    try:
        from plugin.api import config as plugin_config

        token = getattr(plugin_config, "AUDIOMUSE_API_TOKEN", "") or ""
        if token:
            return str(token)
    except Exception:
        pass
    return str(get_setting("audiomuse_api_token") or "")


def _audiomuse_headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    token = _audiomuse_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def audiomuse_get(path: str, *, params: dict[str, str] | None = None, timeout: float = 60.0) -> Any:
    query = "?" + urllib.parse.urlencode(params) if params else ""
    url = f"{_audiomuse_base_url()}{path}{query}"
    req = urllib.request.Request(url, headers=_audiomuse_headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ChannelDesignerError(detail or f"AudioMuse API HTTP {exc.code}", status=exc.code) from exc
    except urllib.error.URLError as exc:
        raise ChannelDesignerError(f"Could not reach AudioMuse API: {exc.reason}") from exc


def audiomuse_post(path: str, payload: dict[str, Any], *, timeout: float = 120.0) -> Any:
    url = f"{_audiomuse_base_url()}{path}"
    data = json.dumps(payload).encode("utf-8")
    headers = {**_audiomuse_headers(), "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        message = detail
        try:
            parsed = json.loads(detail)
            if isinstance(parsed, dict) and parsed.get("error"):
                message = str(parsed["error"])
        except json.JSONDecodeError:
            pass
        raise ChannelDesignerError(message or f"AudioMuse API HTTP {exc.code}", status=exc.code) from exc
    except urllib.error.URLError as exc:
        raise ChannelDesignerError(f"Could not reach AudioMuse API: {exc.reason}") from exc


# ---------------------------------------------------------------------------
# Channel programming model
# ---------------------------------------------------------------------------

PROGRAMMING_TYPES = (
    ("clap_query", "Sonic vibe (CLAP text search)"),
    ("lyrics_query", "Lyrics theme (semantic)"),
    ("mood_centroid", "Mood cluster"),
    ("alchemy_anchor", "Song Alchemy anchor"),
    ("similar_seed", "Similar to seed track"),
)

REFRESH_MODES = (
    ("similar_to_last", "Similar to last played (recommended)"),
    ("similar_to_seed", "Similar to programming seed"),
    ("source_only", "Stay in source pool (allow repeats)"),
)

DIRECT_ALCHEMY_TYPES = frozenset({"alchemy_anchor", "similar_seed"})
PREVIEW_LIMIT_DEFAULT = 30
ANCHOR_BLEND_TRACKS = 8


def _slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "channel"


def _normalize_mount(value: str) -> str:
    value = value.strip()
    return value if value.startswith("/") else f"/{value}"


def _track_rows_from_results(results: Any) -> list[dict[str, Any]]:
    if not isinstance(results, list):
        return []
    rows: list[dict[str, Any]] = []
    for row in results:
        if not isinstance(row, dict):
            continue
        item_id = row.get("item_id") or row.get("id")
        if not item_id:
            continue
        rows.append(
            {
                "item_id": str(item_id),
                "title": row.get("title") or "Unknown",
                "author": row.get("author") or row.get("artist") or "Unknown",
            }
        )
    return rows


def preview_programming(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Run AudioMuse intelligence APIs and return normalized track rows."""
    ptype = profile["programming"]["type"]
    limit = int(profile["programming"].get("limit") or PREVIEW_LIMIT_DEFAULT)
    limit = max(5, min(80, limit))

    if ptype == "clap_query":
        query = (profile["programming"].get("query") or "").strip()
        if len(query) < 3:
            raise ChannelDesignerError("CLAP query must be at least 3 characters.")
        data = audiomuse_post("/api/clap/search", {"query": query, "limit": limit})
        return _track_rows_from_results(data.get("results") if isinstance(data, dict) else None)

    if ptype == "lyrics_query":
        query = (profile["programming"].get("query") or "").strip()
        if len(query) < 3:
            raise ChannelDesignerError("Lyrics query must be at least 3 characters.")
        data = audiomuse_post("/api/lyrics/search/text", {"query": query, "limit": limit})
        return _track_rows_from_results(data.get("results") if isinstance(data, dict) else None)

    if ptype == "mood_centroid":
        mood = (profile["programming"].get("mood") or "").strip().lower()
        centroid_index = profile["programming"].get("centroid_index")
        if not mood or centroid_index is None:
            raise ChannelDesignerError("Choose a mood cluster.")
        data = audiomuse_get(
            "/api/similar_tracks",
            params={
                "mood": mood,
                "centroid_index": str(int(centroid_index)),
                "n": str(limit),
                "eliminate_duplicates": "true",
            },
        )
        return _track_rows_from_results(data)

    if ptype == "alchemy_anchor":
        anchor_id = str(profile["programming"].get("anchor_id") or "").strip()
        if not anchor_id:
            raise ChannelDesignerError("Choose a Song Alchemy anchor.")
        data = audiomuse_post(
            "/api/alchemy",
            {
                "items": [{"id": anchor_id, "op": "ADD", "type": "anchor"}],
                "n": limit,
            },
        )
        return _track_rows_from_results(data.get("results") if isinstance(data, dict) else None)

    if ptype == "similar_seed":
        item_id = str(profile["programming"].get("seed_id") or "").strip()
        if not item_id:
            raise ChannelDesignerError("Pick a seed track.")
        data = audiomuse_get(
            "/api/similar_tracks",
            params={"item_id": item_id, "n": str(limit), "eliminate_duplicates": "true"},
        )
        return _track_rows_from_results(data)

    raise ChannelDesignerError(f"Unsupported programming type: {ptype}")


def enrich_preview(tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ids = [t["item_id"] for t in tracks]
    if not ids:
        return tracks
    try:
        scores = {row["item_id"]: row for row in get_score_data_by_ids(ids)}
    except Exception:
        scores = {}
    enriched: list[dict[str, Any]] = []
    for track in tracks:
        row = dict(track)
        score = scores.get(track["item_id"]) or {}
        row["tempo"] = score.get("tempo")
        row["energy"] = score.get("energy")
        moods = score.get("mood_vector") or score.get("moods")
        if isinstance(moods, dict):
            top = sorted(moods.items(), key=lambda kv: kv[1], reverse=True)[:2]
            row["mood"] = ", ".join(name for name, _ in top)
        else:
            row["mood"] = ""
        enriched.append(row)
    return enriched


def _centroid_from_alchemy_response(data: Any) -> list[float] | None:
    if not isinstance(data, dict):
        return None
    for key in ("add_centroid_vector", "centroid"):
        vec = data.get(key)
        if isinstance(vec, list) and vec:
            return [float(x) for x in vec]
    return None


def _blend_centroid_from_tracks(tracks: list[dict[str, Any]], *, n_results: int = 50) -> list[float]:
    blend_ids = [t["item_id"] for t in tracks[:ANCHOR_BLEND_TRACKS]]
    if not blend_ids:
        raise ChannelDesignerError("No tracks to build an anchor from.")
    data = audiomuse_post(
        "/api/alchemy",
        {
            "items": [{"id": item_id, "op": "ADD", "type": "song"} for item_id in blend_ids],
            "n": n_results,
        },
    )
    centroid = _centroid_from_alchemy_response(data)
    if not centroid:
        raise ChannelDesignerError(
            "AudioMuse could not build an anchor centroid from the preview tracks. "
            "Ensure those tracks are analyzed (embeddings in the library) and try Preview again."
        )
    return centroid


def ensure_programming_anchor(profile: dict[str, Any], tracks: list[dict[str, Any]]) -> int:
    """Map AudioMuse-native programming to a Song Alchemy anchor Alchemy FM can run."""
    ptype = profile["programming"]["type"]
    existing = profile.get("anchor_id")
    if ptype == "alchemy_anchor":
        return int(profile["programming"]["anchor_id"])

    if existing:
        return int(existing)

    station_name = profile["station"]["name"]
    centroid = _blend_centroid_from_tracks(tracks)
    anchor_name = f"AFM: {station_name}"[:80]
    data = audiomuse_post("/api/anchors", {"name": anchor_name, "centroid": centroid})
    anchor = data.get("anchor") if isinstance(data, dict) else None
    if not isinstance(anchor, dict) or not anchor.get("id"):
        raise ChannelDesignerError("Failed to save Song Alchemy anchor for this channel.")
    return int(anchor["id"])


def channel_profile_to_alchemy_payload(profile: dict[str, Any], tracks: list[dict[str, Any]]) -> dict[str, Any]:
    station = profile["station"]
    refresh = profile.get("refresh") or {}
    ptype = profile["programming"]["type"]

    if ptype == "similar_seed":
        source_type = "similar_seed"
        source_ref = str(profile["programming"]["seed_id"]).strip()
        identity_anchor_id = ""
    else:
        anchor_id = ensure_programming_anchor(profile, tracks)
        profile["anchor_id"] = anchor_id
        source_type = "alchemy_anchor"
        source_ref = str(anchor_id)
        identity_anchor_id = str(anchor_id)

    return {
        "name": station["name"],
        "slug": station["slug"],
        "description": station.get("description", "")[:120],
        "icecast_mount": station["icecast_mount"],
        "enabled": bool(station.get("enabled", True)),
        "source_type": source_type,
        "source_ref": source_ref,
        "queue_target": int(station.get("queue_target", 30)),
        "refresh_threshold": int(station.get("refresh_threshold", 10)),
        "artist_separation_minutes": int(station.get("artist_separation_minutes", 90)),
        "continuation_mode": refresh.get("mode", "similar_to_last"),
        "identity_anchor_id": identity_anchor_id,
        "bootstrap_queue": bool(station.get("bootstrap_queue", True)),
    }


def profile_from_form(form) -> dict[str, Any]:
    name = (form.get("name") or "").strip()
    if not name:
        raise ChannelDesignerError("Channel name is required.")

    ptype = (form.get("programming_type") or "clap_query").strip()
    editing_slug = (form.get("editing_slug") or "").strip()
    slug = editing_slug or (form.get("slug") or "").strip() or _slugify(name)
    refresh_mode = (form.get("refresh_mode") or "similar_to_last").strip()
    if refresh_mode not in {key for key, _ in REFRESH_MODES}:
        raise ChannelDesignerError(f"Unsupported refresh mode: {refresh_mode}")

    programming: dict[str, Any] = {"type": ptype, "limit": int(form.get("preview_limit") or PREVIEW_LIMIT_DEFAULT)}
    if ptype == "clap_query":
        programming["query"] = (form.get("clap_query") or "").strip()
    elif ptype == "lyrics_query":
        programming["query"] = (form.get("lyrics_query") or "").strip()
    elif ptype == "mood_centroid":
        programming["mood"] = (form.get("mood_name") or "").strip().lower()
        programming["centroid_index"] = form.get("centroid_index")
    elif ptype == "alchemy_anchor":
        programming["anchor_id"] = (form.get("anchor_id") or "").strip()
    elif ptype == "similar_seed":
        programming["seed_id"] = (
            (form.get("seed_id") or form.get("pick_seed") or "").strip()
        )
    else:
        raise ChannelDesignerError(f"Unsupported programming type: {ptype}")

    return {
        "programming": programming,
        "refresh": {"mode": refresh_mode},
        "station": {
            "name": name,
            "slug": slug,
            "description": (form.get("description") or "").strip(),
            "icecast_mount": _normalize_mount(form.get("icecast_mount") or slug),
            "enabled": form.get("enabled") == "on",
            "bootstrap_queue": form.get("bootstrap_queue") == "on",
            "queue_target": max(5, min(200, int(form.get("queue_target") or 30))),
            "refresh_threshold": max(1, min(100, int(form.get("refresh_threshold") or 10))),
            "artist_separation_minutes": max(0, int(form.get("artist_separation_minutes") or 90)),
        },
        "anchor_id": (form.get("saved_anchor_id") or "").strip() or None,
    }


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

bp = Blueprint("alchemy_fm_bridge", __name__)


def migrate(db) -> None:
    cur = db.cursor()
    channels = table("channels")
    cur.execute(
        "CREATE TABLE IF NOT EXISTS "
        + channels
        + " ("
        "id SERIAL PRIMARY KEY, "
        "name TEXT NOT NULL, "
        "slug TEXT NOT NULL UNIQUE, "
        "profile_json TEXT NOT NULL DEFAULT '{}', "
        "preview_ids_json TEXT NOT NULL DEFAULT '[]', "
        "anchor_id INTEGER, "
        "alchemy_station_id INTEGER, "
        "last_preview_at TEXT, "
        "last_pushed_at TEXT, "
        "last_action TEXT, "
        "last_error TEXT NOT NULL DEFAULT ''"
        ")"
    )
    # Legacy table from v1 — keep for upgrades
    profiles = table("profiles")
    cur.execute(
        "CREATE TABLE IF NOT EXISTS "
        + profiles
        + " ("
        "id SERIAL PRIMARY KEY, "
        "slug TEXT NOT NULL UNIQUE, "
        "alchemy_station_id INTEGER, "
        "config_json TEXT NOT NULL DEFAULT '{}', "
        "last_pushed_at TEXT, "
        "last_action TEXT, "
        "last_error TEXT NOT NULL DEFAULT ''"
        ")"
    )
    db.commit()
    cur.close()


def _client() -> AlchemyFmClient:
    base_url = (get_setting("alchemyfm_url") or "").strip()
    username = (get_setting("alchemyfm_username") or "admin").strip() or "admin"
    password = get_setting("alchemyfm_password") or ""
    if not base_url:
        raise ChannelDesignerError("Set your Alchemy FM URL in plugin settings first.")
    if not password:
        raise ChannelDesignerError("Set your Alchemy FM admin password in plugin settings first.")
    return AlchemyFmClient(base_url, username, password)


def _save_channel(
    profile: dict[str, Any],
    *,
    preview_ids: list[str],
    station: dict[str, Any] | None = None,
    action: str = "",
) -> None:
    db = get_db()
    cur = db.cursor()
    channels = table("channels")
    slug = profile["station"]["slug"]
    anchor_id = profile.get("anchor_id")
    pushed_at = "to_char(now(), 'YYYY-MM-DD HH24:MI:SS')" if action else "NULL"
    cur.execute(
        "INSERT INTO "
        + channels
        + " (name, slug, profile_json, preview_ids_json, anchor_id, alchemy_station_id, "
        "last_preview_at, last_pushed_at, last_action, last_error) "
        "VALUES (%s, %s, %s, %s, %s, %s, to_char(now(), 'YYYY-MM-DD HH24:MI:SS'), "
        + pushed_at
        + ", %s, '') "
        "ON CONFLICT (slug) DO UPDATE SET "
        "name = EXCLUDED.name, "
        "profile_json = EXCLUDED.profile_json, "
        "preview_ids_json = EXCLUDED.preview_ids_json, "
        "anchor_id = EXCLUDED.anchor_id, "
        "alchemy_station_id = COALESCE(EXCLUDED.alchemy_station_id, "
        + channels
        + ".alchemy_station_id), "
        "last_preview_at = EXCLUDED.last_preview_at, "
        "last_pushed_at = COALESCE(EXCLUDED.last_pushed_at, "
        + channels
        + ".last_pushed_at), "
        "last_action = CASE WHEN EXCLUDED.last_action <> '' THEN EXCLUDED.last_action ELSE "
        + channels
        + ".last_action END, "
        "last_error = ''",
        (
            profile["station"]["name"],
            slug,
            json.dumps(profile),
            json.dumps(preview_ids),
            int(anchor_id) if anchor_id else None,
            int(station.get("id")) if station else None,
            action,
        ),
    )
    db.commit()
    cur.close()


def _load_saved_channel(slug: str) -> tuple[dict[str, Any], list[str]] | None:
    slug = slug.strip()
    if not slug:
        return None
    db = get_db()
    cur = db.cursor()
    channels = table("channels")
    try:
        cur.execute(
            "SELECT profile_json, preview_ids_json FROM " + channels + " WHERE slug = %s",
            (slug,),
        )
        row = cur.fetchone()
    except Exception:
        db.rollback()
        return None
    finally:
        cur.close()
    if not row:
        return None
    try:
        profile = json.loads(row[0] or "{}")
        preview_ids = json.loads(row[1] or "[]")
        if not isinstance(preview_ids, list):
            preview_ids = []
    except json.JSONDecodeError:
        return None
    return profile, [str(i) for i in preview_ids if i]


def _preview_tracks_from_ids(item_ids: list[str]) -> list[dict[str, Any]]:
    if not item_ids:
        return []
    try:
        scores = get_score_data_by_ids(item_ids)
    except Exception:
        scores = []
    by_id = {row["item_id"]: row for row in scores if row.get("item_id")}
    tracks: list[dict[str, Any]] = []
    for item_id in item_ids:
        row = by_id.get(item_id) or {}
        tracks.append(
            {
                "item_id": item_id,
                "title": row.get("title") or "Unknown",
                "author": row.get("author") or row.get("artist") or "Unknown",
            }
        )
    return enrich_preview(tracks)


def _apply_loaded_channel(
    slug: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    loaded = _load_saved_channel(slug)
    if not loaded:
        raise ChannelDesignerError(f"No saved channel found for slug '{slug}'.")
    profile, preview_ids = loaded
    values = _form_values_from_profile(profile)
    values["editing_slug"] = slug
    preview_tracks = _preview_tracks_from_ids(preview_ids)
    return values, preview_tracks, profile.get("station", {}).get("name") or slug


def _record_channel_error(slug: str, message: str) -> None:
    db = get_db()
    cur = db.cursor()
    channels = table("channels")
    cur.execute(
        "INSERT INTO " + channels + " (name, slug, profile_json, last_error) "
        "VALUES (%s, %s, '{}', %s) "
        "ON CONFLICT (slug) DO UPDATE SET last_error = EXCLUDED.last_error",
        (slug, slug, message[:500]),
    )
    db.commit()
    cur.close()


def _channel_rows() -> list[tuple]:
    db = get_db()
    cur = db.cursor()
    channels = table("channels")
    try:
        cur.execute(
            "SELECT name, slug, profile_json, anchor_id, alchemy_station_id, "
            "last_preview_at, last_pushed_at, last_action, last_error "
            "FROM " + channels + " ORDER BY last_pushed_at DESC NULLS LAST, slug ASC"
        )
        return cur.fetchall()
    except Exception:
        db.rollback()
        return []
    finally:
        cur.close()


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------


def _flash_html(message: str, level: str = "ok") -> str:
    css = "background:#ecfdf5;border:1px solid #6ee7b7;color:#065f46"
    if level != "ok":
        css = "background:#fef2f2;border:1px solid #fca5a5;color:#991b1b"
    return f'<p style="padding:0.75rem 1rem;border-radius:8px;{css}">{html.escape(message)}</p>'


def _select_options(options: list[tuple[str, str]], selected: str) -> str:
    return "".join(
        f'<option value="{html.escape(key)}"{" selected" if key == selected else ""}>'
        f"{html.escape(label)}</option>"
        for key, label in options
    )


def _anchors() -> list[dict[str, Any]]:
    try:
        data = audiomuse_get("/api/anchors")
    except ChannelDesignerError:
        return []
    anchors = data.get("anchors") if isinstance(data, dict) else None
    return [a for a in anchors if isinstance(a, dict) and a.get("id")] if isinstance(anchors, list) else []


def _mood_centroids_data() -> dict[str, Any]:
    try:
        data = audiomuse_get("/api/mood_centroids")
        return data if isinstance(data, dict) else {}
    except ChannelDesignerError:
        return {}


def _mood_cluster_label(meta: dict[str, Any], idx: int) -> str:
    index = meta.get("index", idx)
    label = f"Cluster {index}"
    tags = meta.get("top_tags") or meta.get("tags") or meta.get("label")
    if isinstance(tags, list) and tags:
        label = ", ".join(str(t) for t in tags[:3])
        n_songs = meta.get("n_songs")
        if n_songs:
            label += f" ({n_songs} tracks)"
    elif isinstance(tags, str) and tags:
        label = tags
    return label


def _mood_options(selected_mood: str, selected_index: str) -> tuple[str, str]:
    data = _mood_centroids_data()
    if not data:
        return (
            "<option value=''>Could not load moods</option>",
            "<option value=''>—</option>",
        )

    mood_opts = ['<option value="">Choose mood…</option>']
    for mood in sorted(data.keys()):
        sel = " selected" if mood == selected_mood else ""
        mood_opts.append(f'<option value="{html.escape(mood)}"{sel}>{html.escape(mood.title())}</option>')

    centroid_opts = ['<option value="">Choose cluster…</option>']
    if selected_mood and selected_mood in data:
        centroids = data[selected_mood]
        if isinstance(centroids, list):
            for idx, meta in enumerate(centroids):
                if not isinstance(meta, dict):
                    continue
                cluster_idx = meta.get("index", idx)
                label = _mood_cluster_label(meta, idx)
                sel = " selected" if str(cluster_idx) == str(selected_index) else ""
                centroid_opts.append(
                    f'<option value="{cluster_idx}"{sel}>{html.escape(label)}</option>'
                )
    return ("".join(mood_opts), "".join(centroid_opts))


def _search_tracks(query: str) -> list[dict[str, Any]]:
    query = query.strip()
    if len(query) < 2:
        return []
    try:
        data = audiomuse_get(
            "/api/search_tracks",
            params={"search_query": query, "end": "20"},
        )
    except ChannelDesignerError:
        return []
    return [t for t in data if isinstance(t, dict) and t.get("item_id")] if isinstance(data, list) else []


def _preview_table_html(tracks: list[dict[str, Any]]) -> str:
    if not tracks:
        return "<p class='hint'>No tracks matched this programming yet. Run a preview.</p>"
    rows = []
    for track in tracks:
        tempo = track.get("tempo")
        energy = track.get("energy")
        tempo_s = f"{float(tempo):.0f}" if tempo is not None else "—"
        energy_s = f"{float(energy):.2f}" if energy is not None else "—"
        rows.append(
            "<tr>"
            f"<td style='padding:0.4rem 0.6rem;border-bottom:1px solid #eee;'>{html.escape(track['title'])}</td>"
            f"<td style='padding:0.4rem 0.6rem;border-bottom:1px solid #eee;'>{html.escape(track['author'])}</td>"
            f"<td style='padding:0.4rem 0.6rem;border-bottom:1px solid #eee;'>{tempo_s}</td>"
            f"<td style='padding:0.4rem 0.6rem;border-bottom:1px solid #eee;'>{energy_s}</td>"
            f"<td style='padding:0.4rem 0.6rem;border-bottom:1px solid #eee;'>{html.escape(track.get('mood') or '—')}</td>"
            "</tr>"
        )
    return (
        f"<p><strong>{len(tracks)}</strong> tracks in preview (from your AudioMuse library analysis).</p>"
        "<table style='width:100%;border-collapse:collapse;font-size:0.92rem;'>"
        "<thead><tr>"
        "<th style='text-align:left;padding:0.4rem 0.6rem;border-bottom:1px solid #ccc;'>Title</th>"
        "<th style='text-align:left;padding:0.4rem 0.6rem;border-bottom:1px solid #ccc;'>Artist</th>"
        "<th style='text-align:left;padding:0.4rem 0.6rem;border-bottom:1px solid #ccc;'>BPM</th>"
        "<th style='text-align:left;padding:0.4rem 0.6rem;border-bottom:1px solid #ccc;'>Energy</th>"
        "<th style='text-align:left;padding:0.4rem 0.6rem;border-bottom:1px solid #ccc;'>Mood</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _programming_fields_html(values: dict[str, Any]) -> str:
    ptype = values.get("programming_type", "clap_query")
    hidden = lambda key: "" if ptype == key else " hidden"

    mood_opts, centroid_opts = _mood_options(
        str(values.get("mood_name", "")),
        str(values.get("centroid_index", "")),
    )

    seed_results = values.get("seed_search_results") or []
    seed_results_html = ""
    if seed_results:
        items = []
        for track in seed_results:
            item_id = str(track.get("item_id"))
            title = track.get("title") or "Unknown"
            artist = track.get("author") or track.get("artist") or "Unknown"
            items.append(
                "<li>"
                f'<button type="submit" name="pick_seed" value="{html.escape(item_id)}" '
                'formnovalidate style="width:100%;text-align:left;padding:0.5rem;">'
                f"{html.escape(title)} — {html.escape(artist)}"
                "</button></li>"
            )
        seed_results_html = "<ul style='list-style:none;padding:0;margin:0.5rem 0;'>" + "".join(items) + "</ul>"

    anchor_opts = ['<option value="">Choose anchor…</option>']
    for anchor in _anchors():
        aid = str(anchor["id"])
        sel = " selected" if aid == str(values.get("anchor_id", "")) else ""
        anchor_opts.append(
            f'<option value="{html.escape(aid)}"{sel}>{html.escape(anchor.get("name") or aid)}</option>'
        )

    return (
        "<fieldset style='border:1px solid #ddd;border-radius:8px;padding:1rem;'>"
        "<legend><strong>1. Programming</strong> — how AudioMuse finds tracks</legend>"
        "<div><label>Programming type</label>"
        f"<select name='programming_type' id='programming_type'>"
        f"{_select_options(PROGRAMMING_TYPES, ptype)}</select></div>"
        f"<div id='field-clap'{hidden('clap_query')} style='margin-top:0.75rem;'>"
        "<label>Sonic vibe (describe the sound)</label>"
        f"<input name='clap_query' style='width:100%;' placeholder='e.g. late-night yacht rock, warm and mellow' "
        f"value='{html.escape(str(values.get('clap_query', '')))}'>"
        "<p class='hint'>Uses CLAP text-to-audio search across your analyzed library.</p></div>"
        f"<div id='field-lyrics'{hidden('lyrics_query')} style='margin-top:0.75rem;'>"
        "<label>Lyrics theme</label>"
        f"<input name='lyrics_query' style='width:100%;' placeholder='e.g. songs about the open road' "
        f"value='{html.escape(str(values.get('lyrics_query', '')))}'>"
        "<p class='hint'>Semantic lyrics search — meaning and themes, not just keywords.</p></div>"
        f"<div id='field-mood'{hidden('mood_centroid')} style='margin-top:0.75rem;display:grid;gap:0.5rem;'>"
        "<div><label>Mood</label><select name='mood_name'>" + mood_opts + "</select></div>"
        "<div><label>Cluster</label><select name='centroid_index' id='centroid_index'>" + centroid_opts + "</select></div>"
        "<p class='hint'>Each mood has sub-clusters from your library analysis — pick one that matches "
        "the vibe (tags show the dominant traits in that cluster).</p></div>"
        f"<div id='field-anchor'{hidden('alchemy_anchor')} style='margin-top:0.75rem;'>"
        "<label>Song Alchemy anchor</label>"
        f"<select name='anchor_id'>{''.join(anchor_opts)}</select></div>"
        f"<div id='field-seed'{hidden('similar_seed')} style='margin-top:0.75rem;'>"
        "<label>Search seed track</label>"
        f"<input name='seed_search' value='{html.escape(str(values.get('seed_search', '')))}'> "
        "<button type='submit' name='action' value='search_seed' formnovalidate>Search</button>"
        f"{seed_results_html}"
        f"<input name='seed_id' placeholder='Track item id' value='{html.escape(str(values.get('seed_id', '')))}'>"
        "</div>"
        "<div style='margin-top:0.75rem;'>"
        "<label>Preview size</label> "
        f"<input type='number' name='preview_limit' min='10' max='80' value='{html.escape(str(values.get('preview_limit', PREVIEW_LIMIT_DEFAULT)))}'>"
        "</div>"
        "<div style='margin-top:1rem;'>"
        "<button type='submit' name='action' value='preview'>Preview programming</button>"
        "</div></fieldset>"
    )


def _deploy_fields_html(values: dict[str, Any]) -> str:
    editing_slug = (values.get("editing_slug") or "").strip()
    slug_readonly = " readonly style='opacity:0.75'" if editing_slug else ""
    slug_extra = ""
    if editing_slug:
        slug_extra = (
            f"<input type='hidden' name='editing_slug' value='{html.escape(editing_slug)}'>"
            "<p class='hint'>Slug is fixed after first deploy (Alchemy FM identifies stations by slug).</p>"
        )
    deploy_label = "Update on Alchemy FM" if editing_slug else "Deploy to Alchemy FM"
    edit_banner = ""
    if editing_slug:
        edit_banner = (
            f"<p style='margin:0 0 0.75rem;padding:0.5rem 0.75rem;background:#eff6ff;"
            f"border:1px solid #93c5fd;border-radius:6px;'>"
            f"Editing channel <strong>{html.escape(editing_slug)}</strong>. "
            f"Change name, description, or queue settings, then click "
            f"<strong>{html.escape(deploy_label)}</strong>."
            f" <button type='submit' name='action' value='new_channel' formnovalidate "
            f"style='margin-left:0.5rem;'>New channel</button></p>"
        )
    return (
        "<fieldset style='border:1px solid #ddd;border-radius:8px;padding:1rem;margin-top:1rem;'>"
        "<legend><strong>2. Channel + deploy</strong> — station on Alchemy FM</legend>"
        f"{edit_banner}"
        "<div style='display:grid;gap:0.75rem;'>"
        "<div><label>Channel name</label>"
        f"<input name='name' required value='{html.escape(str(values.get('name', '')))}'></div>"
        "<div><label>Slug</label>"
        f"<input name='slug' placeholder='auto from name' "
        f"value='{html.escape(str(values.get('slug', editing_slug or '')))}'{slug_readonly}>"
        f"{slug_extra}</div>"
        "<div><label>Description</label>"
        f"<textarea name='description' maxlength='120' rows='2'>{html.escape(str(values.get('description', '')))}</textarea></div>"
        "<div><label>Icecast mount</label>"
        f"<input name='icecast_mount' placeholder='/channel-slug' value='{html.escape(str(values.get('icecast_mount', '')))}'></div>"
        "<div><label>When pool runs low</label>"
        f"<select name='refresh_mode'>{_select_options(REFRESH_MODES, str(values.get('refresh_mode', 'similar_to_last')))}</select>"
        "<p class='hint'>For CLAP/lyrics/mood channels, the plugin saves a Song Alchemy anchor from your preview "
        "so Alchemy FM can keep refilling 24/7.</p></div>"
        "<div style='display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0.75rem;'>"
        "<div><label>Queue target</label>"
        f"<input type='number' name='queue_target' min='5' max='200' value='{html.escape(str(values.get('queue_target', 30)))}'></div>"
        "<div><label>Refresh below</label>"
        f"<input type='number' name='refresh_threshold' min='1' max='100' value='{html.escape(str(values.get('refresh_threshold', 10)))}'></div>"
        "<div><label>Artist separation (min)</label>"
        f"<input type='number' name='artist_separation_minutes' min='0' value='{html.escape(str(values.get('artist_separation_minutes', 90)))}'></div>"
        "</div>"
        "<label><input type='checkbox' name='enabled'"
        f"{' checked' if values.get('enabled', True) else ''}> Start on air after push</label> "
        "<label><input type='checkbox' name='bootstrap_queue'"
        f"{' checked' if values.get('bootstrap_queue', True) else ''}> Bootstrap queue immediately</label>"
        f"<input type='hidden' name='saved_anchor_id' value='{html.escape(str(values.get('saved_anchor_id', '')))}'>"
        "<div style='display:flex;gap:0.75rem;flex-wrap:wrap;margin-top:0.5rem;'>"
        f"<button type='submit' name='action' value='push'>{html.escape(deploy_label)}</button>"
        "<button type='submit' name='action' value='test' formnovalidate>Test Alchemy connection</button>"
        "</div></div></fieldset>"
    )


def _channels_table_html() -> str:
    rows = _channel_rows()
    if not rows:
        return "<p>No saved channels yet. Preview programming above, then deploy.</p>"
    body = (
        "<table style='width:100%;border-collapse:collapse;margin-top:1rem;font-size:0.92rem;'>"
        "<thead><tr>"
        "<th style='text-align:left;padding:0.5rem;border-bottom:1px solid #ddd;'>Channel</th>"
        "<th style='text-align:left;padding:0.5rem;border-bottom:1px solid #ddd;'>Programming</th>"
        "<th style='text-align:left;padding:0.5rem;border-bottom:1px solid #ddd;'>Anchor</th>"
        "<th style='text-align:left;padding:0.5rem;border-bottom:1px solid #ddd;'>Last push</th>"
        "<th style='text-align:left;padding:0.5rem;border-bottom:1px solid #ddd;'>Status</th>"
        "<th style='text-align:left;padding:0.5rem;border-bottom:1px solid #ddd;'></th>"
        "</tr></thead><tbody>"
    )
    for name, slug, profile_json, anchor_id, _station_id, _preview_at, pushed_at, action, error in rows:
        try:
            profile = json.loads(profile_json or "{}")
            ptype = profile.get("programming", {}).get("type", "?")
            query = (
                profile.get("programming", {}).get("query")
                or profile.get("programming", {}).get("seed_id")
                or profile.get("programming", {}).get("anchor_id")
                or profile.get("programming", {}).get("mood")
                or ""
            )
            prog = f"{ptype}: {query}"[:60]
        except json.JSONDecodeError:
            prog = "—"
        status = html.escape(error) if error else html.escape(action or "saved")
        body += (
            "<tr>"
            f"<td style='padding:0.5rem;border-bottom:1px solid #eee;'><strong>{html.escape(name)}</strong><br>"
            f"<span style='opacity:0.7'>{html.escape(slug)}</span></td>"
            f"<td style='padding:0.5rem;border-bottom:1px solid #eee;'>{html.escape(prog)}</td>"
            f"<td style='padding:0.5rem;border-bottom:1px solid #eee;'>{html.escape(str(anchor_id or ''))}</td>"
            f"<td style='padding:0.5rem;border-bottom:1px solid #eee;'>{html.escape(str(pushed_at or ''))}</td>"
            f"<td style='padding:0.5rem;border-bottom:1px solid #eee;'>{status}</td>"
            f"<td style='padding:0.5rem;border-bottom:1px solid #eee;'>"
            f"<form method='post' style='display:inline;margin:0;'>"
            f"<input type='hidden' name='action' value='edit'>"
            f"<input type='hidden' name='load_slug' value='{html.escape(slug)}'>"
            f"<button type='submit'>Edit</button></form>"
            "</td>"
            "</tr>"
        )
    return body + "</tbody></table>"


def _form_values_from_profile(profile: dict[str, Any]) -> dict[str, Any]:
    station = profile.get("station") or {}
    programming = profile.get("programming") or {}
    ptype = programming.get("type", "clap_query")
    values: dict[str, Any] = {
        "programming_type": ptype,
        "name": station.get("name", ""),
        "slug": station.get("slug", ""),
        "description": station.get("description", ""),
        "icecast_mount": station.get("icecast_mount", ""),
        "refresh_mode": (profile.get("refresh") or {}).get("mode", "similar_to_last"),
        "queue_target": station.get("queue_target", 30),
        "refresh_threshold": station.get("refresh_threshold", 10),
        "artist_separation_minutes": station.get("artist_separation_minutes", 90),
        "enabled": station.get("enabled", True),
        "bootstrap_queue": station.get("bootstrap_queue", True),
        "preview_limit": programming.get("limit", PREVIEW_LIMIT_DEFAULT),
        "saved_anchor_id": profile.get("anchor_id") or "",
    }
    if ptype == "clap_query":
        values["clap_query"] = programming.get("query", "")
    elif ptype == "lyrics_query":
        values["lyrics_query"] = programming.get("query", "")
    elif ptype == "mood_centroid":
        values["mood_name"] = programming.get("mood", "")
        values["centroid_index"] = programming.get("centroid_index", "")
    elif ptype == "alchemy_anchor":
        values["anchor_id"] = programming.get("anchor_id", "")
    elif ptype == "similar_seed":
        values["seed_id"] = programming.get("seed_id", "")
    return values


def _page_script(mood_centroids: dict[str, Any] | None = None) -> str:
    mood_json = json.dumps(mood_centroids or {})
    return f"""
<script type="application/json" id="mood-centroids-data">{mood_json}</script>
<script>
(function() {{
  const typeSelect = document.getElementById('programming_type');
  const moodDataEl = document.getElementById('mood-centroids-data');
  const moodData = moodDataEl ? JSON.parse(moodDataEl.textContent || '{{}}') : {{}};
  const sections = {{
    clap_query: document.getElementById('field-clap'),
    lyrics_query: document.getElementById('field-lyrics'),
    mood_centroid: document.getElementById('field-mood'),
    alchemy_anchor: document.getElementById('field-anchor'),
    similar_seed: document.getElementById('field-seed'),
  }};
  function syncType() {{
    if (!typeSelect) return;
    const t = typeSelect.value;
    Object.entries(sections).forEach(([key, el]) => {{
      if (!el) return;
      el.hidden = key !== t;
    }});
  }}
  function clusterLabel(meta, idx) {{
    const index = meta.index != null ? meta.index : idx;
    let label = 'Cluster ' + index;
    const tags = meta.top_tags || meta.tags || meta.label;
    if (Array.isArray(tags) && tags.length) {{
      label = tags.slice(0, 3).join(', ');
      if (meta.n_songs) label += ' (' + meta.n_songs + ' tracks)';
    }} else if (typeof tags === 'string' && tags) {{
      label = tags;
    }}
    return label;
  }}
  function fillClusters() {{
    const moodSelect = document.querySelector('select[name="mood_name"]');
    const clusterSelect = document.getElementById('centroid_index');
    if (!moodSelect || !clusterSelect) return;
    const mood = moodSelect.value;
    const prev = clusterSelect.value;
    clusterSelect.innerHTML = '<option value="">Choose cluster…</option>';
    const list = moodData[mood];
    if (!Array.isArray(list)) return;
    list.forEach((meta, idx) => {{
      if (!meta || typeof meta !== 'object') return;
      const opt = document.createElement('option');
      const clusterIdx = meta.index != null ? String(meta.index) : String(idx);
      opt.value = clusterIdx;
      opt.textContent = clusterLabel(meta, idx);
      if (clusterIdx === prev) opt.selected = true;
      clusterSelect.appendChild(opt);
    }});
  }}
  if (typeSelect) {{
    typeSelect.addEventListener('change', syncType);
    syncType();
  }}
  const moodSelect = document.querySelector('select[name="mood_name"]');
  if (moodSelect) {{
    moodSelect.addEventListener('change', fillClusters);
    fillClusters();
  }}
}})();
</script>
"""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@bp.route("/", methods=["GET", "POST"])
def home():
    flash = ""
    preview_tracks: list[dict[str, Any]] = []
    values: dict[str, Any] = {
        "programming_type": "clap_query",
        "refresh_mode": "similar_to_last",
        "preview_limit": PREVIEW_LIMIT_DEFAULT,
        "queue_target": 30,
        "refresh_threshold": 10,
        "artist_separation_minutes": 90,
        "enabled": True,
        "bootstrap_queue": True,
    }

    if request.method == "GET":
        edit_slug = (request.args.get("edit") or "").strip()
        if edit_slug:
            try:
                values, preview_tracks, channel_name = _apply_loaded_channel(edit_slug)
                flash = _flash_html(f"Editing channel '{channel_name}'.", "ok")
            except ChannelDesignerError as exc:
                flash = _flash_html(str(exc), "error")

    if request.method == "POST":
        action = (request.form.get("action") or "preview").strip()

        if action == "edit":
            load_slug = (request.form.get("load_slug") or "").strip()
            try:
                values, preview_tracks, channel_name = _apply_loaded_channel(load_slug)
                flash = _flash_html(f"Editing channel '{channel_name}'.", "ok")
            except ChannelDesignerError as exc:
                flash = _flash_html(str(exc), "error")
        elif action == "new_channel":
            values = {
                "programming_type": "clap_query",
                "refresh_mode": "similar_to_last",
                "preview_limit": PREVIEW_LIMIT_DEFAULT,
                "queue_target": 30,
                "refresh_threshold": 10,
                "artist_separation_minutes": 90,
                "enabled": True,
                "bootstrap_queue": True,
            }
            preview_tracks = []
            flash = _flash_html("New channel — fill in programming and deploy.", "ok")
        else:
            values.update({k: request.form.get(k, values.get(k, "")) for k in request.form})
            values["enabled"] = request.form.get("enabled") == "on"
            values["bootstrap_queue"] = request.form.get("bootstrap_queue") == "on"

            pick_seed = (request.form.get("pick_seed") or "").strip()
            if pick_seed:
                values["seed_id"] = pick_seed
                values["programming_type"] = "similar_seed"
                if request.form.get("seed_search"):
                    values["seed_search_results"] = _search_tracks(request.form.get("seed_search") or "")
            elif action == "search_seed":
                values["seed_search_results"] = _search_tracks(request.form.get("seed_search") or "")
            elif action == "test":
                try:
                    stations = _client().test_connection()
                    flash = _flash_html(f"Alchemy FM connected — {len(stations)} station(s) on air.", "ok")
                except ChannelDesignerError as exc:
                    flash = _flash_html(str(exc), "error")
            else:
                try:
                    profile = profile_from_form(request.form)
                    values = _form_values_from_profile(profile)
                    if (request.form.get("editing_slug") or "").strip():
                        values["editing_slug"] = request.form.get("editing_slug").strip()

                    if action == "preview":
                        raw = preview_programming(profile)
                        if not raw:
                            raise ChannelDesignerError("No tracks matched this programming.")
                        preview_tracks = enrich_preview(raw)
                        _save_channel(profile, preview_ids=[t["item_id"] for t in preview_tracks])
                        flash = _flash_html(
                            f"Preview ready — {len(preview_tracks)} tracks from AudioMuse. "
                            "Review below, then deploy to Alchemy FM.",
                            "ok",
                        )
                    elif action == "push":
                        raw = preview_programming(profile)
                        if not raw:
                            raise ChannelDesignerError(
                                "No tracks to deploy. Preview programming first or broaden your criteria."
                            )
                        preview_tracks = enrich_preview(raw)
                        payload = channel_profile_to_alchemy_payload(profile, raw)
                        slug = payload["slug"]
                        station, push_action = _client().push_station(
                            payload,
                            slug=slug,
                            bootstrap=bool(payload.get("bootstrap_queue")),
                        )
                        _save_channel(
                            profile,
                            preview_ids=[t["item_id"] for t in preview_tracks],
                            station=station,
                            action=push_action,
                        )
                        anchor_note = ""
                        if profile["programming"]["type"] not in DIRECT_ALCHEMY_TYPES:
                            anchor_note = (
                                f" Created Song Alchemy anchor {profile.get('anchor_id')} for 24/7 refill."
                            )
                        flash = _flash_html(
                            f"Channel '{station.get('name')}' {push_action} on Alchemy FM "
                            f"(id {station.get('id')}).{anchor_note}",
                            "ok",
                        )
                        values = _form_values_from_profile(profile)
                        values["editing_slug"] = slug
                        logger.info(
                            "alchemy_fm_bridge deployed slug=%s action=%s anchor=%s",
                            slug,
                            push_action,
                            profile.get("anchor_id"),
                        )
                except ChannelDesignerError as exc:
                    slug = (
                        request.form.get("editing_slug")
                        or request.form.get("slug")
                        or _slugify(request.form.get("name") or "channel")
                    ).strip()
                    _record_channel_error(slug, str(exc))
                    flash = _flash_html(str(exc), "error")

    body = (
        f"<p style='opacity:0.85;margin-bottom:1rem;'>Channel Designer v{PLUGIN_VERSION} — "
        "use AudioMuse intelligence (CLAP, lyrics, moods, anchors) to audition programming, "
        "then deploy a live station to <strong>Alchemy FM</strong>.</p>"
        f"{flash}"
        "<form method='post' style='max-width:52rem;display:grid;gap:0.5rem;'>"
        f"{_programming_fields_html(values)}"
        f"{_deploy_fields_html(values)}"
        "</form>"
        "<fieldset style='border:1px solid #ddd;border-radius:8px;padding:1rem;margin-top:1.5rem;'>"
        "<legend><strong>Preview</strong></legend>"
        f"{_preview_table_html(preview_tracks)}"
        "</fieldset>"
        "<h3 style='margin-top:2rem;'>Saved channels</h3>"
        f"{_channels_table_html()}"
        f"{_page_script(_mood_centroids_data())}"
    )
    return render_page(body, title="Alchemy FM Channel Designer")


@bp.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        set_setting("alchemyfm_url", (request.form.get("alchemyfm_url") or "").strip().rstrip("/"))
        set_setting("alchemyfm_username", (request.form.get("alchemyfm_username") or "admin").strip())
        password = request.form.get("alchemyfm_password")
        if password:
            set_setting("alchemyfm_password", password)
        token = request.form.get("audiomuse_api_token")
        if token:
            set_setting("audiomuse_api_token", token.strip())
        return redirect(manage_plugins_url())

    alchemyfm_url = get_setting("alchemyfm_url", "")
    alchemyfm_username = get_setting("alchemyfm_username", "admin")
    audiomuse_api_token = get_setting("audiomuse_api_token", "")
    body = (
        "<form method='post' style='display:grid;gap:1rem;max-width:36rem;'>"
        "<p>Connect to your Alchemy FM broadcast instance. Credentials match "
        "<code>ADMIN_USERNAME</code> / <code>ADMIN_PASSWORD</code> in Alchemy FM.</p>"
        "<p class='hint'><strong>Cloudflare / public URL:</strong> If you use "
        "<code>https://alchemyfm.mmagtech.com</code>, allow server-to-server access to "
        "<code>/api/admin/*</code> from your AudioMuse host (WAF skip rule or bypass "
        "Bot Fight Mode). Otherwise use a LAN/direct URL that does not go through Cloudflare.</p>"
        "<div><label>Alchemy FM URL</label>"
        f"<input name='alchemyfm_url' required placeholder='https://alchemyfm.example.com' "
        f"value='{html.escape(alchemyfm_url)}'></div>"
        "<div><label>Admin username</label>"
        f"<input name='alchemyfm_username' value='{html.escape(alchemyfm_username)}'></div>"
        "<div><label>Admin password</label>"
        "<input name='alchemyfm_password' type='password' autocomplete='new-password' "
        "placeholder='Leave blank to keep current password'></div>"
        "<div><label>AudioMuse API token (optional)</label>"
        f"<input name='audiomuse_api_token' type='password' autocomplete='new-password' "
        f"placeholder='Only if AudioMuse auth is enabled' value='{html.escape(audiomuse_api_token)}'></div>"
        "<button type='submit'>Save</button>"
        "</form>"
    )
    return render_page(body, title="Alchemy FM Bridge Settings")


def register(ctx):
    ctx.on_install(migrate)
    ctx.add_blueprint(bp)
    ctx.add_menu_item("Alchemy FM", "alchemy_fm_bridge.home", admin_only=True)
