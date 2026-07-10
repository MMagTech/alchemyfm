"""AudioMuse-native channel designer — preview programming, then deploy to Alchemy FM."""

from __future__ import annotations

import base64
import html
import json
import re
import uuid
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from flask import Blueprint, request, redirect, url_for

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

PLUGIN_VERSION = "3.1.0"
PLUGIN_ID = "alchemy_fm_bridge"
CRON_TASK_LIVING = "refresh_living"
CRON_TASK_TYPE = f"plugin.{PLUGIN_ID}.{CRON_TASK_LIVING}"
CRON_TASK_LABEL = "Alchemy FM"
HELP_DOC_URL = (
    "https://github.com/MMagTech/alchemyfm/blob/master/docs/CHANNEL_DESIGNER_HELP.md"
)

ALCHEMY_FM_USER_AGENT = (
    "AlchemyFmBridge/3.0 AudioMuse-Plugin (+https://github.com/MMagTech/alchemyfm)"
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

    def delete_station(self, station_id: int) -> None:
        self._request("DELETE", f"/api/admin/stations/{int(station_id)}")

    def refresh_queue(self, station_id: int) -> dict[str, Any]:
        result = self._request("POST", f"/api/admin/stations/{int(station_id)}/refresh-queue")
        if not isinstance(result, dict):
            raise ChannelDesignerError("Unexpected response when refreshing station queue")
        return result

    def rebuild_m3u(self, station_id: int) -> dict[str, Any]:
        result = self._request("POST", f"/api/admin/stations/{int(station_id)}/rebuild-m3u")
        if not isinstance(result, dict):
            raise ChannelDesignerError("Unexpected response when rebuilding M3U")
        return result

    def delete_artwork(self, station_id: int) -> dict[str, Any]:
        result = self._request("DELETE", f"/api/admin/stations/{int(station_id)}/artwork")
        if not isinstance(result, dict):
            raise ChannelDesignerError("Unexpected response when deleting artwork")
        return result

    def upload_artwork(self, station_id: int, filename: str, data: bytes, content_type: str) -> dict[str, Any]:
        boundary = uuid.uuid4().hex
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("utf-8")
        url = f"{self.base_url}/api/admin/stations/{int(station_id)}/artwork"
        headers = dict(self.headers)
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                result = json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ChannelDesignerError(
                _friendly_http_error(exc.code, detail), status=exc.code
            ) from exc
        except urllib.error.URLError as exc:
            raise ChannelDesignerError(
                f"Could not reach Alchemy FM at {self.base_url}: {exc.reason}"
            ) from exc
        if not isinstance(result, dict):
            raise ChannelDesignerError("Unexpected response when uploading artwork")
        return result

    def set_station_enabled(self, station_id: int, enabled: bool) -> dict[str, Any]:
        return self.update_station(int(station_id), {"enabled": bool(enabled)})

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
    custom = (get_setting("audiomuse_api_url") or "").strip().rstrip("/")
    if custom:
        return custom
    try:
        from plugin.api import config as plugin_config

        host = getattr(plugin_config, "AUDIOMUSE_CONTROL_HOST", "") or ""
        port = getattr(plugin_config, "AUDIOMUSE_CONTROL_PORT", "") or ""
        if host and port:
            return f"http://{host}:{port}".rstrip("/")
    except Exception:
        pass
    try:
        from flask import has_request_context

        if has_request_context():
            return request.host_url.rstrip("/")
    except Exception:
        pass
    return "http://127.0.0.1:8000"


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
    ("clap_query", "Sonic Vibe (CLAP Text Search)"),
    ("lyrics_query", "Lyrics Theme (Semantic)"),
    ("mood_centroid", "Mood Cluster"),
    ("alchemy_anchor", "Song Alchemy Anchor"),
    ("similar_seed", "Similar to Seed Track"),
)

PROGRAMMING_TYPE_LABELS = {key: label for key, label in PROGRAMMING_TYPES}
PROGRAMMING_TYPE_LABELS.update(
    {
        "clap_query": "Sonic Vibe (CLAP)",
        "lyrics_query": "Lyrics Theme",
        "mood_centroid": "Mood Cluster",
    }
)

REFRESH_MODES = (
    ("similar_to_last", "Similar to Last Played (Recommended)"),
    ("no_repeats", "No Repeats (Fresh Tracks First)"),
    ("similar_to_seed", "Similar to Programming Seed"),
    ("programming_only", "Programming Only (Re-Query Step 2)"),
    ("source_only", "Stay in Source Pool (Allow Repeats)"),
)

DIRECT_ALCHEMY_TYPES = frozenset({"alchemy_anchor", "similar_seed"})
LIVE_SOURCE_TYPES = frozenset(
    {"clap_query", "lyrics_query", "mood_centroid", "alchemy_anchor", "similar_seed"}
)
PREVIEW_LIMIT_DEFAULT = 30
ANCHOR_BLEND_TRACKS = 8
BOOTSTRAP_TRACK_LIMIT_DEFAULT = 30
CHAT_PLAYLIST_LIMIT = 60


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


def _merged_programming_tracks_unfiltered(
    profile: dict[str, Any], slug: str | None = None
) -> list[dict[str, Any]]:
    """Programming + living pool merge, enriched — before track filters."""
    raw = preview_programming(profile)
    slug = (slug or (profile.get("station") or {}).get("slug") or "").strip()
    living_cfg = profile.get("living") or {}
    if living_cfg.get("enabled") and slug:
        seen = {t["item_id"] for t in raw}
        pool_ids = [item_id for item_id in _pool_item_ids(slug) if item_id not in seen]
        if pool_ids:
            raw = raw + _preview_tracks_from_ids(pool_ids)
    return enrich_preview(raw)


def _merged_programming_tracks(profile: dict[str, Any], slug: str | None = None) -> list[dict[str, Any]]:
    """Programming preview merged with living pool tracks (deduped by item_id)."""
    return apply_track_filters(_merged_programming_tracks_unfiltered(profile, slug), profile)


def _programming_source_ref(programming: dict[str, Any]) -> str:
    ptype = programming["type"]
    if ptype in ("clap_query", "lyrics_query"):
        query = (programming.get("query") or "").strip()
        if len(query) < 3:
            raise ChannelDesignerError(f"{ptype} requires a query of at least 3 characters.")
        return query
    if ptype == "mood_centroid":
        mood = (programming.get("mood") or "").strip().lower()
        centroid_index = programming.get("centroid_index")
        if not mood or centroid_index is None:
            raise ChannelDesignerError("Mood cluster programming requires mood and cluster.")
        return f"{mood}:{int(centroid_index)}"
    if ptype == "alchemy_anchor":
        anchor_id = str(programming.get("anchor_id") or "").strip()
        if not anchor_id:
            raise ChannelDesignerError("Choose a Song Alchemy anchor.")
        return anchor_id
    if ptype == "similar_seed":
        seed_id = str(programming.get("seed_id") or "").strip()
        if not seed_id:
            raise ChannelDesignerError("Pick a seed track.")
        return seed_id
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
        row["year"] = score.get("year")
        row["top_genre"] = score.get("top_genre") or score.get("genre") or ""
        moods = score.get("mood_vector") or score.get("moods")
        if isinstance(moods, dict):
            top = sorted(moods.items(), key=lambda kv: kv[1], reverse=True)[:4]
            row["mood_tags"] = [name for name, _ in top]
            row["mood"] = ", ".join(name for name, _ in top[:2])
        else:
            row["mood_tags"] = []
            row["mood"] = ""
        enriched.append(row)
    return enriched


def _parse_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _parse_csv_list(value: Any) -> list[str]:
    if not value:
        return []
    return [part.strip().lower() for part in str(value).split(",") if part.strip()]


def _filters_from_form(form) -> dict[str, Any]:
    return {
        "tempo_min": _parse_optional_float(form.get("filter_tempo_min")),
        "tempo_max": _parse_optional_float(form.get("filter_tempo_max")),
        "energy_min": _parse_optional_float(form.get("filter_energy_min")),
        "energy_max": _parse_optional_float(form.get("filter_energy_max")),
        "year_min": _parse_optional_int(form.get("filter_year_min")),
        "year_max": _parse_optional_int(form.get("filter_year_max")),
        "genre_include": _parse_csv_list(form.get("filter_genre_include")),
        "genre_exclude": _parse_csv_list(form.get("filter_genre_exclude")),
        "mood_include": _parse_csv_list(form.get("filter_mood_include")),
        "exclude_artists": _parse_csv_list(form.get("filter_exclude_artists")),
    }


def _living_from_form(form) -> dict[str, Any]:
    return {
        "enabled": form.get("living_enabled") == "on",
        "auto_add_on_analyze": form.get("living_auto_add") == "on",
        "auto_refresh_alchemy": form.get("living_auto_refresh") == "on",
    }


def _filters_active(filters: dict[str, Any] | None) -> bool:
    if not filters:
        return False
    scalar_keys = ("tempo_min", "tempo_max", "energy_min", "energy_max", "year_min", "year_max")
    if any(filters.get(key) is not None for key in scalar_keys):
        return True
    list_keys = ("genre_include", "genre_exclude", "mood_include", "exclude_artists")
    return any(filters.get(key) for key in list_keys)


def _track_genre(track: dict[str, Any]) -> str:
    genre = track.get("top_genre") or track.get("genre") or ""
    return str(genre).strip().lower()


def _track_mood_tags(track: dict[str, Any]) -> list[str]:
    tags = track.get("mood_tags")
    if isinstance(tags, list) and tags:
        return [str(t).strip().lower() for t in tags if t]
    mood = track.get("mood") or ""
    if mood:
        return [part.strip().lower() for part in str(mood).split(",") if part.strip()]
    return []


def track_passes_filters(track: dict[str, Any], filters: dict[str, Any] | None) -> bool:
    if not _filters_active(filters):
        return True
    tempo = track.get("tempo")
    energy = track.get("energy")
    year = track.get("year")
    if tempo is None and energy is None:
        analysis = track.get("analysis") or {}
        tempo = analysis.get("tempo")
        energy = analysis.get("energy")
    if filters.get("tempo_min") is not None:
        if tempo is None or float(tempo) < float(filters["tempo_min"]):
            return False
    if filters.get("tempo_max") is not None:
        if tempo is None or float(tempo) > float(filters["tempo_max"]):
            return False
    if filters.get("energy_min") is not None:
        if energy is None or float(energy) < float(filters["energy_min"]):
            return False
    if filters.get("energy_max") is not None:
        if energy is None or float(energy) > float(filters["energy_max"]):
            return False
    if filters.get("year_min") is not None:
        if year is None or int(year) < int(filters["year_min"]):
            return False
    if filters.get("year_max") is not None:
        if year is None or int(year) > int(filters["year_max"]):
            return False
    genre = _track_genre(track)
    genre_include = filters.get("genre_include") or []
    if genre_include and genre not in genre_include:
        return False
    genre_exclude = filters.get("genre_exclude") or []
    if genre_exclude and genre in genre_exclude:
        return False
    mood_include = filters.get("mood_include") or []
    if mood_include:
        tags = _track_mood_tags(track)
        if not any(m in tags for m in mood_include):
            return False
    exclude_artists = filters.get("exclude_artists") or []
    if exclude_artists:
        artist = (track.get("author") or track.get("artist") or "").strip().lower()
        if artist in exclude_artists:
            return False
    return True


def apply_track_filters(tracks: list[dict[str, Any]], profile: dict[str, Any]) -> list[dict[str, Any]]:
    filters = profile.get("filters") or {}
    if not _filters_active(filters):
        return tracks
    return [track for track in tracks if track_passes_filters(track, filters)]


def _append_csv_term(current: str, term: str) -> str:
    term = term.strip()
    if not term:
        return current
    parts = [part.strip() for part in current.split(",") if part.strip()]
    if term.lower() not in {part.lower() for part in parts}:
        parts.append(term)
    return ", ".join(parts)


def _audiomuse_mood_labels() -> list[str]:
    labels: list[str] = []
    try:
        cfg = audiomuse_get("/api/config")
        if isinstance(cfg, dict):
            raw = cfg.get("mood_labels")
            if isinstance(raw, list):
                labels.extend(str(item).strip().lower() for item in raw if item)
            elif isinstance(raw, dict):
                labels.extend(str(key).strip().lower() for key in raw.keys())
    except ChannelDesignerError:
        pass
    if not labels:
        for mood in _mood_centroids_data().keys():
            labels.append(str(mood).strip().lower())
    return sorted({label for label in labels if label})


def _search_artists(query: str) -> list[str]:
    artists: list[str] = []
    seen: set[str] = set()
    for track in _search_tracks(query):
        artist = (track.get("author") or track.get("artist") or "").strip()
        key = artist.lower()
        if artist and key not in seen:
            seen.add(key)
            artists.append(artist)
    return artists[:15]


def _filter_feedback_report(
    unfiltered: list[dict[str, Any]],
    filtered: list[dict[str, Any]],
    filters: dict[str, Any] | None,
) -> dict[str, Any]:
    if not _filters_active(filters):
        return {}
    filters = filters or {}
    mood_vocab = set(_audiomuse_mood_labels())
    genre_counts: dict[str, int] = {}
    for track in unfiltered:
        genre = _track_genre(track)
        if genre:
            genre_counts[genre] = genre_counts.get(genre, 0) + 1
    terms: list[dict[str, Any]] = []
    for term in filters.get("genre_include") or []:
        count = sum(1 for track in unfiltered if _track_genre(track) == term)
        terms.append(
            {
                "kind": "Genre Include",
                "term": term,
                "count": count,
                "status": "ok" if count else "warn",
                "note": "exact Top Genre match",
            }
        )
    for term in filters.get("genre_exclude") or []:
        count = sum(1 for track in unfiltered if _track_genre(track) == term)
        terms.append(
            {
                "kind": "Genre Exclude",
                "term": term,
                "count": count,
                "status": "ok",
                "note": f"would remove {count}" if count else "not in preview pool",
            }
        )
    for term in filters.get("mood_include") or []:
        count = sum(1 for track in unfiltered if term in _track_mood_tags(track))
        known = term in mood_vocab
        status = "ok" if count else ("warn" if known else "unknown")
        note = "AudioMuse mood label" if known else "not a known AudioMuse mood"
        terms.append(
            {
                "kind": "Mood Include",
                "term": term,
                "count": count,
                "status": status,
                "note": note,
            }
        )
    for term in filters.get("exclude_artists") or []:
        count = sum(
            1
            for track in unfiltered
            if (track.get("author") or track.get("artist") or "").strip().lower() == term
        )
        terms.append(
            {
                "kind": "Exclude Artist",
                "term": term,
                "count": count,
                "status": "ok",
                "note": f"would remove {count}" if count else "not in preview pool",
            }
        )
    return {
        "before": len(unfiltered),
        "after": len(filtered),
        "terms": terms,
        "sample_genres": sorted(genre_counts.keys(), key=lambda g: (-genre_counts[g], g))[:10],
        "genre_counts": genre_counts,
    }


def _filter_feedback_html(report: dict[str, Any] | None) -> str:
    if not report:
        return ""
    before = int(report.get("before") or 0)
    after = int(report.get("after") or 0)
    removed = max(0, before - after)
    lines = [
        "<section class='afm-filter-feedback'>",
        "<h4 class='afm-filter-feedback-title'>Filter Check (Last Preview)</h4>",
        f"<p class='hint'>Pool before filters: <strong>{before}</strong> → after filters: "
        f"<strong>{after}</strong>"
        + (f" ({removed} removed)" if removed else "")
        + ". Genre and mood filters match <strong>analyzed</strong> score metadata exactly.</p>",
    ]
    terms = report.get("terms") or []
    if terms:
        lines.append("<ul class='afm-filter-feedback-list'>")
        for item in terms:
            term = html.escape(str(item.get("term") or ""))
            kind = html.escape(str(item.get("kind") or "Filter"))
            count = int(item.get("count") or 0)
            status = str(item.get("status") or "ok")
            note = html.escape(str(item.get("note") or ""))
            if status == "ok":
                marker = f"✓ {count} in pool"
                cls = "afm-filter-term-ok"
            elif status == "unknown":
                marker = "✗ unknown mood"
                cls = "afm-filter-term-warn"
            else:
                marker = f"✗ {count} in pool"
                cls = "afm-filter-term-warn"
            lines.append(
                f"<li class='{cls}'><strong>{kind}</strong> {term} — {marker}"
                + (f" <span class='afm-filter-term-note'>({note})</span>" if note else "")
                + "</li>"
            )
        lines.append("</ul>")
    sample_genres = report.get("sample_genres") or []
    genre_counts = report.get("genre_counts") or {}
    if sample_genres:
        parts = []
        for genre in sample_genres:
            parts.append(f"{html.escape(genre)} ({int(genre_counts.get(genre, 0))})")
        lines.append(
            "<p class='hint afm-filter-genre-hint'><strong>Top genres in preview pool:</strong> "
            + ", ".join(parts)
            + "</p>"
        )
    lines.append("</section>")
    return "".join(lines)


def _apply_filter_feedback(
    values: dict[str, Any],
    profile: dict[str, Any],
    unfiltered: list[dict[str, Any]],
    filtered: list[dict[str, Any]],
) -> None:
    report = _filter_feedback_report(unfiltered, filtered, profile.get("filters"))
    if report:
        values["filter_feedback"] = report


def _mood_datalist_html(labels: list[str]) -> str:
    if not labels:
        return ""
    options = "".join(f'<option value="{html.escape(label)}"></option>' for label in labels)
    return f"<datalist id='afm-mood-labels'>{options}</datalist>"


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


def _bootstrap_from_form(form) -> dict[str, Any] | None:
    if form.get("bootstrap_enabled") != "on":
        return None
    playlist_id = (form.get("bootstrap_playlist_id") or "").strip()
    if not playlist_id:
        return None
    limit = max(5, min(80, int(form.get("bootstrap_track_limit") or BOOTSTRAP_TRACK_LIMIT_DEFAULT)))
    return {
        "type": "navidrome_playlist",
        "playlist_id": playlist_id,
        "track_limit": limit,
    }


def _search_playlists(query: str) -> list[dict[str, Any]]:
    query = query.strip()
    if len(query) < 2:
        return []
    for params in ({"query": query}, {"search_query": query, "end": "20"}):
        try:
            data = audiomuse_get("/api/search_playlists", params={k: str(v) for k, v in params.items()})
        except ChannelDesignerError:
            continue
        if isinstance(data, list) and data:
            return [row for row in data if isinstance(row, dict) and row.get("id")]
    return []


def _verify_bootstrap_playlist(playlist_id: str, *, limit: int) -> dict[str, Any]:
    playlist_id = playlist_id.strip()
    if not playlist_id:
        return {"ok": False, "error": "Enter a Navidrome playlist id or pick one from search."}
    try:
        track_ids = _playlist_track_ids(playlist_id, limit=limit)
    except ChannelDesignerError as exc:
        return {"ok": False, "playlist_id": playlist_id, "error": str(exc)}
    if not track_ids:
        return {
            "ok": False,
            "playlist_id": playlist_id,
            "error": "Playlist not found or has no playable tracks in AudioMuse.",
        }
    return {
        "ok": True,
        "playlist_id": playlist_id,
        "resolved_count": len(track_ids),
        "limit": limit,
    }


def _bootstrap_feedback_html(check: dict[str, Any] | None) -> str:
    if not check:
        return ""
    if check.get("ok"):
        count = int(check.get("resolved_count") or 0)
        limit = int(check.get("limit") or 0)
        pid = html.escape(str(check.get("playlist_id") or ""))
        return (
            "<section class='afm-filter-feedback afm-bootstrap-feedback'>"
            "<h4 class='afm-filter-feedback-title'>Bootstrap Check</h4>"
            f"<p class='afm-filter-term-ok'>✓ Playlist <strong>{pid}</strong> resolves to "
            f"<strong>{count}</strong> opener track(s) (limit {limit}).</p>"
            "</section>"
        )
    error = html.escape(str(check.get("error") or "Could not verify playlist."))
    pid = html.escape(str(check.get("playlist_id") or ""))
    id_note = f" for <strong>{pid}</strong>" if pid else ""
    return (
        "<section class='afm-filter-feedback afm-bootstrap-feedback'>"
        "<h4 class='afm-filter-feedback-title'>Bootstrap Check</h4>"
        f"<p class='afm-filter-term-warn'>✗ {error}{id_note}</p>"
        "<p class='hint'>Search Navidrome playlists above and pick a result, or paste a playlist id from "
        "Navidrome/AudioMuse and click Verify.</p>"
        "</section>"
    )


def _apply_bootstrap_check(values: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any] | None:
    bootstrap = profile.get("bootstrap")
    if not bootstrap or not bootstrap.get("playlist_id"):
        return None
    limit = int(bootstrap.get("track_limit") or BOOTSTRAP_TRACK_LIMIT_DEFAULT)
    check = _verify_bootstrap_playlist(str(bootstrap["playlist_id"]), limit=limit)
    values["bootstrap_check"] = check
    return check


def _playlist_track_ids(playlist_id: str, *, limit: int) -> list[str]:
    playlist_id = playlist_id.strip()
    if not playlist_id:
        return []
    try:
        data = audiomuse_get("/api/playlist", params={"playlist_id": playlist_id})
    except ChannelDesignerError as exc:
        raise ChannelDesignerError(f"Could not load Navidrome playlist: {exc}") from exc
    tracks = data.get("tracks") if isinstance(data, dict) else data
    if not isinstance(tracks, list):
        return []
    ids: list[str] = []
    for row in tracks:
        if not isinstance(row, dict):
            continue
        item_id = row.get("item_id") or row.get("id")
        if item_id:
            ids.append(str(item_id))
        if len(ids) >= limit:
            break
    return ids


def _resolve_navidrome_bootstrap(bootstrap: dict[str, Any]) -> list[str]:
    playlist_id = str(bootstrap.get("playlist_id") or "").strip()
    if not playlist_id:
        return []
    limit = int(bootstrap.get("track_limit") or BOOTSTRAP_TRACK_LIMIT_DEFAULT)
    return _playlist_track_ids(playlist_id, limit=limit)


def _clustering_playlists() -> list[dict[str, Any]]:
    """AudioMuse GET /api/playlists returns {playlist_name: [tracks]} — not a list."""
    try:
        data = audiomuse_get("/api/playlists")
    except ChannelDesignerError:
        return []

    playlists: list[dict[str, Any]] = []
    if isinstance(data, dict):
        for name, tracks in data.items():
            playlist_name = str(name or "").strip()
            if not playlist_name:
                continue
            track_rows = tracks if isinstance(tracks, list) else []
            playlists.append(
                {
                    "id": playlist_name,
                    "name": playlist_name,
                    "playlist_id": playlist_name,
                    "track_count": len(track_rows),
                    "tracks": track_rows,
                }
            )
        return sorted(playlists, key=lambda row: str(row.get("name", "")).lower())

    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    return []


def _clustering_start() -> dict[str, Any]:
    return audiomuse_post("/api/clustering/start", {})


def _last_clustering_task() -> dict[str, Any] | None:
    try:
        data = audiomuse_get("/api/last_task")
    except ChannelDesignerError:
        return None
    if not isinstance(data, dict):
        return None
    task_type = str(data.get("task_type") or "")
    if task_type == "main_clustering" or "clustering" in task_type.lower():
        return data
    return None


def _chat_playlist_tracks(prompt: str, *, limit: int = CHAT_PLAYLIST_LIMIT) -> list[dict[str, Any]]:
    prompt = prompt.strip()
    if len(prompt) < 8:
        raise ChannelDesignerError("Describe your station in at least 8 characters.")
    data = audiomuse_post(
        "/chat/api/chatPlaylist",
        {"prompt": prompt, "limit": limit},
    )
    results = None
    if isinstance(data, dict):
        results = data.get("results") or data.get("tracks") or data.get("playlist")
    return _track_rows_from_results(results)


def channel_profile_to_alchemy_payload(profile: dict[str, Any], tracks: list[dict[str, Any]]) -> dict[str, Any]:
    station = profile["station"]
    refresh = profile.get("refresh") or {}
    programming = profile["programming"]
    ptype = programming["type"]

    if ptype not in LIVE_SOURCE_TYPES:
        raise ChannelDesignerError(f"Unsupported deploy programming type: {ptype}")

    source_type = ptype
    source_ref = _programming_source_ref(programming)
    identity_anchor_id = ""
    identity_seed_item_id = ""
    if ptype == "alchemy_anchor":
        identity_anchor_id = source_ref
    elif ptype == "similar_seed":
        identity_seed_item_id = source_ref
    elif tracks:
        identity_seed_item_id = tracks[len(tracks) // 2]["item_id"]

    deploy_profile = dict(profile)
    bootstrap = deploy_profile.get("bootstrap") or {}
    if bootstrap.get("type") == "navidrome_playlist" and bootstrap.get("playlist_id"):
        try:
            resolved = _resolve_navidrome_bootstrap(bootstrap)
            if resolved:
                bootstrap = dict(bootstrap)
                bootstrap["resolved_ids"] = resolved
                deploy_profile["bootstrap"] = bootstrap
        except ChannelDesignerError:
            pass

    return {
        "name": station["name"],
        "slug": station["slug"],
        "description": station.get("description", "")[:120],
        "icecast_mount": station["icecast_mount"],
        "enabled": bool(station.get("enabled", True)),
        "source_type": source_type,
        "source_ref": source_ref,
        "programming_json": json.dumps(deploy_profile),
        "queue_target": int(station.get("queue_target", 30)),
        "refresh_threshold": int(station.get("refresh_threshold", 10)),
        "artist_separation_minutes": int(station.get("artist_separation_minutes", 90)),
        "continuation_mode": refresh.get("mode", "similar_to_last"),
        "identity_anchor_id": identity_anchor_id,
        "identity_seed_item_id": identity_seed_item_id,
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
        "filters": _filters_from_form(form),
        "living": _living_from_form(form),
        "bootstrap": _bootstrap_from_form(form),
        "design_notes": (form.get("design_notes") or "").strip(),
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
    pool = table("channel_pool")
    cur.execute(
        "CREATE TABLE IF NOT EXISTS "
        + pool
        + " ("
        "id SERIAL PRIMARY KEY, "
        "channel_slug TEXT NOT NULL, "
        "item_id TEXT NOT NULL, "
        "source TEXT NOT NULL DEFAULT 'preview', "
        "added_at TEXT, "
        "UNIQUE(channel_slug, item_id)"
        ")"
    )
    auditions = table("auditions")
    cur.execute(
        "CREATE TABLE IF NOT EXISTS "
        + auditions
        + " ("
        "id SERIAL PRIMARY KEY, "
        "channel_slug TEXT NOT NULL, "
        "run_at TEXT NOT NULL, "
        "track_count INTEGER NOT NULL DEFAULT 0, "
        "track_ids_json TEXT NOT NULL DEFAULT '[]'"
        ")"
    )
    cur.execute(
        "INSERT INTO cron (name, task_type, cron_expr, enabled) VALUES (%s, %s, %s, FALSE) "
        "ON CONFLICT (task_type) DO NOTHING",
        (
            CRON_TASK_LABEL,
            CRON_TASK_TYPE,
            "0 3 * * *",
        ),
    )
    cur.execute(
        "UPDATE cron SET name=%s WHERE task_type=%s",
        (CRON_TASK_LABEL, CRON_TASK_TYPE),
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


def _local_channels_by_slug() -> dict[str, tuple]:
    rows = _channel_rows()
    return {slug: row for row in rows for slug in [row[1]]}


def _delete_local_channel(slug: str) -> None:
    slug = slug.strip()
    if not slug:
        return
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM " + table("channels") + " WHERE slug = %s", (slug,))
    db.commit()
    cur.close()


def _profile_from_remote_station(station: dict[str, Any]) -> dict[str, Any]:
    programming_json = station.get("programming_json") or ""
    if programming_json:
        try:
            profile = json.loads(programming_json)
            if isinstance(profile, dict) and profile.get("programming"):
                station_cfg = profile.get("station") or {}
                station_cfg.setdefault("name", station.get("name") or "")
                station_cfg.setdefault("slug", station.get("slug") or "")
                station_cfg.setdefault("description", station.get("description") or "")
                station_cfg.setdefault("icecast_mount", station.get("icecast_mount") or "")
                station_cfg.setdefault("enabled", bool(station.get("enabled", True)))
                station_cfg.setdefault("queue_target", int(station.get("queue_target") or 30))
                station_cfg.setdefault("refresh_threshold", int(station.get("refresh_threshold") or 10))
                station_cfg.setdefault(
                    "artist_separation_minutes", int(station.get("artist_separation_minutes") or 90)
                )
                profile["station"] = station_cfg
                profile.setdefault("refresh", {"mode": station.get("continuation_mode") or "similar_to_last"})
                return profile
        except json.JSONDecodeError:
            pass

    source_type = str(station.get("source_type") or "alchemy_anchor")
    source_ref = str(station.get("source_ref") or "")
    if source_type == "similar_seed":
        programming: dict[str, Any] = {
            "type": "similar_seed",
            "seed_id": source_ref,
            "limit": PREVIEW_LIMIT_DEFAULT,
        }
    elif source_type == "clap_query":
        programming = {"type": "clap_query", "query": source_ref, "limit": PREVIEW_LIMIT_DEFAULT}
    elif source_type == "lyrics_query":
        programming = {"type": "lyrics_query", "query": source_ref, "limit": PREVIEW_LIMIT_DEFAULT}
    elif source_type == "mood_centroid" and ":" in source_ref:
        mood, _, idx = source_ref.partition(":")
        programming = {
            "type": "mood_centroid",
            "mood": mood,
            "centroid_index": idx,
            "limit": PREVIEW_LIMIT_DEFAULT,
        }
    else:
        programming = {
            "type": "alchemy_anchor",
            "anchor_id": source_ref,
            "limit": PREVIEW_LIMIT_DEFAULT,
        }
    continuation = str(station.get("continuation_mode") or "similar_to_last")
    anchor_id = station.get("identity_anchor_id") or (
        source_ref if source_type == "alchemy_anchor" else None
    )
    return {
        "programming": programming,
        "refresh": {"mode": continuation},
        "filters": {},
        "living": {"enabled": False, "auto_add_on_analyze": False, "auto_refresh_alchemy": False},
        "station": {
            "name": station.get("name") or "",
            "slug": station.get("slug") or "",
            "description": station.get("description") or "",
            "icecast_mount": station.get("icecast_mount") or f"/{station.get('slug', 'station')}",
            "enabled": bool(station.get("enabled", True)),
            "bootstrap_queue": False,
            "queue_target": int(station.get("queue_target") or 30),
            "refresh_threshold": int(station.get("refresh_threshold") or 10),
            "artist_separation_minutes": int(station.get("artist_separation_minutes") or 90),
        },
        "anchor_id": anchor_id,
    }


def _apply_loaded_channel(
    slug: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    slug = slug.strip()
    station_remote: dict[str, Any] | None = None
    try:
        station_remote = _client().find_station_by_slug(slug)
    except ChannelDesignerError:
        station_remote = None

    loaded = _load_saved_channel(slug)
    if loaded:
        profile, preview_ids = loaded
        values = _form_values_from_profile(profile)
        values["editing_slug"] = slug
        values["profile_source"] = "saved"
        preview_tracks = _preview_tracks_from_ids(preview_ids)
        channel_name = profile.get("station", {}).get("name") or slug
    elif station_remote:
        profile = _profile_from_remote_station(station_remote)
        values = _form_values_from_profile(profile)
        values["editing_slug"] = slug
        values["profile_source"] = "remote"
        preview_tracks = []
        channel_name = profile.get("station", {}).get("name") or slug
    else:
        raise ChannelDesignerError(f"No channel found locally or on Alchemy FM for slug '{slug}'.")

    if station_remote:
        values["edit_station_id"] = int(station_remote.get("id") or 0)
        values["edit_on_air"] = bool(station_remote.get("enabled"))
        values["edit_queued"] = station_remote.get("queued_count", station_remote.get("queued", "?"))
        values["edit_description"] = station_remote.get("description") or values.get("description", "")
        values["edit_source_error"] = station_remote.get("source_last_error") or ""
        values["edit_source_healthy"] = bool(station_remote.get("source_healthy", True))
    if (profile.get("living") or {}).get("enabled"):
        values["edit_pool_count"] = _pool_count(slug)
    return values, preview_tracks, channel_name


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


def _record_audition(slug: str, item_ids: list[str]) -> None:
    slug = slug.strip()
    if not slug:
        return
    db = get_db()
    cur = db.cursor()
    auditions = table("auditions")
    cur.execute(
        "INSERT INTO "
        + auditions
        + " (channel_slug, run_at, track_count, track_ids_json) "
        "VALUES (%s, to_char(now(), 'YYYY-MM-DD HH24:MI:SS'), %s, %s)",
        (slug, len(item_ids), json.dumps(item_ids)),
    )
    cur.execute(
        "DELETE FROM "
        + auditions
        + " WHERE id NOT IN ("
        "SELECT id FROM "
        + auditions
        + " WHERE channel_slug = %s ORDER BY id DESC LIMIT 20"
        ")",
        (slug,),
    )
    db.commit()
    cur.close()


def _add_to_pool(slug: str, item_ids: list[str], *, source: str = "preview") -> int:
    slug = slug.strip()
    if not slug or not item_ids:
        return 0
    db = get_db()
    cur = db.cursor()
    pool = table("channel_pool")
    added = 0
    for item_id in item_ids:
        cur.execute(
            "INSERT INTO "
            + pool
            + " (channel_slug, item_id, source, added_at) "
            "VALUES (%s, %s, %s, to_char(now(), 'YYYY-MM-DD HH24:MI:SS')) "
            "ON CONFLICT (channel_slug, item_id) DO NOTHING",
            (slug, str(item_id), source),
        )
        if cur.rowcount:
            added += 1
    db.commit()
    cur.close()
    return added


def _pool_item_ids(slug: str, *, limit: int = 200) -> list[str]:
    slug = slug.strip()
    if not slug:
        return []
    db = get_db()
    cur = db.cursor()
    pool = table("channel_pool")
    try:
        cur.execute(
            "SELECT item_id FROM "
            + pool
            + " WHERE channel_slug = %s ORDER BY added_at DESC NULLS LAST, id DESC LIMIT %s",
            (slug, int(limit)),
        )
        return [str(row[0]) for row in cur.fetchall() if row and row[0]]
    except Exception:
        db.rollback()
        return []
    finally:
        cur.close()


def _pool_count(slug: str) -> int:
    slug = slug.strip()
    if not slug:
        return 0
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute(
            "SELECT COUNT(*) FROM " + table("channel_pool") + " WHERE channel_slug = %s",
            (slug,),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0
    except Exception:
        db.rollback()
        return 0
    finally:
        cur.close()


def _living_channel_profiles() -> list[tuple[str, dict[str, Any], int | None]]:
    rows = _channel_rows()
    living: list[tuple[str, dict[str, Any], int | None]] = []
    for row in rows:
        slug = str(row[1] or "")
        try:
            profile = json.loads(row[2] or "{}")
        except json.JSONDecodeError:
            continue
        living_cfg = profile.get("living") or {}
        if not living_cfg.get("enabled"):
            continue
        alchemy_id = row[4]
        living.append((slug, profile, int(alchemy_id) if alchemy_id else None))
    return living


def _audition_rows(slug: str | None = None, *, limit: int = 10) -> list[tuple]:
    db = get_db()
    cur = db.cursor()
    auditions = table("auditions")
    try:
        if slug:
            cur.execute(
                "SELECT channel_slug, run_at, track_count, track_ids_json FROM "
                + auditions
                + " WHERE channel_slug = %s ORDER BY id DESC LIMIT %s",
                (slug.strip(), int(limit)),
            )
        else:
            cur.execute(
                "SELECT channel_slug, run_at, track_count, track_ids_json FROM "
                + auditions
                + " ORDER BY id DESC LIMIT %s",
                (int(limit),),
            )
        return cur.fetchall()
    except Exception:
        db.rollback()
        return []
    finally:
        cur.close()


def on_song_analyzed(song: dict[str, Any]) -> None:
    item_id = str(song.get("item_id") or "").strip()
    if not item_id:
        return
    analysis = song.get("analysis") or {}
    track: dict[str, Any] = {
        "item_id": item_id,
        "tempo": analysis.get("tempo"),
        "energy": analysis.get("energy"),
        "author": song.get("author") or song.get("artist") or "",
    }
    try:
        scores = get_score_data_by_ids([item_id])
        if scores:
            score = scores[0]
            track["year"] = score.get("year")
            track["top_genre"] = score.get("top_genre") or score.get("genre") or ""
            moods = score.get("mood_vector") or score.get("moods")
            if isinstance(moods, dict):
                top = sorted(moods.items(), key=lambda kv: kv[1], reverse=True)[:4]
                track["mood_tags"] = [name for name, _ in top]
    except Exception:
        pass
    for slug, profile, _alchemy_id in _living_channel_profiles():
        living_cfg = profile.get("living") or {}
        if not living_cfg.get("auto_add_on_analyze"):
            continue
        if not track_passes_filters(track, profile.get("filters")):
            continue
        added = _add_to_pool(slug, [item_id], source="analyze")
        if added:
            logger.info(
                "alchemy_fm_bridge living pool +1 slug=%s item_id=%s",
                slug,
                item_id,
            )


def refresh_living_channels() -> None:
    channels = _living_channel_profiles()
    if not channels:
        logger.info("alchemy_fm_bridge refresh_living: no living channels configured")
        return

    client: AlchemyFmClient | None = None
    for slug, profile, alchemy_station_id in channels:
        try:
            preview_tracks = _merged_programming_tracks(profile, slug)
            item_ids = [t["item_id"] for t in preview_tracks]
            if not item_ids:
                logger.warning("alchemy_fm_bridge refresh_living: no tracks for slug=%s", slug)
                continue

            _record_audition(slug, item_ids)
            added = _add_to_pool(slug, item_ids, source="cron")
            _save_channel(profile, preview_ids=item_ids)

            living_cfg = profile.get("living") or {}
            if not living_cfg.get("auto_refresh_alchemy") or not alchemy_station_id:
                logger.info(
                    "alchemy_fm_bridge refresh_living slug=%s pool=%s added=%s",
                    slug,
                    len(item_ids),
                    added,
                )
                continue

            payload = channel_profile_to_alchemy_payload(profile, preview_tracks)
            if client is None:
                try:
                    client = _client()
                except ChannelDesignerError as exc:
                    logger.warning("alchemy_fm_bridge refresh_living: Alchemy FM unavailable: %s", exc)
                    continue

            update_fields = {
                key: value
                for key, value in payload.items()
                if key not in ("slug", "bootstrap_queue")
            }
            client.update_station(int(alchemy_station_id), update_fields)
            client.refresh_queue(int(alchemy_station_id))
            logger.info(
                "alchemy_fm_bridge refresh_living slug=%s updated station=%s pool=%s",
                slug,
                alchemy_station_id,
                _pool_count(slug),
            )
        except ChannelDesignerError as exc:
            _record_channel_error(slug, str(exc))
            logger.warning("alchemy_fm_bridge refresh_living slug=%s failed: %s", slug, exc)
        except Exception:
            logger.exception("alchemy_fm_bridge refresh_living slug=%s failed", slug)


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------


def _flash_html(message: str, level: str = "ok") -> str:
    level_class = "afm-flash-ok" if level == "ok" else "afm-flash-error"
    return f'<p class="afm-flash {level_class}">{html.escape(message)}</p>'


def _panel_heading(title: str, note: str = "") -> str:
    note_html = f'<p class="afm-panel-note">{html.escape(note)}</p>' if note else ""
    return (
        f'<div class="afm-panel-heading">'
        f'<h3 class="afm-panel-title">{html.escape(title)}</h3>'
        f"{note_html}"
        "</div>"
    )


def _step_panel_heading(
    step: str,
    title: str,
    purpose: str,
    *,
    optional: bool = False,
) -> str:
    badge = "Optional" if optional else step
    badge_class = "afm-step-badge-optional" if optional else "afm-step-badge"
    return (
        f'<div class="afm-step-header">'
        f'<span class="afm-step-badge {badge_class}">{html.escape(badge)}</span>'
        f'<div class="afm-step-copy">'
        f'<h3 class="afm-step-title">{html.escape(title)}</h3>'
        f'<p class="afm-step-purpose">{html.escape(purpose)}</p>'
        f"</div></div>"
    )


def _designer_flow_overview_html() -> str:
    return (
        '<section class="afm-panel afm-flow-overview-panel">'
        + _panel_heading(
            "How to Build a Station",
            "Follow the Steps Below — Only Step 2 Is Required Before Preview.",
        )
        + "<ol class='afm-flow-steps'>"
        "<li><strong>Name It</strong> — What listeners see (mount, homepage).</li>"
        "<li><strong>Program It</strong> — How AudioMuse finds music (CLAP, lyrics, mood, etc.). "
        "This is re-queried when the queue needs more tracks.</li>"
        "<li><strong>Preview It</strong> — Sanity-check tracks before anything goes on air.</li>"
        "<li><strong>Optional Extras</strong> — Filters, opener playlist, living pool (skip on first try).</li>"
        "<li><strong>Playback Rules</strong> — What happens when the pool runs low.</li>"
        "<li><strong>Deploy</strong> — Creates or updates the station on Alchemy FM.</li>"
        "</ol>"
        "<p class='hint'>Discover Channels (collapsed helper below) and Chat Designer prefills ideas only — they do not deploy by themselves.</p>"
        "</section>"
    )


def _page_header_html() -> str:
    return (
        '<header class="afm-page-header">'
        '<h2 class="afm-page-title">Live Programming for Alchemy FM</h2>'
        f'<a href="{html.escape(HELP_DOC_URL)}" class="afm-help-link" target="_blank" '
        'rel="noopener noreferrer">Help</a>'
        "</header>"
    )


def _field_label(text: str, *, mandatory: bool = False) -> str:
    suffix = " (Mandatory)" if mandatory else ""
    return f"<label>{html.escape(text)}{suffix}</label>"

def _page_styles() -> str:
    return """
<style>
.afm-shell {
  max-width: none;
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
  color: var(--text, inherit);
}
.afm-page-header {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 0.55rem;
  margin: 0 0 1.65rem;
  padding-bottom: 1.1rem;
  border-bottom: 1px solid var(--border, rgba(255, 255, 255, 0.1));
}
.afm-page-title {
  margin: 0;
  font-size: 1.55rem;
  font-weight: 700;
  letter-spacing: -0.03em;
  color: var(--text, inherit);
  text-transform: none;
}
.afm-help-link {
  font-size: 0.88rem;
  font-weight: 500;
  color: var(--muted, #94a3b8);
  text-decoration: none;
  border-bottom: 1px solid transparent;
  transition: color 0.15s ease, border-color 0.15s ease;
}
.afm-help-link:hover {
  color: var(--text, inherit);
  border-bottom-color: var(--border, rgba(255, 255, 255, 0.25));
}
.afm-flash {
  padding: 0.75rem 1rem;
  border-radius: 10px;
  margin: 0 0 1rem;
  line-height: 1.45;
}
.afm-flash-ok {
  background: color-mix(in srgb, #22c55e 14%, transparent);
  border: 1px solid color-mix(in srgb, #22c55e 45%, transparent);
  color: var(--text, #ecfdf5);
}
.afm-flash-error {
  background: color-mix(in srgb, #ef4444 14%, transparent);
  border: 1px solid color-mix(in srgb, #ef4444 45%, transparent);
  color: var(--text, #fef2f2);
}
.afm-section { margin: 0 0 1.75rem; min-width: 0; }
.afm-section-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 0.85rem;
  flex-wrap: wrap;
}
.afm-section-title {
  margin: 0;
  font-size: 1.05rem;
  font-weight: 600;
  letter-spacing: -0.02em;
  text-transform: none;
  color: var(--text, inherit);
}
.afm-section-note {
  margin: 0.35rem 0 0;
  color: var(--muted, #94a3b8);
  font-size: 0.92rem;
  line-height: 1.45;
}
.afm-table-wrap {
  overflow-x: auto;
  border: 1px solid var(--border, rgba(255, 255, 255, 0.12));
  border-radius: 12px;
  background: var(--field, rgba(255, 255, 255, 0.04));
  padding: 0.25rem 0.6rem 0.4rem 0.25rem;
  box-sizing: border-box;
}
.afm-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.92rem;
  min-width: 640px;
}
.afm-table th {
  text-align: left;
  padding: 0.65rem 0.85rem;
  font-size: 0.78rem;
  font-weight: 600;
  text-transform: none;
  letter-spacing: normal;
  color: var(--muted, #94a3b8);
  border-bottom: 1px solid var(--border, rgba(255, 255, 255, 0.12));
  white-space: nowrap;
}
.afm-table th.afm-actions-col,
.afm-table td.afm-actions-cell {
  padding-right: 1rem;
}
.afm-table td {
  padding: 0.75rem 0.85rem;
  border-bottom: 1px solid var(--border, rgba(255, 255, 255, 0.08));
  vertical-align: top;
  color: var(--text, inherit);
  line-height: 1.45;
  word-break: break-word;
}
.afm-table tbody tr:last-child td { border-bottom: none; }
.afm-table tbody tr.is-editing { background: color-mix(in srgb, var(--accent, #6366f1) 10%, transparent); }
.afm-table td.afm-status-cell { white-space: nowrap; }
.afm-table td.afm-actions-cell { white-space: nowrap; }
.afm-station-primary { font-weight: 600; color: var(--text, inherit); line-height: 1.35; }
.afm-station-meta { margin-top: 0.2rem; font-size: 0.84rem; color: var(--muted, #94a3b8); }
.afm-programming-type {
  display: block;
  font-size: 0.72rem;
  font-weight: 600;
  line-height: 1.4;
  color: var(--text, inherit);
}
.afm-badge-row { display: flex; flex-wrap: nowrap; gap: 0.35rem; align-items: center; }
.afm-table .afm-badge-row { gap: 0.28rem; }
.afm-table .afm-badge { font-size: 0.68rem; padding: 0.14rem 0.48rem; }
.afm-badge {
  display: inline-flex;
  align-items: center;
  padding: 0.18rem 0.55rem;
  border-radius: 999px;
  font-size: 0.72rem;
  font-weight: 600;
  line-height: 1.35;
  white-space: nowrap;
  border: 1px solid transparent;
}
.afm-badge-live {
  background: color-mix(in srgb, #22c55e 18%, transparent);
  color: #86efac;
  border-color: color-mix(in srgb, #22c55e 35%, transparent);
}
.afm-badge-off {
  background: color-mix(in srgb, var(--muted, #64748b) 16%, transparent);
  color: var(--muted, #cbd5e1);
  border-color: var(--border, rgba(255, 255, 255, 0.12));
}
.afm-badge-queue {
  background: color-mix(in srgb, #3b82f6 16%, transparent);
  color: #93c5fd;
  border-color: color-mix(in srgb, #3b82f6 30%, transparent);
}
.afm-badge-saved {
  background: color-mix(in srgb, #8b5cf6 16%, transparent);
  color: #c4b5fd;
  border-color: color-mix(in srgb, #8b5cf6 30%, transparent);
}
.afm-badge-remote {
  background: color-mix(in srgb, #f97316 14%, transparent);
  color: #fdba74;
  border-color: color-mix(in srgb, #f97316 28%, transparent);
}
.afm-badge-living {
  background: color-mix(in srgb, #06b6d4 14%, transparent);
  color: #67e8f9;
  border-color: color-mix(in srgb, #06b6d4 28%, transparent);
}
.afm-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.35rem;
  padding: 0.48rem 0.85rem;
  border-radius: 8px;
  border: 1px solid var(--border, rgba(255, 255, 255, 0.16));
  background: var(--field, rgba(255, 255, 255, 0.06));
  color: var(--text, inherit);
  font: inherit;
  font-weight: 500;
  cursor: pointer;
  text-decoration: none;
  line-height: 1.25;
  white-space: nowrap;
  text-transform: none;
  transition: background 0.15s ease, border-color 0.15s ease, box-shadow 0.15s ease, transform 0.12s ease, color 0.15s ease;
}
.afm-btn:hover {
  transform: translateY(-1px);
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.22);
}
.afm-btn:active { transform: translateY(0); box-shadow: none; }
.afm-btn-primary {
  background: var(--accent, #6366f1);
  border-color: var(--accent, #6366f1);
  color: #fff;
}
.afm-btn-primary:hover {
  background: color-mix(in srgb, var(--accent, #6366f1) 88%, #fff);
  border-color: color-mix(in srgb, var(--accent, #6366f1) 88%, #fff);
  box-shadow: 0 4px 14px color-mix(in srgb, var(--accent, #6366f1) 42%, transparent);
}
.afm-btn-secondary {
  background: var(--field, rgba(255, 255, 255, 0.06));
}
.afm-btn-secondary:hover {
  background: color-mix(in srgb, var(--accent, #6366f1) 14%, var(--field, rgba(255, 255, 255, 0.06)));
  border-color: color-mix(in srgb, var(--accent, #6366f1) 38%, var(--border, rgba(255, 255, 255, 0.16)));
  color: var(--text, inherit);
}
.afm-btn-danger {
  background: transparent;
  border-color: color-mix(in srgb, #ef4444 45%, transparent);
  color: #fca5a5;
}
.afm-btn-danger:hover {
  background: color-mix(in srgb, #ef4444 14%, transparent);
  border-color: color-mix(in srgb, #ef4444 65%, transparent);
  color: #fecaca;
  box-shadow: 0 2px 10px color-mix(in srgb, #ef4444 28%, transparent);
}
.afm-row-actions { display: flex; flex-wrap: nowrap; gap: 0.45rem; align-items: center; }
.afm-table .afm-row-actions { padding-right: 0.1rem; }
.afm-table .afm-btn:hover {
  transform: none;
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--accent, #6366f1) 35%, transparent);
}
.afm-table .afm-btn-danger:hover {
  box-shadow: 0 0 0 1px color-mix(in srgb, #ef4444 55%, transparent);
}
.afm-edit-bar {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  align-items: flex-start;
  flex-wrap: wrap;
  padding: 1rem 1.1rem;
  margin: 0 0 1rem;
  border: 1px solid var(--border, rgba(255, 255, 255, 0.12));
  border-radius: 12px;
  background: var(--field, rgba(255, 255, 255, 0.04));
  overflow: visible;
}
.afm-edit-eyebrow {
  display: block;
  font-size: 0.78rem;
  font-weight: 600;
  letter-spacing: normal;
  text-transform: none;
  color: var(--muted, #94a3b8);
  margin-bottom: 0.25rem;
}
.afm-edit-title {
  margin: 0;
  font-size: 1.25rem;
  line-height: 1.25;
  color: var(--text, inherit);
  word-break: break-word;
}
.afm-edit-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0.45rem;
  margin-top: 0.55rem;
  align-items: center;
}
.afm-edit-slug { color: var(--muted, #94a3b8); font-size: 0.88rem; }
.afm-edit-actions { display: flex; gap: 0.55rem; flex-wrap: wrap; align-items: center; }
.afm-panel {
  border: 1px solid var(--border, rgba(255, 255, 255, 0.12));
  border-radius: 12px;
  padding: 1.1rem 1.15rem 1.2rem;
  margin: 0 0 0.85rem;
  background: var(--field, rgba(255, 255, 255, 0.04));
  overflow: visible;
  min-width: 0;
}
.afm-panel-heading { margin: 0 0 0.85rem; }
.afm-panel-title {
  margin: 0;
  font-size: 0.88rem;
  font-weight: 600;
  text-transform: none;
  letter-spacing: normal;
  color: var(--muted, #94a3b8);
}
.afm-panel-note {
  margin: 0.3rem 0 0;
  color: var(--muted, #94a3b8);
  font-size: 0.88rem;
  line-height: 1.45;
}
.afm-step-panel { padding-top: 1rem; }
.afm-step-header {
  display: flex;
  gap: 0.85rem;
  align-items: flex-start;
  margin: 0 0 1rem;
  padding-bottom: 0.85rem;
  border-bottom: 1px solid var(--border, rgba(255, 255, 255, 0.1));
}
.afm-step-badge {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 4.5rem;
  padding: 0.28rem 0.55rem;
  border-radius: 999px;
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: #bfdbfe;
  background: color-mix(in srgb, #3b82f6 22%, transparent);
  border: 1px solid color-mix(in srgb, #3b82f6 35%, transparent);
}
.afm-step-badge-optional {
  color: #cbd5e1;
  background: color-mix(in srgb, #64748b 18%, transparent);
  border-color: color-mix(in srgb, #64748b 30%, transparent);
}
.afm-step-title {
  margin: 0;
  font-size: 1.02rem;
  font-weight: 650;
  letter-spacing: -0.01em;
  color: var(--text, inherit);
  text-transform: none;
}
.afm-step-purpose {
  margin: 0.35rem 0 0;
  color: var(--muted, #94a3b8);
  font-size: 0.9rem;
  line-height: 1.5;
}
.afm-flow-overview-panel { margin-bottom: 1rem; }
.afm-flow-steps {
  margin: 0.5rem 0 0.75rem;
  padding-left: 1.25rem;
  color: var(--text, inherit);
  font-size: 0.9rem;
  line-height: 1.55;
}
.afm-flow-steps li { margin: 0.35rem 0; }
.afm-helpers-panel {
  border-style: dashed;
  background: transparent;
}
.afm-collapsible-helper {
  border-style: dashed;
  background: transparent;
  padding: 0;
}
.afm-collapsible-helper > summary {
  list-style: none;
  cursor: pointer;
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  padding: 1rem 1.15rem;
}
.afm-collapsible-helper > summary::-webkit-details-marker { display: none; }
.afm-collapsible-helper > summary::before {
  content: "▸";
  flex: 0 0 auto;
  margin-top: 0.15rem;
  color: var(--muted, #94a3b8);
  transition: transform 0.15s ease;
}
.afm-collapsible-helper[open] > summary::before {
  transform: rotate(90deg);
}
.afm-collapsible-helper[open] > summary {
  border-bottom: 1px solid var(--border, rgba(255, 255, 255, 0.1));
}
.afm-collapsible-body {
  padding: 0.85rem 1.15rem 1.15rem;
}
.afm-helper-badge {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  padding: 0.22rem 0.5rem;
  border-radius: 999px;
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.03em;
  text-transform: uppercase;
  color: #cbd5e1;
  background: color-mix(in srgb, #64748b 18%, transparent);
  border: 1px solid color-mix(in srgb, #64748b 30%, transparent);
}
.afm-collapsible-title {
  display: block;
  font-size: 1rem;
  font-weight: 650;
  color: var(--text, inherit);
}
.afm-collapsible-hint {
  display: block;
  margin-top: 0.3rem;
  font-size: 0.88rem;
  line-height: 1.45;
  color: var(--muted, #94a3b8);
}
.afm-form-actions-inline {
  margin: 0;
  padding-top: 0.15rem;
}
.afm-filter-explainer {
  margin: 0;
  padding: 0;
  border: none;
  background: transparent;
  font-size: 0.88rem;
  line-height: 1.55;
  color: var(--muted, #94a3b8);
}
.afm-filter-explainer p { margin: 0.45rem 0; }
.afm-filter-explainer p:first-child { margin-top: 0; }
.afm-filter-explainer p:last-child { margin-bottom: 0; }
.afm-filter-explainer strong { color: var(--text, inherit); }
.afm-collapsible-explainer {
  margin: 0 0 1rem;
  border-radius: 10px;
  border: 1px solid var(--border, rgba(255, 255, 255, 0.12));
  background: color-mix(in srgb, var(--accent, #6366f1) 6%, transparent);
  overflow: hidden;
}
.afm-collapsible-explainer > summary {
  list-style: none;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 0.55rem;
  padding: 0.65rem 0.9rem;
  font-size: 0.88rem;
  font-weight: 650;
  color: var(--text, inherit);
}
.afm-collapsible-explainer > summary::-webkit-details-marker { display: none; }
.afm-collapsible-explainer > summary::before {
  content: "▸";
  flex: 0 0 auto;
  color: var(--muted, #94a3b8);
  transition: transform 0.15s ease;
}
.afm-collapsible-explainer[open] > summary::before {
  transform: rotate(90deg);
}
.afm-collapsible-explainer[open] > summary {
  border-bottom: 1px solid var(--border, rgba(255, 255, 255, 0.1));
}
.afm-collapsible-explainer-body {
  padding: 0.75rem 0.9rem 0.9rem;
}
.afm-preview-results-panel {
  scroll-margin-top: 1rem;
}
.afm-preview-results-panel > summary {
  list-style: none;
  cursor: pointer;
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 0.5rem 1rem;
  padding: 0.15rem 0 0.65rem;
  margin-bottom: 0.65rem;
  border-bottom: 1px solid var(--border, rgba(255, 255, 255, 0.1));
}
.afm-preview-results-panel > summary::-webkit-details-marker { display: none; }
.afm-preview-results-panel > summary::before {
  content: "▸";
  margin-right: 0.55rem;
  color: var(--muted, #94a3b8);
  transition: transform 0.15s ease;
}
.afm-preview-results-panel[open] > summary::before {
  transform: rotate(90deg);
}
.afm-preview-results-title {
  font-size: 0.88rem;
  font-weight: 600;
  color: var(--muted, #94a3b8);
}
.afm-preview-results-meta {
  font-size: 0.84rem;
  color: var(--muted, #94a3b8);
}
.afm-preview-results-panel.afm-preview-results-highlight {
  border-color: color-mix(in srgb, #3b82f6 45%, transparent);
  box-shadow: 0 0 0 1px color-mix(in srgb, #3b82f6 18%, transparent);
}
.afm-preview-results-body { padding-top: 0.15rem; }
.afm-panel label {
  display: block;
  font-size: 0.82rem;
  font-weight: 600;
  text-transform: none;
  letter-spacing: normal;
  color: var(--muted, #94a3b8);
  margin-bottom: 0.35rem;
}
.afm-panel label.afm-check-label {
  display: flex;
  font-size: 0.92rem;
  font-weight: 400;
  text-transform: none;
  letter-spacing: normal;
  color: var(--text, inherit);
  margin-bottom: 0;
}
.afm-panel input[type="text"],
.afm-panel input[type="number"],
.afm-panel input[type="password"],
.afm-panel input[type="search"],
.afm-panel textarea,
.afm-panel select,
.afm-panel .afm-text-input {
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
  color: var(--text, #e2e8f0);
  background: var(--field, rgba(15, 23, 42, 0.55));
  border: 1px solid var(--border, rgba(255, 255, 255, 0.14));
  border-radius: 8px;
  padding: 0.45rem 0.6rem;
}
.afm-select-wrap {
  position: relative;
  width: 100%;
}
.afm-select-native {
  position: absolute;
  left: 0;
  top: 0;
  width: 100%;
  height: 100%;
  opacity: 0;
  pointer-events: none;
  z-index: 1;
}
.afm-select-trigger {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.65rem;
  box-sizing: border-box;
  text-align: left;
  padding: 0.45rem 0.6rem;
  border-radius: 8px;
  border: 1px solid var(--border, rgba(255, 255, 255, 0.14));
  background: var(--field, rgba(15, 23, 42, 0.55));
  color: var(--text, #e2e8f0);
  font: inherit;
  line-height: 1.35;
  cursor: pointer;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.afm-select-trigger::after {
  content: "";
  flex: 0 0 auto;
  width: 0.45rem;
  height: 0.45rem;
  border-right: 2px solid currentColor;
  border-bottom: 2px solid currentColor;
  transform: rotate(45deg) translateY(-1px);
  opacity: 0.72;
}
.afm-select-wrap.is-open .afm-select-trigger,
.afm-select-trigger:focus-visible {
  border-color: color-mix(in srgb, var(--accent, #6366f1) 65%, var(--border, rgba(255, 255, 255, 0.14)));
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--accent, #6366f1) 40%, transparent);
  outline: none;
}
.afm-select-menu {
  position: absolute;
  z-index: 50;
  top: calc(100% + 0.35rem);
  left: 0;
  right: 0;
  max-height: 16rem;
  overflow-y: auto;
  margin: 0;
  padding: 0.35rem;
  list-style: none;
  border-radius: 8px;
  border: 1px solid var(--border, rgba(255, 255, 255, 0.14));
  background: var(--bg, #0f172a);
  box-shadow: 0 10px 28px rgba(0, 0, 0, 0.38);
}
.afm-select-menu[hidden] { display: none; }
.afm-select-option {
  display: block;
  width: 100%;
  box-sizing: border-box;
  text-align: left;
  padding: 0.52rem 0.65rem;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text, #e2e8f0);
  font: inherit;
  line-height: 1.35;
  cursor: pointer;
}
.afm-select-option:hover,
.afm-select-option.is-selected {
  background: color-mix(in srgb, var(--accent, #6366f1) 20%, transparent);
  color: var(--text, #f8fafc);
}
.afm-programming-panel .afm-field:has(.afm-text-input) {
  width: 100%;
  max-width: none;
}
.afm-programming-panel input.afm-text-input {
  display: block;
  width: 100% !important;
  max-width: 100% !important;
}
.afm-seed-search-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.65rem;
  align-items: center;
  margin-top: 0.5rem;
}
.afm-seed-search-row .afm-seed-search-input,
.afm-seed-search-row .afm-text-input {
  flex: 1 1 14rem;
  min-width: 0;
  width: auto !important;
  max-width: none !important;
  margin: 0;
}
.afm-seed-search-row .afm-seed-search-btn,
.afm-seed-search-row > .afm-btn {
  flex: 0 0 auto;
  margin: 0;
}
.afm-seed-field .afm-seed-search-row {
  margin-top: 0.35rem;
}
.afm-seed-field .afm-seed-search-input {
  flex: 1 1 16rem;
}
.afm-seed-field .afm-seed-search-btn {
  align-self: center;
}
.afm-seed-field .afm-seed-id-field {
  margin-top: 1.1rem;
}
.afm-seed-field .afm-seed-results {
  margin-top: 0.85rem;
}
.afm-panel .hint {
  margin: 0.55rem 0 0;
  color: var(--muted, #94a3b8);
  font-size: 0.84rem;
  line-height: 1.6;
}
.afm-panel .hint + .afm-check-group { margin-top: 1rem; }
.afm-deploy-tail {
  display: flex;
  flex-direction: column;
  gap: 1.15rem;
  margin-top: 0.2rem;
}
.afm-deploy-tail .afm-field-grid-3,
.afm-deploy-tail .afm-check-group,
.afm-deploy-tail .afm-form-actions {
  margin-top: 0;
}
.afm-check-group {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  margin-top: 0.85rem;
}
.afm-field { margin-top: 1rem; }
.afm-field:first-child { margin-top: 0; }
.afm-field-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.85rem;
  margin-top: 1rem;
}
.afm-field-grid-3 {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.85rem;
  margin-top: 1rem;
}
.afm-field-grid-3 label,
.afm-field-grid label {
  margin-bottom: 0.4rem;
}
.afm-form-actions {
  display: flex;
  gap: 0.65rem;
  flex-wrap: wrap;
  margin-top: 1.2rem;
  padding-top: 0.25rem;
}
.afm-deploy-tail .afm-form-actions {
  padding-top: 0;
  padding-bottom: 0.1rem;
}
.afm-designer-form { display: grid; gap: 0; min-width: 0; }
.afm-empty { color: var(--muted, #94a3b8); margin: 0; line-height: 1.45; }
.afm-panel input.is-readonly { opacity: 0.75; }
.afm-check-label {
  display: flex;
  gap: 0.55rem;
  align-items: flex-start;
  margin: 0;
  font-size: 0.92rem;
  line-height: 1.5;
  font-weight: 400;
  text-transform: none;
  letter-spacing: normal;
  color: var(--text, inherit);
}
.afm-check-label input { width: auto; margin-top: 0.15rem; }
.afm-filter-feedback {
  margin-top: 1rem;
  padding: 0.85rem 1rem;
  border-radius: 10px;
  border: 1px solid var(--border, rgba(255, 255, 255, 0.12));
  background: color-mix(in srgb, var(--accent, #6366f1) 8%, transparent);
}
.afm-filter-feedback-title {
  margin: 0 0 0.45rem;
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--text, inherit);
  text-transform: none;
  letter-spacing: normal;
}
.afm-filter-feedback-list {
  margin: 0.55rem 0 0;
  padding-left: 1.1rem;
  line-height: 1.5;
}
.afm-filter-feedback-list li { margin: 0.2rem 0; }
.afm-filter-term-ok { color: #86efac; }
.afm-filter-term-warn { color: #fdba74; }
.afm-filter-term-note { color: var(--muted, #94a3b8); font-size: 0.88rem; }
.afm-filter-genre-hint { margin: 0.65rem 0 0; }
.afm-mood-inline-hint {
  margin: 0.35rem 0 0;
  font-size: 0.84rem;
  color: #fdba74;
  min-height: 1.1rem;
}
.afm-bootstrap-panel .afm-panel-heading { margin-bottom: 0.6rem; }
.afm-bootstrap-panel .afm-panel-note { margin-top: 0.25rem; }
.afm-bootstrap-panel > .hint { margin: 0 0 0.9rem; }
.afm-bootstrap-panel .afm-check-group { margin: 0 0 1rem; }
.afm-bootstrap-panel .afm-field { margin-top: 0.85rem; }
.afm-bootstrap-panel .afm-field + .afm-field { margin-top: 1rem; }
.afm-seed-results {
  list-style: none;
  padding: 0;
  margin: 0.5rem 0 0;
  border: 1px solid var(--border, rgba(255, 255, 255, 0.12));
  border-radius: 8px;
  overflow: hidden;
}
.afm-seed-results button {
  width: 100%;
  text-align: left;
  padding: 0.55rem 0.65rem;
  border: none;
  border-bottom: 1px solid var(--border, rgba(255, 255, 255, 0.08));
  background: transparent;
  color: var(--text, inherit);
  cursor: pointer;
}
.afm-seed-results button:hover { background: color-mix(in srgb, var(--accent, #6366f1) 12%, transparent); }
.afm-seed-results li:last-child button { border-bottom: none; }
@media (max-width: 720px) {
  .afm-field-grid, .afm-field-grid-3 { grid-template-columns: 1fr; }
  .afm-edit-bar { padding: 0.9rem; }
  .afm-edit-actions { width: 100%; }
  .afm-table .afm-badge-row { flex-wrap: wrap; }
  .afm-row-actions { flex-wrap: wrap; }
}
</style>
"""


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
            f"<td>{html.escape(track['title'])}</td>"
            f"<td>{html.escape(track['author'])}</td>"
            f"<td>{html.escape(str(track.get('top_genre') or '—'))}</td>"
            f"<td>{tempo_s}</td>"
            f"<td>{energy_s}</td>"
            f"<td>{html.escape(track.get('mood') or '—')}</td>"
            "</tr>"
        )
    return (
        f"<p><strong>{len(tracks)}</strong> tracks in preview (from your AudioMuse library analysis).</p>"
        '<div class="afm-table-wrap"><table class="afm-table">'
        "<thead><tr>"
        "<th>Title</th><th>Artist</th><th>Genre</th><th>BPM</th><th>Energy</th><th>Mood</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def _preview_results_html(
    preview_tracks: list[dict[str, Any]],
    *,
    highlight: bool = False,
) -> str:
    count = len(preview_tracks)
    open_attr = " open" if count else ""
    highlight_class = " afm-preview-results-highlight" if highlight else ""
    if count:
        meta = f"{count} Track(s) From Your Last Step 3 Preview"
        body = _preview_table_html(preview_tracks)
    else:
        meta = "Run Preview Programming in Step 3 to See Tracks Here"
        body = "<p class='hint'>No preview yet. Set programming above, then click <strong>Preview Programming</strong>.</p>"
    return (
        f'<details id="preview-results" class="afm-panel afm-preview-results-panel{highlight_class}"{open_attr}>'
        "<summary>"
        '<span class="afm-preview-results-title">Preview Results</span>'
        f'<span class="afm-preview-results-meta">{html.escape(meta)}</span>'
        "</summary>"
        f'<div class="afm-preview-results-body">{body}</div>'
        "</details>"
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
        seed_results_html = "<ul class='afm-seed-results'>" + "".join(items) + "</ul>"

    anchor_opts = ['<option value="">Choose anchor…</option>']
    for anchor in _anchors():
        aid = str(anchor["id"])
        sel = " selected" if aid == str(values.get("anchor_id", "")) else ""
        anchor_opts.append(
            f'<option value="{html.escape(aid)}"{sel}>{html.escape(anchor.get("name") or aid)}</option>'
        )

    return (
        "<section class='afm-panel afm-programming-panel afm-step-panel'>"
        + _step_panel_heading(
            "Step 2",
            "Programming",
            "Defines what music fits this station. Alchemy FM re-runs this query when the queue needs more tracks.",
        )
        + "<div class='afm-field'>"
        + _field_label("Programming Type", mandatory=True)
        + f"<select name='programming_type' id='programming_type' class='afm-select'>"
        + f"{_select_options(PROGRAMMING_TYPES, ptype)}</select></div>"
        + f"<div id='field-clap' class='afm-field'{hidden('clap_query')}>"
        + _field_label("Sonic Vibe (Describe the Sound)", mandatory=True)
        + f"<input name='clap_query' class='afm-text-input' placeholder='e.g. late night rock' "
        + f"value='{html.escape(str(values.get('clap_query', '')))}'>"
        + "<p class='hint'>Matches how tracks <strong>sound</strong> — not lyrics. For theme or meaning, use "
        "<strong>Lyrics Theme</strong> instead of Sonic Vibe.</p></div>"
        + f"<div id='field-lyrics' class='afm-field'{hidden('lyrics_query')}>"
        + _field_label("Lyrics Theme", mandatory=True)
        + f"<input name='lyrics_query' class='afm-text-input' placeholder='e.g. songs about the open road' "
        + f"value='{html.escape(str(values.get('lyrics_query', '')))}'>"
        + "<p class='hint'>Semantic lyrics search — meaning and themes, not just keywords.</p></div>"
        + f"<div id='field-mood' class='afm-field afm-field-grid'{hidden('mood_centroid')}>"
        + "<div>"
        + _field_label("Mood", mandatory=True)
        + "<select name='mood_name' class='afm-select'>"
        + mood_opts
        + "</select></div>"
        + "<div>"
        + _field_label("Cluster", mandatory=True)
        + "<select name='centroid_index' id='centroid_index' class='afm-select'>"
        + centroid_opts
        + "</select></div>"
        + "<p class='hint' style='grid-column:1/-1;'>Each mood has sub-clusters from your library analysis — pick one that matches "
        + "the vibe (tags show the dominant traits in that cluster).</p></div>"
        + f"<div id='field-anchor' class='afm-field'{hidden('alchemy_anchor')}>"
        + _field_label("Song Alchemy Anchor", mandatory=True)
        + f"<select name='anchor_id' class='afm-select'>{''.join(anchor_opts)}</select></div>"
        + f"<div id='field-seed' class='afm-field afm-seed-field'{hidden('similar_seed')}>"
        + _field_label("Search Seed Track")
        + "<div class='afm-seed-search-row'>"
        + f"<input name='seed_search' class='afm-text-input afm-seed-search-input' "
        + f"placeholder='Title or artist…' value='{html.escape(str(values.get('seed_search', '')))}'>"
        + "<button type='submit' name='action' value='search_seed' formnovalidate "
        + "class='afm-btn afm-btn-secondary afm-seed-search-btn'>Search</button>"
        + "</div>"
        + "<p class='hint'>Search your library and pick a result, or enter a track item id below.</p>"
        + f"{seed_results_html}"
        + "<div class='afm-seed-id-field'>"
        + _field_label("Track Item ID", mandatory=True)
        + f"<input name='seed_id' class='afm-text-input' placeholder='Filled when you pick a search result' "
        + f"value='{html.escape(str(values.get('seed_id', '')))}'>"
        + "</div></div>"
        + "<div class='afm-field'>"
        + _field_label("Preview Size")
        + f"<input type='number' name='preview_limit' min='10' max='80' value='{html.escape(str(values.get('preview_limit', PREVIEW_LIMIT_DEFAULT)))}'>"
        + "</div></section>"
    )


def _collapsible_explainer_html(body: str) -> str:
    return (
        "<details class='afm-collapsible-explainer'>"
        "<summary>Explanation</summary>"
        f"<div class='afm-collapsible-explainer-body afm-filter-explainer'>{body}</div>"
        "</details>"
    )


def _filters_explainer_html() -> str:
    return _collapsible_explainer_html(
        "<p><strong>When filters run:</strong> In AudioMuse only — when you "
        "<strong>Preview Programming</strong> (Step 3). If <strong>Living Channel</strong> is on, "
        "they also apply when new analyzed songs join the pool or cron refreshes it.</p>"
        "<p><strong>When filters do not run:</strong> Alchemy FM on-air playback. "
        "When the queue runs low, refills use <strong>Step 2 Programming</strong> and "
        "<strong>Step 5 Playback Rules</strong> — not these filters.</p>"
        "<p><strong>First station?</strong> Leave filters empty until Preview Results look mostly right, "
        "then add rules to cut outliers (e.g. tempo range, block an artist).</p>"
        "<p><strong>After Preview:</strong> The <strong>Filter Check</strong> panel below the fields "
        "shows which genre/mood terms matched — use the <strong>Genre</strong> column in Preview Results "
        "to see exact spellings.</p>"
    )


def _playback_rules_explainer_html() -> str:
    return _collapsible_explainer_html(
        "<p><strong>When these rules run:</strong> Alchemy FM on-air playback only — when the queue "
        "drops below <strong>Refresh Below</strong>. They do not change Preview Programming or Living "
        "pool filters.</p>"
        "<p><strong>Refill order:</strong> Every refill first re-runs your <strong>Step 2 Programming</strong> "
        "query, then reuses unplayed tracks from the imported pool, then expands with similar/anchor tiers "
        "depending on the mode below.</p>"
        "<p><strong>Similar to Last Played:</strong> After the pool is exhausted, pulls tracks similar to "
        "whatever just played. The channel can drift over time — good default for variety.</p>"
        "<p><strong>No Repeats:</strong> Same expansion as Similar to Last Played, but never replays pool "
        "tracks that already aired. Best when you want fresh recommendations before any repeat.</p>"
        "<p><strong>Similar to Programming Seed:</strong> Stays near one fixed anchor track from the "
        "station identity — less drift than Similar to Last Played.</p>"
        "<p><strong>Programming Only:</strong> Re-queries Step 2 and uses the pool once — no similar-track "
        "expansion and no pool repeats. Stops when those are exhausted.</p>"
        "<p><strong>Stay in Source Pool:</strong> Reuses imported tracks and allows repeats before leaving "
        "the pool. No similar-track drift.</p>"
    )


def _filters_fields_html(values: dict[str, Any], *, mood_labels: list[str] | None = None) -> str:
    mood_labels = mood_labels if mood_labels is not None else _audiomuse_mood_labels()
    feedback = _filter_feedback_html(values.get("filter_feedback"))
    exclude_artist_results = values.get("exclude_artist_results") or []
    artist_results_html = ""
    if exclude_artist_results:
        items = []
        for artist in exclude_artist_results:
            items.append(
                "<li>"
                f'<button type="submit" name="pick_exclude_artist" value="{html.escape(artist)}" '
                'formnovalidate class="afm-btn afm-btn-secondary" style="width:100%;text-align:left;">'
                f"Add {html.escape(artist)}"
                "</button></li>"
            )
        artist_results_html = "<ul class='afm-seed-results'>" + "".join(items) + "</ul>"
    return (
        "<section class='afm-panel afm-step-panel'>"
        + _step_panel_heading(
            "Optional",
            "Filters",
            "Trims Preview and Living pool tracks. Does not control on-air refills when the queue runs low.",
            optional=True,
        )
        + _filters_explainer_html()
        + "<div class='afm-field-grid'>"
        "<div><label>Tempo Min (BPM)</label>"
        f"<input type='number' name='filter_tempo_min' min='0' step='1' "
        f"value='{html.escape(str(values.get('filter_tempo_min', '')))}' placeholder='any'></div>"
        "<div><label>Tempo Max (BPM)</label>"
        f"<input type='number' name='filter_tempo_max' min='0' step='1' "
        f"value='{html.escape(str(values.get('filter_tempo_max', '')))}' placeholder='any'></div>"
        "<div><label>Energy Min (0–1)</label>"
        f"<input type='number' name='filter_energy_min' min='0' max='1' step='0.01' "
        f"value='{html.escape(str(values.get('filter_energy_min', '')))}' placeholder='any'></div>"
        "<div><label>Energy Max (0–1)</label>"
        f"<input type='number' name='filter_energy_max' min='0' max='1' step='0.01' "
        f"value='{html.escape(str(values.get('filter_energy_max', '')))}' placeholder='any'></div>"
        "<div><label>Year Min</label>"
        f"<input type='number' name='filter_year_min' min='1900' max='2100' step='1' "
        f"value='{html.escape(str(values.get('filter_year_min', '')))}' placeholder='any'></div>"
        "<div><label>Year Max</label>"
        f"<input type='number' name='filter_year_max' min='1900' max='2100' step='1' "
        f"value='{html.escape(str(values.get('filter_year_max', '')))}' placeholder='any'></div>"
        "</div>"
        + "<p class='hint'>Tempo, energy, and year use analyzed score data. Leave blank for no limit.</p>"
        + "<div class='afm-field'>"
        + _field_label("Genre Include")
        + f"<input name='filter_genre_include' class='afm-text-input' placeholder='e.g. rock — exact Top Genre from Preview Results' "
        + f"value='{html.escape(str(values.get('filter_genre_include', '')))}'></div>"
        + "<div class='afm-field'>"
        + _field_label("Genre Exclude")
        + f"<input name='filter_genre_exclude' class='afm-text-input' placeholder='e.g. classical — exact Top Genre spelling' "
        + f"value='{html.escape(str(values.get('filter_genre_exclude', '')))}'></div>"
        + "<div class='afm-field'>"
        + _field_label("Mood Tags Include")
        + f"<input name='filter_mood_include' id='filter_mood_include' class='afm-text-input' "
        + f"list='afm-mood-labels' placeholder='melancholic, dreamy' "
        + f"value='{html.escape(str(values.get('filter_mood_include', '')))}'>"
        + "<p class='hint'>Choose from suggestions — each tag must match how AudioMuse labeled that track "
        "(not free-text lyrics or titles).</p>"
        + "<p id='afm-mood-inline-hint' class='afm-mood-inline-hint' aria-live='polite'></p></div>"
        + "<div class='afm-field'>"
        + _field_label("Exclude Artists")
        + f"<input name='filter_exclude_artists' id='filter_exclude_artists' class='afm-text-input' "
        + f"placeholder='comma-separated artist names' "
        + f"value='{html.escape(str(values.get('filter_exclude_artists', '')))}'>"
        + "<div class='afm-seed-search-row'>"
        + f"<input name='exclude_artist_search' class='afm-text-input afm-seed-search-input' "
        + f"placeholder='Search artist to add…' value='{html.escape(str(values.get('exclude_artist_search', '')))}'>"
        + "<button type='submit' name='action' value='search_exclude_artist' formnovalidate "
        + "class='afm-btn afm-btn-secondary afm-seed-search-btn'>Search</button>"
        + "</div>"
        + f"{artist_results_html}"
        + "<p class='hint'>Must match artist name in your library (search above to add). Case-insensitive.</p></div>"
        + _mood_datalist_html(mood_labels)
        + f"{feedback}"
        + "</section>"
    )


def _bootstrap_fields_html(values: dict[str, Any]) -> str:
    bootstrap_enabled = values.get("bootstrap_enabled", False)
    playlist_results = values.get("bootstrap_playlist_results") or []
    results_html = ""
    if playlist_results:
        items = []
        for pl in playlist_results:
            pl_id = str(pl.get("id") or "")
            name = pl.get("name") or pl_id or "Playlist"
            count = pl.get("count")
            count_s = f" · {count} tracks" if count is not None else ""
            items.append(
                "<li>"
                f'<button type="submit" name="pick_bootstrap_playlist" value="{html.escape(pl_id)}" '
                'formnovalidate class="afm-btn afm-btn-secondary" style="width:100%;text-align:left;">'
                f"{html.escape(name)}{html.escape(count_s)}"
                "</button></li>"
            )
        results_html = "<ul class='afm-seed-results'>" + "".join(items) + "</ul>"
    feedback = _bootstrap_feedback_html(values.get("bootstrap_check"))
    return (
        "<section class='afm-panel afm-bootstrap-panel afm-step-panel'>"
        + _step_panel_heading(
            "Optional",
            "Bootstrap Opener",
            "A Navidrome playlist that plays first at deploy only. After that, Step 2 programming takes over.",
            optional=True,
        )
        + "<p class='hint'>Search and pick a playlist, or paste an id and click Verify. Deploy blocks if verification fails.</p>"
        + "<div class='afm-check-group'>"
        + "<label class='afm-check-label'><input type='checkbox' name='bootstrap_enabled'"
        + f"{' checked' if bootstrap_enabled else ''}> Use Navidrome Playlist Opener</label>"
        + "</div>"
        + "<div class='afm-field'>"
        + _field_label("Search Navidrome Playlists")
        + "<div class='afm-seed-search-row'>"
        + f"<input name='bootstrap_playlist_search' class='afm-text-input afm-seed-search-input' "
        + f"placeholder='Playlist name…' value='{html.escape(str(values.get('bootstrap_playlist_search', '')))}'>"
        + "<button type='submit' name='action' value='search_bootstrap_playlist' formnovalidate "
        + "class='afm-btn afm-btn-secondary afm-seed-search-btn'>Search</button>"
        + "</div>"
        + f"{results_html}</div>"
        + "<div class='afm-field'>"
        + _field_label("Playlist ID")
        + "<div class='afm-seed-search-row'>"
        + f"<input name='bootstrap_playlist_id' id='bootstrap_playlist_id' class='afm-text-input' "
        + f"placeholder='From search or Navidrome' "
        + f"value='{html.escape(str(values.get('bootstrap_playlist_id', '')))}'>"
        + "<button type='submit' name='action' value='verify_bootstrap_playlist' formnovalidate "
        + "class='afm-btn afm-btn-secondary afm-seed-search-btn'>Verify</button>"
        + "</div></div>"
        + "<div class='afm-field'>"
        + _field_label("Opener Track Limit")
        + f"<input type='number' name='bootstrap_track_limit' min='5' max='80' "
        + f"value='{html.escape(str(values.get('bootstrap_track_limit', BOOTSTRAP_TRACK_LIMIT_DEFAULT)))}'>"
        + "</div>"
        + f"{feedback}"
        + "</section>"
    )


def _chat_designer_fields_html(values: dict[str, Any]) -> str:
    return (
        "<section class='afm-panel afm-helpers-panel'>"
        + _panel_heading(
            "Chat Designer",
            "Brainstorm Only — Does Not Deploy or Set Programming Until You Copy Ideas Into Step 2",
        )
        + "<p class='hint'>Slow LLM call. Use for initial ideas, then set programming above and preview.</p>"
        + "<div class='afm-field'>"
        + _field_label("Describe Your Station")
        + f"<textarea name='chat_prompt' rows='3' class='afm-text-input' "
        + f"placeholder='e.g. upbeat 80s synthpop for a morning commute'>{html.escape(str(values.get('chat_prompt', '')))}</textarea>"
        + "</div>"
        + "<button type='submit' name='action' value='chat_preview' formnovalidate "
        + "class='afm-btn afm-btn-secondary'>Generate Playlist Preview</button>"
        + f"<input type='hidden' name='design_notes' value='{html.escape(str(values.get('design_notes', '')))}'>"
        + "</section>"
    )


def _discover_channels_html() -> str:
    task = _last_clustering_task()
    task_note = ""
    if task:
        status = task.get("status") or task.get("state") or "unknown"
        task_note = f"<p class='hint'>Last Clustering Task: {html.escape(str(status))}</p>"
    playlists = _clustering_playlists()
    rows: list[str] = []
    for pl in playlists[:24]:
        pl_id = str(pl.get("id") or pl.get("playlist_id") or "")
        name = str(pl.get("name") or pl_id or "Cluster playlist")
        mood = str(pl.get("mood") or pl.get("description") or "")
        if not mood and pl.get("track_count"):
            mood = f"{int(pl['track_count'])} Tracks"
        deploy_query = name
        rows.append(
            "<tr>"
            f"<td>{html.escape(name)}</td>"
            f"<td>{html.escape(mood[:80])}</td>"
            f'<td><button type="submit" name="discover_deploy" value="{html.escape(pl_id)}" '
            'formnovalidate class="afm-btn afm-btn-secondary">'
            "Use in Designer</button></td>"
            f'<td><input type="hidden" name="discover_query_{html.escape(pl_id)}" '
            f'value="{html.escape(deploy_query)}"></td>'
            "</tr>"
        )
    table = (
        "<p class='hint'>No clustering playlists found. Run clustering in AudioMuse first.</p>"
        if not rows
        else (
            '<div class="afm-table-wrap"><table class="afm-table">'
            "<thead><tr><th>Playlist</th><th>Mood / Notes</th><th>Action</th><th></th></tr></thead>"
            "<tbody>"
            + "".join(rows)
            + "</tbody></table></div>"
        )
    )
    return (
        '<details class="afm-panel afm-collapsible-helper" id="discover">'
        "<summary>"
        '<span class="afm-helper-badge">Helper</span>'
        '<span><span class="afm-collapsible-title">Discover Channels</span>'
        '<span class="afm-collapsible-hint">Browse clustering playlists. '
        "<strong>Use in Designer</strong> prefills Step 2 with a <strong>Sonic Vibe (CLAP)</strong> query from the cluster name — "
        "it finds tracks that <em>sound</em> like that description, not songs with those words in the lyrics. "
        "It does not import cluster tracks or deploy.</span></span>"
        "</summary>"
        '<div class="afm-collapsible-body">'
        '<div class="afm-form-actions afm-form-actions-inline" style="margin-bottom:0.75rem;">'
        '<button type="submit" name="action" value="start_clustering" formnovalidate '
        'class="afm-btn afm-btn-secondary">Run Clustering</button>'
        "</div>"
        f"{task_note}{table}"
        "</div></details>"
    )


def _living_fields_html(values: dict[str, Any]) -> str:
    slug = (values.get("editing_slug") or values.get("slug") or "").strip()
    pool_note = ""
    if slug:
        pool_note = (
            f"<p class='hint'>Pool for <strong>{html.escape(slug)}</strong>: "
            f"{_pool_count(slug)} track(s) tracked for evolution.</p>"
        )
    living_enabled = values.get("living_enabled", False)
    auto_add = values.get("living_auto_add", living_enabled)
    auto_refresh = values.get("living_auto_refresh", living_enabled)
    return (
        "<section class='afm-panel afm-step-panel'>"
        + _step_panel_heading(
            "Optional",
            "Living Channel",
            "Let this station's track pool grow as new songs are analyzed. Requires Scheduled Tasks → Alchemy FM.",
            optional=True,
        )
        + "<p class='hint'>Auto-add puts new analyzed songs into the pool when they pass filters. "
        "Refresh re-runs programming and can push updates to Alchemy FM.</p>"
        f"{pool_note}"
        "<div class='afm-check-group'>"
        "<label class='afm-check-label'><input type='checkbox' name='living_enabled'"
        f"{' checked' if living_enabled else ''}> Enable Living Channel</label>"
        "<label class='afm-check-label'><input type='checkbox' name='living_auto_add'"
        f"{' checked' if auto_add else ''}> Auto-Add New Analyzed Songs That Pass Filters</label>"
        "<label class='afm-check-label'><input type='checkbox' name='living_auto_refresh'"
        f"{' checked' if auto_refresh else ''}> Refresh: Re-Score Pool and Push to Alchemy FM</label>"
        "</div>"
        "</section>"
    )


def _audition_history_html(slug: str | None = None) -> str:
    rows = _audition_rows(slug, limit=12)
    if not rows:
        hint = "Run a preview to record audition history."
        if slug:
            hint = f"No auditions recorded for {slug} yet. Preview programming to start."
        return f"<p class='hint'>{html.escape(hint)}</p>"

    body = (
        '<div class="afm-table-wrap"><table class="afm-table">'
        "<thead><tr><th>When</th><th>Channel</th><th>Tracks</th></tr></thead><tbody>"
    )
    for channel_slug, run_at, track_count, _track_ids_json in rows:
        body += (
            "<tr>"
            f"<td>{html.escape(str(run_at))}</td>"
            f"<td>{html.escape(str(channel_slug))}</td>"
            f"<td>{int(track_count)}</td>"
            "</tr>"
        )
    return body + "</tbody></table></div>"


def _station_identity_fields_html(values: dict[str, Any]) -> str:
    editing_slug = (values.get("editing_slug") or "").strip()
    slug_readonly = " readonly class='is-readonly'" if editing_slug else ""
    slug_extra = ""
    if editing_slug:
        slug_extra = (
            f"<input type='hidden' name='editing_slug' value='{html.escape(editing_slug)}'>"
            "<p class='hint'>Slug is fixed after first deploy.</p>"
        )
    return (
        "<section class='afm-panel afm-step-panel'>"
        + _step_panel_heading(
            "Step 1",
            "Station Identity",
            "What listeners see on Alchemy FM — name, URL mount, and homepage description. Does not affect track selection.",
        )
        + "<div class='afm-field'>"
        + _field_label("Channel Name", mandatory=True)
        + f"<input name='name' required value='{html.escape(str(values.get('name', '')))}'></div>"
        + "<div class='afm-field'>"
        + _field_label("Slug")
        + f"<input name='slug' placeholder='auto from name' "
        + f"value='{html.escape(str(values.get('slug', editing_slug or '')))}'{slug_readonly}>"
        + f"{slug_extra}</div>"
        + "<div class='afm-field'>"
        + _field_label("Description")
        + f"<textarea name='description' maxlength='120' rows='2' "
        + "placeholder='Short homepage blurb (max 120 characters)'>"
        + f"{html.escape(str(values.get('description', '')))}</textarea></div>"
        + "<div class='afm-field'>"
        + _field_label("Icecast Mount")
        + f"<input name='icecast_mount' placeholder='/channel-slug' value='{html.escape(str(values.get('icecast_mount', '')))}'>"
        + "<p class='hint'>Listen URL path, e.g. /yachtrock</p>"
        + "</div></section>"
    )


def _preview_step_html() -> str:
    return (
        "<section class='afm-panel afm-step-panel' id='step-preview'>"
        + _step_panel_heading(
            "Step 3",
            "Preview Programming",
            "Runs your Step 2 query in AudioMuse and shows matching tracks below. Nothing goes on air until Step 6 deploy.",
        )
        + "<div class='afm-form-actions afm-form-actions-inline'>"
        + "<button type='submit' name='action' value='preview' class='afm-btn afm-btn-primary'>"
        "Preview Programming</button>"
        + "</div></section>"
    )


def _playback_rules_fields_html(values: dict[str, Any]) -> str:
    return (
        "<section class='afm-panel afm-step-panel'>"
        + _step_panel_heading(
            "Step 5",
            "24/7 Playback Rules",
            "Controls how Alchemy FM refills the queue when tracks run low — not the initial vibe (that is Step 2).",
        )
        + _playback_rules_explainer_html()
        + "<div class='afm-field'>"
        + _field_label("When Pool Runs Low")
        + f"<select name='refresh_mode' class='afm-select'>{_select_options(REFRESH_MODES, str(values.get('refresh_mode', 'similar_to_last')))}</select>"
        + "</div>"
        + "<div class='afm-field-grid-3'>"
        + "<div><label>Queue Target</label>"
        + f"<input type='number' name='queue_target' min='5' max='200' value='{html.escape(str(values.get('queue_target', 30)))}'>"
        + "<p class='hint'>Tracks to keep queued</p></div>"
        + "<div><label>Refresh Below</label>"
        + f"<input type='number' name='refresh_threshold' min='1' max='100' value='{html.escape(str(values.get('refresh_threshold', 10)))}'>"
        + "<p class='hint'>Refill when queue drops under this</p></div>"
        + "<div><label>Artist Separation (Min)</label>"
        + f"<input type='number' name='artist_separation_minutes' min='0' value='{html.escape(str(values.get('artist_separation_minutes', 90)))}'>"
        + "<p class='hint'>Min minutes before same artist</p></div>"
        + "</div></section>"
    )


def _deploy_actions_fields_html(values: dict[str, Any]) -> str:
    editing_slug = (values.get("editing_slug") or "").strip()
    deploy_label = "Save Changes to Alchemy FM" if editing_slug else "Deploy to Alchemy FM"
    return (
        "<section class='afm-panel afm-step-panel afm-deploy-panel'>"
        + _step_panel_heading(
            "Step 6",
            "Deploy to Alchemy FM",
            "Creates or updates the station and optionally fills the play queue. Encoding and themes are in Alchemy FM Admin.",
        )
        + "<div class='afm-check-group'>"
        + "<label class='afm-check-label'><input type='checkbox' name='enabled'"
        + f"{' checked' if values.get('enabled', True) else ''}> Start On Air After Push</label>"
        + "<label class='afm-check-label'><input type='checkbox' name='bootstrap_queue'"
        + f"{' checked' if values.get('bootstrap_queue', True) else ''}> Bootstrap Queue Immediately</label>"
        + "</div>"
        + f"<input type='hidden' name='saved_anchor_id' value='{html.escape(str(values.get('saved_anchor_id', '')))}'>"
        + "<div class='afm-form-actions'>"
        + f"<button type='submit' name='action' value='push' class='afm-btn afm-btn-primary'>{html.escape(deploy_label)}</button>"
        + "<button type='submit' name='action' value='test' formnovalidate class='afm-btn afm-btn-secondary'>Test Connection</button>"
        + "</div></section>"
    )


def _programming_detail(profile: dict[str, Any] | None, station: dict[str, Any]) -> tuple[str, str, bool, bool]:
    has_saved = profile is not None
    living = bool((profile or {}).get("living", {}).get("enabled"))
    if profile:
        programming = profile.get("programming") or {}
        ptype = programming.get("type", "?")
        type_label = PROGRAMMING_TYPE_LABELS.get(ptype, ptype.replace("_", " ").title())
        detail = (
            programming.get("query")
            or programming.get("seed_id")
            or programming.get("anchor_id")
            or programming.get("mood")
            or ""
        )
        if ptype == "mood_centroid":
            mood = programming.get("mood") or ""
            cluster = programming.get("centroid_index")
            detail = f"{mood} · cluster {cluster}" if cluster is not None else mood
        return type_label, str(detail), living, has_saved
    source_type = str(station.get("source_type") or "?")
    source_ref = str(station.get("source_ref") or "")
    type_label = PROGRAMMING_TYPE_LABELS.get(source_type, source_type.replace("_", " ").title())
    return type_label, source_ref, False, False


def _programming_label(profile: dict[str, Any] | None, station: dict[str, Any]) -> str:
    type_label, detail, _, _ = _programming_detail(profile, station)
    return f"{type_label}: {detail}"[:72]


def _edit_toolbar_html(values: dict[str, Any]) -> str:
    editing_slug = (values.get("editing_slug") or "").strip()
    if not editing_slug:
        return ""
    name = (values.get("name") or editing_slug).strip()
    source = values.get("profile_source") or "remote"
    source_badge = (
        '<span class="afm-badge afm-badge-saved">Saved Design</span>'
        if source == "saved"
        else '<span class="afm-badge afm-badge-remote">Alchemy FM Only</span>'
    )
    on_air_badge = (
        '<span class="afm-badge afm-badge-live">On Air</span>'
        if values.get("edit_on_air")
        else '<span class="afm-badge afm-badge-off">Off Air</span>'
    )
    queued = values.get("edit_queued", "?")
    extra_badges = ""
    if values.get("living_enabled") or values.get("edit_pool_count"):
        pool_count = values.get("edit_pool_count", _pool_count(editing_slug))
        extra_badges = f'<span class="afm-badge afm-badge-living">Living · {pool_count} in pool</span>'
    source_error = (values.get("edit_source_error") or "").strip()
    error_badge = ""
    if source_error:
        short = source_error[:60] + ("…" if len(source_error) > 60 else "")
        error_badge = (
            f'<span class="afm-badge afm-badge-remote" title="{html.escape(source_error)}">'
            f"Source error: {html.escape(short)}</span>"
        )
    station_id = values.get("edit_station_id")
    id_note = f" · id {station_id}" if station_id else ""
    op_buttons = ""
    if station_id:
        on_label = "Take Off Air" if values.get("edit_on_air") else "Put On Air"
        op_buttons = (
            f'<form method="post" style="margin:0;display:inline;">'
            f'<input type="hidden" name="editing_slug" value="{html.escape(editing_slug)}">'
            f'<input type="hidden" name="op_station_id" value="{int(station_id)}">'
            '<button type="submit" name="action" value="op_refresh_queue" formnovalidate '
            'class="afm-btn afm-btn-secondary">Refresh Queue</button>'
            '<button type="submit" name="action" value="op_bootstrap" formnovalidate '
            'class="afm-btn afm-btn-secondary">Rebuild Pool</button>'
            '<button type="submit" name="action" value="op_rebuild_m3u" formnovalidate '
            'class="afm-btn afm-btn-secondary">Rebuild M3U</button>'
            f'<button type="submit" name="action" value="op_toggle_enabled" formnovalidate '
            f'class="afm-btn afm-btn-secondary">{html.escape(on_label)}</button>'
            "</form>"
            f'<form method="post" enctype="multipart/form-data" style="margin:0;display:inline;">'
            f'<input type="hidden" name="editing_slug" value="{html.escape(editing_slug)}">'
            f'<input type="hidden" name="op_station_id" value="{int(station_id)}">'
            '<input type="file" name="artwork_file" accept="image/*" style="max-width:10rem;">'
            '<button type="submit" name="action" value="op_upload_artwork" formnovalidate '
            'class="afm-btn afm-btn-secondary">Upload Art</button>'
            '<button type="submit" name="action" value="op_delete_artwork" formnovalidate '
            'class="afm-btn afm-btn-secondary">Remove Art</button>'
            "</form>"
        )
    return (
        f'<div class="afm-edit-bar" id="designer">'
        "<div>"
        '<span class="afm-edit-eyebrow">Editing Station</span>'
        f'<h2 class="afm-edit-title">{html.escape(name)}</h2>'
        '<div class="afm-edit-meta">'
        f'<span class="afm-edit-slug">{html.escape(editing_slug)}{html.escape(id_note)}</span>'
        f"{source_badge}{on_air_badge}{error_badge}"
        f'<span class="afm-badge afm-badge-queue">{html.escape(str(queued))} Queued</span>'
        f"{extra_badges}"
        "</div>"
        "</div>"
        '<div class="afm-edit-actions">'
        f"{op_buttons}"
        f'<a href="{html.escape(url_for("alchemy_fm_bridge.home"))}" class="afm-btn afm-btn-secondary">← All Stations</a>'
        '<button type="submit" form="afm-designer-form" name="action" value="new_channel" formnovalidate '
        'class="afm-btn afm-btn-secondary">New Channel</button>'
        '<button type="submit" form="afm-designer-form" name="action" value="push" '
        'class="afm-btn afm-btn-primary">Save Changes</button>'
        "</div></div>"
    )


def _stations_section_html(editing_slug: str | None = None) -> str:
    try:
        stations = _client().test_connection()
    except ChannelDesignerError as exc:
        return (
            '<section class="afm-section">'
            f'<p class="afm-empty">Could not load Alchemy FM stations: {html.escape(str(exc))}</p>'
            "</section>"
        )

    editing_slug = (editing_slug or "").strip().lower()
    local = _local_channels_by_slug()
    if not stations:
        table_html = (
            '<p class="afm-empty">No stations yet. Use the designer below to create your first channel.</p>'
        )
    else:
        rows: list[str] = []
        for station in sorted(stations, key=lambda s: str(s.get("name") or s.get("slug") or "")):
            slug = str(station.get("slug") or "")
            slug_key = slug.lower()
            name = str(station.get("name") or slug or "Station")
            station_id = int(station.get("id") or 0)
            queued = str(station.get("queued_count", station.get("queued", "?")))
            profile = None
            if slug in local:
                try:
                    profile = json.loads(local[slug][2] or "{}")
                except json.JSONDecodeError:
                    profile = None
            type_label, _detail, living, has_saved = _programming_detail(profile, station)
            is_editing = slug_key == editing_slug
            row_class = ' class="is-editing"' if is_editing else ""
            on_air_badge = (
                '<span class="afm-badge afm-badge-live">On Air</span>'
                if station.get("enabled")
                else '<span class="afm-badge afm-badge-off">Off Air</span>'
            )
            profile_badge = (
                '<span class="afm-badge afm-badge-saved">Saved Design</span>'
                if has_saved
                else '<span class="afm-badge afm-badge-remote">Remote Only</span>'
            )
            living_badge = '<span class="afm-badge afm-badge-living">Living</span>' if living else ""
            pool_badge = ""
            if living and slug:
                pool_badge = f'<span class="afm-badge afm-badge-living">{_pool_count(slug)} pool</span>'
            source_error = str(station.get("source_last_error") or "").strip()
            error_badge = ""
            if source_error:
                short = source_error[:40] + ("…" if len(source_error) > 40 else "")
                error_badge = (
                    f'<span class="afm-badge afm-badge-remote" title="{html.escape(source_error)}">'
                    f"Err: {html.escape(short)}</span>"
                )
            edit_href = html.escape(url_for("alchemy_fm_bridge.home", edit=slug) + "#designer")
            edit_label = "Continue Editing" if is_editing else "Edit"
            edit_btn_class = "afm-btn afm-btn-primary" if is_editing else "afm-btn afm-btn-secondary"
            confirm_msg = f"Delete station {name} ({slug})? This removes it from Alchemy FM permanently."
            rows.append(
                f"<tr{row_class}>"
                f"<td><div class='afm-station-primary'>{html.escape(name)}</div>"
                f"<div class='afm-station-meta'>{html.escape(slug)}</div></td>"
                f"<td><span class='afm-programming-type'>{html.escape(type_label)}</span></td>"
                f'<td class="afm-status-cell"><div class="afm-badge-row">{on_air_badge}{profile_badge}'
                f'<span class="afm-badge afm-badge-queue">{html.escape(queued)} Queued</span>'
                f"{living_badge}{pool_badge}{error_badge}</div></td>"
                f'<td class="afm-actions-cell"><div class="afm-row-actions">'
                f'<a href="{edit_href}" class="{edit_btn_class}">{html.escape(edit_label)}</a>'
                f'<form method="post" style="margin:0;" onsubmit="return confirm({json.dumps(confirm_msg)});">'
                f'<input type="hidden" name="action" value="delete">'
                f'<input type="hidden" name="station_id" value="{station_id}">'
                f'<input type="hidden" name="delete_slug" value="{html.escape(slug)}">'
                '<button type="submit" class="afm-btn afm-btn-danger">Delete</button>'
                "</form></div></td>"
                "</tr>"
            )
        table_html = (
            '<div class="afm-table-wrap"><table class="afm-table">'
            "<thead><tr>"
            "<th>Station</th><th>Programming</th><th>Status</th><th class='afm-actions-col'>Actions</th>"
            "</tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table></div>"
        )

    title = "Other Stations" if editing_slug else "Your Stations"
    note = (
        "Switch Stations Without Losing Your Place — Each Opens in the Designer Above."
        if editing_slug
        else "Pick a Station to Edit, or Create a New Channel Below."
    )
    new_channel = ""
    if not editing_slug:
        new_channel = (
            f'<a href="{html.escape(url_for("alchemy_fm_bridge.home", new="1") + "#designer")}" '
            'class="afm-btn afm-btn-primary">+ New Channel</a>'
        )
    return (
        f'<section class="afm-section" id="stations">'
        '<div class="afm-section-head">'
        f"<div><h2 class='afm-section-title'>{html.escape(title)}</h2>"
        f"<p class='afm-section-note'>{html.escape(note)}</p></div>"
        f"{new_channel}"
        "</div>"
        f"{table_html}"
        "</section>"
    )


def _channels_table_html() -> str:
    return _stations_section_html()


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
    filters = profile.get("filters") or {}
    for key in (
        "tempo_min",
        "tempo_max",
        "energy_min",
        "energy_max",
        "year_min",
        "year_max",
        "genre_include",
        "genre_exclude",
        "mood_include",
        "exclude_artists",
    ):
        val = filters.get(key)
        if val is not None and val != "" and val != []:
            if isinstance(val, list):
                values[f"filter_{key}"] = ", ".join(val)
            else:
                values[f"filter_{key}"] = val
    bootstrap = profile.get("bootstrap") or {}
    if bootstrap.get("type") == "navidrome_playlist":
        values["bootstrap_enabled"] = True
        values["bootstrap_playlist_id"] = bootstrap.get("playlist_id", "")
        values["bootstrap_track_limit"] = bootstrap.get("track_limit", BOOTSTRAP_TRACK_LIMIT_DEFAULT)
    values["design_notes"] = profile.get("design_notes") or ""
    values["chat_prompt"] = profile.get("design_notes") or values.get("chat_prompt", "")
    living = profile.get("living") or {}
    values["living_enabled"] = bool(living.get("enabled"))
    values["living_auto_add"] = bool(living.get("auto_add_on_analyze", living.get("enabled")))
    values["living_auto_refresh"] = bool(living.get("auto_refresh_alchemy", living.get("enabled")))
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


def _page_script(
    mood_centroids: dict[str, Any] | None = None,
    mood_labels: list[str] | None = None,
    *,
    scroll_to_preview: bool = False,
) -> str:
    mood_json = json.dumps(mood_centroids or {})
    mood_labels_json = json.dumps(mood_labels or [])
    scroll_flag = "true" if scroll_to_preview else "false"
    return f"""
<script type="application/json" id="mood-centroids-data">{mood_json}</script>
<script type="application/json" id="mood-labels-data">{mood_labels_json}</script>
<script>
(function() {{
  const typeSelect = document.getElementById('programming_type');
  const moodDataEl = document.getElementById('mood-centroids-data');
  const moodLabelsEl = document.getElementById('mood-labels-data');
  const moodData = moodDataEl ? JSON.parse(moodDataEl.textContent || '{{}}') : {{}};
  const moodLabels = moodLabelsEl ? JSON.parse(moodLabelsEl.textContent || '[]') : [];
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

  function closeAllSelectMenus(exceptMenu) {{
    document.querySelectorAll('.afm-select-wrap.is-open').forEach((wrap) => {{
      const menu = wrap.querySelector('.afm-select-menu');
      if (menu && menu !== exceptMenu) {{
        menu.hidden = true;
        wrap.classList.remove('is-open');
      }}
    }});
  }}

  function syncAfmSelect(select) {{
    const wrap = select.closest('.afm-select-wrap');
    if (!wrap) return;
    const trigger = wrap.querySelector('.afm-select-trigger');
    const menu = wrap.querySelector('.afm-select-menu');
    if (!trigger || !menu) return;
    const selected = select.options[select.selectedIndex];
    trigger.textContent = selected ? selected.textContent : 'Choose…';
    menu.querySelectorAll('.afm-select-option').forEach((btn) => {{
      const on = btn.dataset.value === select.value;
      btn.classList.toggle('is-selected', on);
      btn.setAttribute('aria-selected', on ? 'true' : 'false');
    }});
  }}

  function buildAfmSelectMenu(select) {{
    const wrap = select.closest('.afm-select-wrap');
    if (!wrap) return;
    const menu = wrap.querySelector('.afm-select-menu');
    if (!menu) return;
    menu.innerHTML = '';
    Array.from(select.options).forEach((opt) => {{
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'afm-select-option';
      btn.setAttribute('role', 'option');
      btn.dataset.value = opt.value;
      btn.textContent = opt.textContent;
      if (opt.selected) btn.classList.add('is-selected');
      btn.addEventListener('click', () => {{
        select.value = opt.value;
        select.dispatchEvent(new Event('change', {{ bubbles: true }}));
        menu.hidden = true;
        wrap.classList.remove('is-open');
        syncAfmSelect(select);
      }});
      menu.appendChild(btn);
    }});
    syncAfmSelect(select);
  }}

  function enhanceAfmSelect(select) {{
    if (!select || select.dataset.afmEnhanced === '1') return;
    select.dataset.afmEnhanced = '1';
    select.classList.add('afm-select-native');

    const wrap = document.createElement('div');
    wrap.className = 'afm-select-wrap';
    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(select);

    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'afm-select-trigger';
    trigger.setAttribute('aria-haspopup', 'listbox');

    const menu = document.createElement('div');
    menu.className = 'afm-select-menu';
    menu.setAttribute('role', 'listbox');
    menu.hidden = true;

    trigger.addEventListener('click', () => {{
      if (menu.hidden) {{
        buildAfmSelectMenu(select);
        closeAllSelectMenus(menu);
        menu.hidden = false;
        wrap.classList.add('is-open');
      }} else {{
        menu.hidden = true;
        wrap.classList.remove('is-open');
      }}
    }});

    select.addEventListener('change', () => syncAfmSelect(select));
    new MutationObserver(() => buildAfmSelectMenu(select)).observe(select, {{
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['selected', 'value'],
    }});

    wrap.appendChild(trigger);
    wrap.appendChild(menu);
    buildAfmSelectMenu(select);
  }}

  document.querySelectorAll('.afm-panel select.afm-select').forEach(enhanceAfmSelect);
  document.addEventListener('click', (event) => {{
    if (!event.target.closest('.afm-select-wrap')) closeAllSelectMenus(null);
  }});

  if (location.hash === '#designer') {{
    const target = document.getElementById('designer');
    if (target) target.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
  }}

  const moodInput = document.getElementById('filter_mood_include');
  const moodHint = document.getElementById('afm-mood-inline-hint');
  function checkMoodTerms() {{
    if (!moodInput || !moodHint || !moodLabels.length) return;
    const vocab = new Set(moodLabels.map((m) => String(m).toLowerCase()));
    const unknown = (moodInput.value || '')
      .split(',')
      .map((s) => s.trim().toLowerCase())
      .filter((s) => s && !vocab.has(s));
    moodHint.textContent = unknown.length
      ? 'Unknown mood(s): ' + unknown.join(', ') + ' — pick from suggestions.'
      : '';
  }}
  if (moodInput) {{
    moodInput.addEventListener('input', checkMoodTerms);
    moodInput.addEventListener('blur', checkMoodTerms);
    checkMoodTerms();
  }}

  function scrollToPreviewResults() {{
    const el = document.getElementById('preview-results');
    if (!el) return;
    if (el.tagName === 'DETAILS') el.open = true;
    window.requestAnimationFrame(() => {{
      el.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
    }});
  }}
  const shouldScrollPreview = {scroll_flag} || window.location.hash === '#preview-results';
  if (shouldScrollPreview) {{
    if (document.readyState === 'loading') {{
      document.addEventListener('DOMContentLoaded', scrollToPreviewResults);
    }} else {{
      scrollToPreviewResults();
    }}
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
            except ChannelDesignerError as exc:
                flash = _flash_html(str(exc), "error")
        elif request.args.get("new"):
            flash = _flash_html("New Channel — Design Programming, Preview Tracks, Then Deploy.", "ok")

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        discover_id = (request.form.get("discover_deploy") or "").strip()

        if discover_id:
            query_key = f"discover_query_{discover_id}"
            clap_query = (request.form.get(query_key) or "").strip()
            values.update(
                {
                    "programming_type": "clap_query",
                    "clap_query": clap_query,
                    "name": clap_query[:60] or f"Cluster {discover_id}",
                }
            )
            flash = _flash_html(
                "Cluster playlist loaded into designer — preview, then deploy.",
                "ok",
            )
        elif action == "edit":
            load_slug = (request.form.get("load_slug") or "").strip()
            if load_slug:
                return redirect(url_for("alchemy_fm_bridge.home", edit=load_slug) + "#designer")
        elif action == "new_channel":
            return redirect(url_for("alchemy_fm_bridge.home", new=1) + "#designer")
        elif action == "delete":
            try:
                station_id = int(request.form.get("station_id") or "0")
                delete_slug = (request.form.get("delete_slug") or "").strip()
                if station_id <= 0:
                    raise ChannelDesignerError("Invalid station id for delete.")
                _client().delete_station(station_id)
                _delete_local_channel(delete_slug)
                flash = _flash_html(
                    f"Deleted station '{delete_slug or station_id}' from Alchemy FM.",
                    "ok",
                )
                logger.info("alchemy_fm_bridge deleted station id=%s slug=%s", station_id, delete_slug)
            except ChannelDesignerError as exc:
                flash = _flash_html(str(exc), "error")
            except ValueError as exc:
                flash = _flash_html(str(exc), "error")
        else:
            values.update({k: request.form.get(k, values.get(k, "")) for k in request.form})
            values["enabled"] = request.form.get("enabled") == "on"
            values["bootstrap_queue"] = request.form.get("bootstrap_queue") == "on"
            values["living_enabled"] = request.form.get("living_enabled") == "on"
            values["living_auto_add"] = request.form.get("living_auto_add") == "on"
            values["living_auto_refresh"] = request.form.get("living_auto_refresh") == "on"

            values["bootstrap_enabled"] = request.form.get("bootstrap_enabled") == "on"

            pick_bootstrap = (request.form.get("pick_bootstrap_playlist") or "").strip()
            if pick_bootstrap:
                values["bootstrap_playlist_id"] = pick_bootstrap
                values["bootstrap_enabled"] = True
                limit = max(
                    5,
                    min(80, int(request.form.get("bootstrap_track_limit") or BOOTSTRAP_TRACK_LIMIT_DEFAULT)),
                )
                values["bootstrap_check"] = _verify_bootstrap_playlist(pick_bootstrap, limit=limit)

            pick_seed = (request.form.get("pick_seed") or "").strip()
            if pick_seed:
                values["seed_id"] = pick_seed
                values["programming_type"] = "similar_seed"
                if request.form.get("seed_search"):
                    values["seed_search_results"] = _search_tracks(request.form.get("seed_search") or "")
            pick_exclude_artist = (request.form.get("pick_exclude_artist") or "").strip()
            if pick_exclude_artist:
                values["filter_exclude_artists"] = _append_csv_term(
                    request.form.get("filter_exclude_artists") or "",
                    pick_exclude_artist,
                )
            elif action == "search_seed":
                values["seed_search_results"] = _search_tracks(request.form.get("seed_search") or "")
            elif action == "search_bootstrap_playlist":
                values["bootstrap_playlist_results"] = _search_playlists(
                    request.form.get("bootstrap_playlist_search") or ""
                )
            elif action == "verify_bootstrap_playlist":
                limit = max(
                    5,
                    min(80, int(request.form.get("bootstrap_track_limit") or BOOTSTRAP_TRACK_LIMIT_DEFAULT)),
                )
                values["bootstrap_check"] = _verify_bootstrap_playlist(
                    request.form.get("bootstrap_playlist_id") or "",
                    limit=limit,
                )
            elif action == "search_exclude_artist":
                values["exclude_artist_results"] = _search_artists(
                    request.form.get("exclude_artist_search") or ""
                )
            elif action == "start_clustering":
                try:
                    _clustering_start()
                    flash = _flash_html(
                        "Clustering started in AudioMuse. Check Active Tasks, then refresh this page.",
                        "ok",
                    )
                except ChannelDesignerError as exc:
                    flash = _flash_html(str(exc), "error")
            elif action.startswith("op_"):
                edit_slug = (request.form.get("editing_slug") or "").strip()
                station_id = int(request.form.get("op_station_id") or "0")
                if station_id <= 0:
                    flash = _flash_html("No Alchemy FM station id for this operation.", "error")
                else:
                    try:
                        client = _client()
                        if action == "op_refresh_queue":
                            client.refresh_queue(station_id)
                            flash = _flash_html("Queue refresh requested.", "ok")
                        elif action == "op_bootstrap":
                            client.bootstrap_station(station_id)
                            flash = _flash_html("Station pool/bootstrap rebuild started.", "ok")
                        elif action == "op_rebuild_m3u":
                            client.rebuild_m3u(station_id)
                            flash = _flash_html("queue.m3u rebuilt from database.", "ok")
                        elif action == "op_toggle_enabled":
                            remote = None
                            for st in client.test_connection():
                                if int(st.get("id") or 0) == station_id:
                                    remote = st
                                    break
                            current = bool(remote.get("enabled")) if remote else False
                            client.set_station_enabled(station_id, not current)
                            flash = _flash_html(
                                "Station taken off air." if current else "Station put on air.",
                                "ok",
                            )
                        elif action == "op_delete_artwork":
                            client.delete_artwork(station_id)
                            flash = _flash_html("Station artwork removed.", "ok")
                        elif action == "op_upload_artwork":
                            upload = request.files.get("artwork_file")
                            if not upload or not upload.filename:
                                raise ChannelDesignerError("Choose an image file to upload.")
                            data = upload.read()
                            if not data:
                                raise ChannelDesignerError("Uploaded file is empty.")
                            client.upload_artwork(
                                station_id,
                                upload.filename,
                                data,
                                upload.mimetype or "image/jpeg",
                            )
                            flash = _flash_html("Station artwork uploaded.", "ok")
                        if edit_slug:
                            loaded = _apply_loaded_channel(edit_slug)
                            values, preview_tracks, _ = loaded
                    except ChannelDesignerError as exc:
                        flash = _flash_html(str(exc), "error")
            elif action == "test":
                try:
                    stations = _client().test_connection()
                    flash = _flash_html(f"Alchemy FM connected — {len(stations)} station(s) on air.", "ok")
                except ChannelDesignerError as exc:
                    flash = _flash_html(str(exc), "error")
            elif action in ("preview", "push", "chat_preview"):
                try:
                    profile = profile_from_form(request.form)
                    values = _form_values_from_profile(profile)
                    if (request.form.get("editing_slug") or "").strip():
                        values["editing_slug"] = request.form.get("editing_slug").strip()
                    slug = profile["station"]["slug"]

                    if action == "chat_preview":
                        prompt = (request.form.get("chat_prompt") or "").strip()
                        raw = _chat_playlist_tracks(prompt)
                        if not raw:
                            raise ChannelDesignerError("Chat designer returned no tracks.")
                        profile["design_notes"] = prompt
                        profile["programming"] = {
                            "type": "clap_query",
                            "query": prompt[:120],
                            "limit": PREVIEW_LIMIT_DEFAULT,
                        }
                        values = _form_values_from_profile(profile)
                        values["chat_prompt"] = prompt
                        unfiltered = enrich_preview(raw)
                        preview_tracks = apply_track_filters(unfiltered, profile)
                        _apply_filter_feedback(values, profile, unfiltered, preview_tracks)
                        item_ids = [t["item_id"] for t in preview_tracks]
                        _record_audition(slug, item_ids)
                        _save_channel(profile, preview_ids=item_ids)
                        values["scroll_to_preview"] = True
                        flash = _flash_html(
                            f"Chat preview — {len(preview_tracks)} tracks. Tweak programming/filters, then deploy.",
                            "ok",
                        )
                    elif action == "preview":
                        unfiltered = _merged_programming_tracks_unfiltered(profile, slug)
                        preview_tracks = apply_track_filters(unfiltered, profile)
                        if not preview_tracks:
                            raise ChannelDesignerError(
                                "No tracks matched programming (and living pool, if enabled)."
                            )
                        _apply_filter_feedback(values, profile, unfiltered, preview_tracks)
                        bootstrap_check = _apply_bootstrap_check(values, profile)
                        item_ids = [t["item_id"] for t in preview_tracks]
                        _record_audition(slug, item_ids)
                        if (profile.get("living") or {}).get("enabled"):
                            _add_to_pool(slug, item_ids, source="preview")
                        _save_channel(profile, preview_ids=item_ids)
                        values["scroll_to_preview"] = True
                        flash = _flash_html(
                            f"Preview ready — {len(preview_tracks)} tracks from AudioMuse. "
                            "Review below, then deploy to Alchemy FM.",
                            "ok",
                        )
                        if bootstrap_check and not bootstrap_check.get("ok"):
                            flash += _flash_html(
                                f"Bootstrap warning: {bootstrap_check.get('error')}",
                                "error",
                            )
                    elif action == "push":
                        unfiltered = _merged_programming_tracks_unfiltered(profile, slug)
                        preview_tracks = apply_track_filters(unfiltered, profile)
                        if not preview_tracks:
                            raise ChannelDesignerError(
                                "No tracks to deploy. Preview programming first or broaden your criteria."
                            )
                        _apply_filter_feedback(values, profile, unfiltered, preview_tracks)
                        bootstrap_check = _apply_bootstrap_check(values, profile)
                        if bootstrap_check and not bootstrap_check.get("ok"):
                            raise ChannelDesignerError(
                                bootstrap_check.get("error") or "Bootstrap playlist could not be verified."
                            )
                        payload = channel_profile_to_alchemy_payload(profile, preview_tracks)
                        slug = payload["slug"]
                        item_ids = [t["item_id"] for t in preview_tracks]
                        _record_audition(slug, item_ids)
                        if (profile.get("living") or {}).get("enabled"):
                            _add_to_pool(slug, item_ids, source="preview")
                        station, push_action = _client().push_station(
                            payload,
                            slug=slug,
                            bootstrap=bool(payload.get("bootstrap_queue")),
                        )
                        _save_channel(
                            profile,
                            preview_ids=item_ids,
                            station=station,
                            action=push_action,
                        )
                        flash = _flash_html(
                            f"Channel '{station.get('name')}' {push_action} on Alchemy FM "
                            f"(id {station.get('id')}) with live {profile['programming']['type']} programming.",
                            "ok",
                        )
                        values = _form_values_from_profile(profile)
                        values["editing_slug"] = slug
                        if station:
                            values["edit_station_id"] = int(station.get("id") or 0)
                            values["edit_on_air"] = bool(station.get("enabled"))
                            values["edit_queued"] = station.get("queued_count", "?")
                        logger.info(
                            "alchemy_fm_bridge deployed slug=%s action=%s type=%s",
                            slug,
                            push_action,
                            profile["programming"]["type"],
                        )
                except ChannelDesignerError as exc:
                    slug = (
                        request.form.get("editing_slug")
                        or request.form.get("slug")
                        or _slugify(request.form.get("name") or "channel")
                    ).strip()
                    _record_channel_error(slug, str(exc))
                    flash = _flash_html(str(exc), "error")

    editing_slug = (values.get("editing_slug") or "").strip()
    stations_html = _stations_section_html(editing_slug or None)
    edit_bar = _edit_toolbar_html(values)
    designer_section_open = (
        '<section class="afm-section" id="designer">'
        '<div class="afm-section-head">'
        "<div><h2 class='afm-section-title'>Channel Designer</h2>"
        "<p class='afm-section-note'>Work Through the Numbered Steps — Name, Program, Preview, Then Deploy.</p></div>"
        "</div>"
        if not editing_slug
        else ""
    )
    designer_section_close = "</section>" if not editing_slug else ""
    scroll_to_preview = bool(values.get("scroll_to_preview"))
    preview_block = _preview_results_html(
        preview_tracks,
        highlight=scroll_to_preview,
    )
    audition_block = (
        "<section class='afm-panel'>"
        + _panel_heading("Audition History", "Recent Preview Runs for This Channel")
        + f"{_audition_history_html(editing_slug or None)}"
        + "</section>"
    )
    designer_form = (
        f"{designer_section_open}"
        "<form method='post' id='afm-designer-form' class='afm-designer-form'>"
        f"{_designer_flow_overview_html()}"
        f"{_discover_channels_html()}"
        f"{_station_identity_fields_html(values)}"
        f"{_programming_fields_html(values)}"
        f"{_preview_step_html()}"
        f"{_filters_fields_html(values)}"
        f"{_bootstrap_fields_html(values)}"
        f"{_living_fields_html(values)}"
        f"{_playback_rules_fields_html(values)}"
        f"{_deploy_actions_fields_html(values)}"
        f"{_chat_designer_fields_html(values)}"
        "</form>"
        f"{preview_block}"
        f"{audition_block}"
        f"{designer_section_close}"
    )

    if editing_slug:
        main_flow = f"{edit_bar}{designer_form}{stations_html}"
    else:
        main_flow = f"{stations_html}{designer_form}"

    mood_labels = _audiomuse_mood_labels()
    body = (
        f"{_page_styles()}"
        '<div class="afm-shell">'
        f"{_page_header_html()}"
        f"{flash}"
        f"{main_flow}"
        "</div>"
        f"{_page_script(_mood_centroids_data(), mood_labels, scroll_to_preview=scroll_to_preview)}"
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
        api_url = request.form.get("audiomuse_api_url")
        if api_url is not None:
            set_setting("audiomuse_api_url", api_url.strip().rstrip("/"))
        return redirect(manage_plugins_url())

    alchemyfm_url = get_setting("alchemyfm_url", "")
    alchemyfm_username = get_setting("alchemyfm_username", "admin")
    audiomuse_api_token = get_setting("audiomuse_api_token", "")
    audiomuse_api_url = get_setting("audiomuse_api_url", "")
    body = (
        "<form method='post' style='display:grid;gap:1rem;max-width:36rem;'>"
        "<p>Connect to your Alchemy FM broadcast instance. Credentials match "
        "<code>ADMIN_USERNAME</code> / <code>ADMIN_PASSWORD</code> in Alchemy FM. "
        f"<a href='{html.escape(HELP_DOC_URL)}' target='_blank' rel='noopener noreferrer'>Help</a></p>"
        "<p class='hint'><strong>Cloudflare / public URL:</strong> If Alchemy FM is behind Cloudflare, allow "
        "server-to-server access to <code>/api/admin/*</code> from your AudioMuse host (WAF skip rule or "
        "bypass Bot Fight Mode). Otherwise use a LAN/direct URL that does not go through Cloudflare "
        "(e.g. <code>http://192.168.1.100:8080</code>).</p>"
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
        "<div><label>AudioMuse API URL (optional, for worker/cron)</label>"
        f"<input name='audiomuse_api_url' placeholder='http://192.168.1.100:8387' "
        f"value='{html.escape(audiomuse_api_url)}'>"
        "<p class='hint'>Living-channel cron and <code>on_song_analyzed</code> run on the worker and "
        "need a URL the worker can reach (LAN IP, not <code>localhost</code>). Leave blank to use "
        "AudioMuse control host/port from the environment.</p></div>"
        "<button type='submit'>Save</button>"
        "</form>"
    )
    return render_page(body, title="Alchemy FM Bridge Settings")


def _patch_cron_scheduled_tasks_label() -> None:
    """Show a friendly label on AudioMuse Administration → Scheduled Tasks."""
    try:
        from flask import Response
        import app_cron
        from plugin.manager import plugin_manager

        original = plugin_manager.available_cron_tasks

        def available_cron_tasks():
            items = original()
            patched: list[dict[str, str]] = []
            for item in items:
                if item.get("task_type") == CRON_TASK_TYPE:
                    patched.append(
                        {
                            "task_type": CRON_TASK_TYPE,
                            "plugin": CRON_TASK_LABEL,
                            "task": "",
                        }
                    )
                else:
                    patched.append(item)
            return patched

        plugin_manager.available_cron_tasks = available_cron_tasks

        original_page = app_cron.cron_bp.view_functions.get("cron_page")
        if original_page is None:
            return

        label_script = (
            "<script>"
            "(function(){"
            f"var needle={json.dumps(CRON_TASK_TYPE)};"
            f"var label={json.dumps(CRON_TASK_LABEL)};"
            "function fixLabels(){"
            "document.querySelectorAll('#plugin-cron-list label').forEach(function(el){"
            "if(el.dataset.afmFixed)return;"
            "if(el.textContent.indexOf(needle)!==-1){"
            "el.textContent=label;el.dataset.afmFixed='1';"
            "}});"
            "}"
            "document.addEventListener('DOMContentLoaded',function(){"
            "fixLabels();"
            "var list=document.getElementById('plugin-cron-list');"
            "if(list)new MutationObserver(fixLabels).observe(list,{childList:true,subtree:true});"
            "});"
            "})();"
            "</script>"
        )

        def cron_page():
            result = original_page()
            if isinstance(result, str) and label_script not in result:
                lower = result.lower()
                idx = lower.rfind("</body>")
                if idx != -1:
                    return result[:idx] + label_script + result[idx:]
                return result + label_script
            if isinstance(result, Response):
                body = result.get_data(as_text=True)
                if label_script not in body:
                    lower = body.lower()
                    idx = lower.rfind("</body>")
                    if idx != -1:
                        body = body[:idx] + label_script + body[idx:]
                        result.set_data(body)
            return result

        app_cron.cron_bp.view_functions["cron_page"] = cron_page
    except Exception:
        logger.exception("alchemy_fm_bridge: could not patch cron task label")


def register(ctx):
    ctx.on_install(migrate)
    ctx.add_blueprint(bp)
    ctx.add_menu_item("Alchemy FM", "alchemy_fm_bridge.home", admin_only=True)
    ctx.on_song_analyzed(on_song_analyzed)
    ctx.on_flask_start(_patch_cron_scheduled_tasks_label)
    ctx.add_cron_task(CRON_TASK_LIVING, refresh_living_channels)
