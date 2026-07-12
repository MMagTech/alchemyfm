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

from flask import Blueprint, jsonify, request, redirect, url_for

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

PLUGIN_VERSION = "3.0.7"
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
            detail = parsed.get("detail") or parsed.get("error")
            if detail:
                detail_text = str(detail)
                if status == 401 and detail_text.lower() in (
                    "authentication required",
                    "unauthorized",
                ):
                    return (
                        "Alchemy FM rejected admin login (HTTP 401). "
                        "Re-open plugin Settings and re-enter ADMIN_USERNAME / ADMIN_PASSWORD "
                        "from your Alchemy FM .env (use the LAN URL, e.g. http://192.168.x.x:9246)."
                    )
                return detail_text
    except json.JSONDecodeError:
        pass
    if status == 400 and "icecast mount" in body.lower():
        return (
            f"Alchemy FM rejected the station (HTTP 400): {body[:200]}\n"
            "Pick a different Icecast mount in Step 1, or delete the existing station using that mount."
        )
    text = re.sub(r"<[^>]+>", " ", body)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:500] or f"HTTP {status}"


def _audiomuse_http_error_message(detail: str, status: int) -> str:
    if status == 401:
        return (
            "AudioMuse returned 401 Unauthorized. Preview and deploy call AudioMuse APIs "
            "(CLAP, lyrics, mood clusters) — not Alchemy FM. Test Connection only checks Alchemy. "
            "Fix: in plugin Settings set audiomuse_api_token (AudioMuse → Settings → API), "
            "leave AudioMuse API URL blank unless the worker needs it, and do not put the Alchemy FM URL there."
        )
    if status == 404:
        return f"AudioMuse API route not found (HTTP 404). Update AudioMuse core or the Channel Designer plugin."
    try:
        parsed = json.loads(detail)
        if isinstance(parsed, dict):
            if parsed.get("error"):
                return str(parsed["error"])
            if parsed.get("message"):
                return str(parsed["message"])
    except json.JSONDecodeError:
        pass
    if "<html" in detail.lower():
        title_match = re.search(r"<title>([^<]+)</title>", detail, re.I)
        if title_match:
            return f"AudioMuse API HTTP {status}: {title_match.group(1).strip()}"
        return f"AudioMuse API HTTP {status} (HTML error page)."
    text = re.sub(r"<[^>]+>", " ", detail)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:500] or f"AudioMuse API HTTP {status}"


class ChannelDesignerError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _text(value: Any) -> str:
    """Coerce optional form/JSON values to a stripped string (None-safe)."""
    if value is None:
        return ""
    return str(value).strip()


def _decode_json_body(body: str, *, context: str) -> Any:
    if not body:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        preview = re.sub(r"\s+", " ", body[:240]).strip()
        raise ChannelDesignerError(
            f"{context} returned non-JSON (often an HTML login or error page). "
            "Check plugin settings: Alchemy FM URL/password, leave AudioMuse API URL blank "
            "unless the worker needs it, and set audiomuse_api_token if AudioMuse API auth is on. "
            f"Response preview: {preview or '(empty)'}"
        ) from exc


def _plugin_error_page(title: str, message: str) -> str:
    settings_href = html.escape(url_for("alchemy_fm_bridge.settings"))
    body = (
        f"{_page_styles()}"
        '<div class="afm-shell">'
        f'<section class="afm-section"><h2 class="afm-section-title">{html.escape(title)}</h2>'
        f'<p class="afm-flash afm-flash-error">{html.escape(message)}</p>'
        "<p class='hint'>If this started right after saving settings, double-check "
        "<strong>Alchemy FM URL</strong> (e.g. <code>http://192.168.1.10:9246</code>), "
        "<strong>username</strong> (<code>mmagtech</code>, not <code>admin</code> if that is your admin user), "
        "and password. Only fill <strong>AudioMuse API URL</strong> for living-channel cron — "
        "leave it blank for normal designer use.</p>"
        f"<p><a href='{settings_href}'>Open plugin settings</a></p>"
        "</section></div>"
    )
    return render_page(body, title=title)


ALCHEMY_SESSION_COOKIE = "admin_session"


class AlchemyFmClient:
    def __init__(self, base_url: str, username: str, password: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username.strip()
        self.password = password
        self.timeout = timeout
        self._session_cookie: str | None = None
        token = base64.b64encode(f"{self.username}:{self.password}".encode()).decode("ascii")
        self.headers = {
            "Authorization": f"Basic {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": ALCHEMY_FM_USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        }

    def _auth_headers(self, *, json_body: bool = True) -> dict[str, str]:
        headers = dict(self.headers)
        if not json_body:
            headers.pop("Content-Type", None)
        if self._session_cookie:
            headers["Cookie"] = self._session_cookie
        return headers

    def _establish_session(self) -> None:
        url = f"{self.base_url}/api/admin/login"
        body = json.dumps({"username": self.username, "password": self.password}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": ALCHEMY_FM_USER_AGENT,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                set_cookie = resp.headers.get("Set-Cookie") or ""
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ChannelDesignerError(
                _friendly_http_error(exc.code, detail), status=exc.code
            ) from exc
        except urllib.error.URLError as exc:
            raise ChannelDesignerError(
                f"Could not reach Alchemy FM at {self.base_url}: {exc.reason}"
            ) from exc
        prefix = f"{ALCHEMY_SESSION_COOKIE}="
        for segment in set_cookie.split(","):
            segment = segment.strip()
            if segment.startswith(prefix):
                self._session_cookie = segment.split(";", 1)[0]
                return
        raise ChannelDesignerError(
            "Alchemy FM login succeeded but returned no admin session cookie."
        )

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        _auth_retried: bool = False,
    ) -> Any:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, headers=self._auth_headers(), method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
                return _decode_json_body(body, context=f"Alchemy FM {method} {path}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code == 401 and not _auth_retried:
                try:
                    self._establish_session()
                except ChannelDesignerError:
                    raise ChannelDesignerError(
                        _friendly_http_error(exc.code, detail), status=exc.code
                    ) from exc
                return self._request(method, path, payload, _auth_retried=True)
            raise ChannelDesignerError(
                _friendly_http_error(exc.code, detail), status=exc.code
            ) from exc
        except urllib.error.URLError as exc:
            raise ChannelDesignerError(
                f"Could not reach Alchemy FM at {self.base_url}: {exc.reason}"
            ) from exc

    def verify_credentials(self) -> None:
        """Confirm admin login — establishes session cookie when Basic auth is blocked."""
        self._request("GET", "/api/admin/me")

    def _verify_deploy_ready_legacy_backend(self, *, strict: bool = True) -> str | None:
        """Backend predates /api/admin/deploy-check — probe AudioMuse from plugin."""
        try:
            audiomuse_get("/api/mood_centroids", timeout=15)
        except ChannelDesignerError as exc:
            if exc.status == 401:
                message = (
                    "AudioMuse returned 401 Unauthorized.\n"
                    "1. Channel Designer plugin settings: audiomuse_api_token\n"
                    "2. Alchemy FM .env: AUDIOMUSE_API_TOKEN (same token — required for bootstrap)\n"
                    "Copy the token from AudioMuse Settings → API."
                )
                if strict:
                    raise ChannelDesignerError(message) from exc
                return message
            message = (
                f"Cannot reach AudioMuse from plugin: {exc}\n"
                "Fix AudioMuse connectivity before deploying."
            )
            if strict:
                raise ChannelDesignerError(message) from exc
            return message
        return (
            "Alchemy FM backend is outdated (missing /api/admin/deploy-check). "
            "Pull ghcr.io/mmagtech/alchemyfm-backend:latest and restart. "
            "Deploy will continue, but bootstrap needs AUDIOMUSE_API_TOKEN, AUDIOMUSE_URL, "
            "NAVIDROME_URL, NAVIDROME_USER, and NAVIDROME_PASSWORD in Alchemy FM .env."
        )

    def verify_deploy_ready(self, *, strict: bool = True) -> str | None:
        """Ensure Alchemy FM can reach AudioMuse + Navidrome (bootstrap will fail otherwise).

        When strict=False (Deploy), returns a warning string instead of raising so deploy
        can proceed — Test Connection may succeed while bootstrap env on Alchemy is wrong.
        """
        try:
            check = self._request("GET", "/api/admin/deploy-check")
        except ChannelDesignerError as exc:
            if exc.status == 404:
                return self._verify_deploy_ready_legacy_backend(strict=strict)
            if strict:
                raise
            return str(exc)
        if not isinstance(check, dict) or check.get("ok"):
            return None
        parts: list[str] = []
        audiomuse = check.get("audiomuse") if isinstance(check.get("audiomuse"), dict) else {}
        navidrome = check.get("navidrome") if isinstance(check.get("navidrome"), dict) else {}
        if not audiomuse.get("ok") and audiomuse.get("error"):
            parts.append(str(audiomuse["error"]))
        if not navidrome.get("ok") and navidrome.get("error"):
            parts.append(str(navidrome["error"]))
        hint = (
            "Use the Alchemy FM LAN URL in plugin settings (http://192.168.x.x:PORT), "
            "not the public Cloudflare URL."
        )
        body = "\n".join(parts) if parts else "AudioMuse or Navidrome is not reachable from Alchemy FM."
        message = f"Alchemy FM is not ready to deploy stations.\n{body}\n{hint}"
        if strict:
            raise ChannelDesignerError(message)
        return message

    def _list_stations(self) -> list[dict[str, Any]]:
        stations = self._request("GET", "/api/admin/stations")
        if not isinstance(stations, list):
            raise ChannelDesignerError("Unexpected response from /api/admin/stations")
        return stations

    def test_connection(self, *, skip_deploy_check: bool = False) -> list[dict[str, Any]]:
        self.verify_credentials()
        if not skip_deploy_check:
            self.verify_deploy_ready()
        return self._list_stations()

    def find_station_by_slug(self, slug: str) -> dict[str, Any] | None:
        slug = slug.strip().lower()
        for station in self._list_stations():
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
                result = _decode_json_body(raw, context=f"Alchemy FM POST artwork station {station_id}")
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
        station_id: int | None = None,
        create_new: bool = False,
        bootstrap: bool = True,
    ) -> tuple[dict[str, Any], str, str | None]:
        target_slug = (slug or payload.get("slug") or "").strip().lower()
        update_fields = {
            key: value
            for key, value in payload.items()
            if key not in ("slug", "bootstrap_queue")
        }

        def _create_new_station() -> tuple[dict[str, Any], int, str]:
            create_payload = {
                key: value for key, value in payload.items() if key != "bootstrap_queue"
            }
            create_payload["bootstrap_queue"] = False
            try:
                created = self.create_station(create_payload)
            except ChannelDesignerError as exc:
                raise ChannelDesignerError(
                    f"Deploy failed while creating station '{target_slug}'.\n{exc}"
                ) from exc
            new_id = int(created["id"])
            actual_slug = str(created.get("slug") or target_slug).strip().lower()
            return created, new_id, actual_slug

        if station_id:
            try:
                station = self.update_station(int(station_id), update_fields)
            except ChannelDesignerError as exc:
                raise ChannelDesignerError(
                    f"Deploy failed while updating station id {station_id}.\n{exc}"
                ) from exc
            action = "updated"
            target_slug = str(station.get("slug") or target_slug).strip().lower()
        elif create_new:
            station, station_id, target_slug = _create_new_station()
            action = "created"
        else:
            try:
                existing = self.find_station_by_slug(target_slug) if target_slug else None
            except ChannelDesignerError as exc:
                raise ChannelDesignerError(
                    f"Deploy failed while listing stations on Alchemy FM.\n{exc}"
                ) from exc
            if existing:
                station_id = int(existing["id"])
                try:
                    station = self.update_station(station_id, update_fields)
                except ChannelDesignerError as exc:
                    raise ChannelDesignerError(
                        f"Deploy failed while updating station '{target_slug}'.\n{exc}"
                    ) from exc
                action = "updated"
            else:
                station, station_id, target_slug = _create_new_station()
                action = "created"
        bootstrap_warning: str | None = None
        if bootstrap and payload.get("bootstrap_queue", True):
            try:
                station = self.bootstrap_station(station_id)
            except ChannelDesignerError as exc:
                bootstrap_warning = (
                    f"Station '{target_slug}' was saved on Alchemy FM (id {station_id}), "
                    "but the play queue could not be filled. Preview runs inside AudioMuse; "
                    "bootstrap runs from the Alchemy FM container and needs AUDIOMUSE_URL + "
                    "AUDIOMUSE_API_TOKEN in Alchemy FM .env.\n"
                    f"{exc}"
                )
        return station, action, bootstrap_warning


def _audiomuse_base_url() -> str:
    custom = (get_setting("audiomuse_api_url") or "").strip().rstrip("/")
    alchemy = (get_setting("alchemyfm_url") or "").strip().rstrip("/")
    if custom:
        if alchemy and custom == alchemy:
            raise ChannelDesignerError(
                "Plugin settings mistake: AudioMuse API URL is set to your Alchemy FM URL. "
                "Leave AudioMuse API URL blank for Channel Designer preview/deploy, or use "
                "your AudioMuse URL (e.g. http://192.168.1.10:8387) for living-channel cron only."
            )
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
    # Plugin routes run inside AudioMuse — reuse the logged-in admin session for API calls.
    try:
        from flask import has_request_context, request

        if has_request_context():
            cookie = request.headers.get("Cookie")
            if cookie:
                headers["Cookie"] = cookie
    except Exception:
        pass
    return headers


def audiomuse_get(path: str, *, params: dict[str, str] | None = None, timeout: float = 60.0) -> Any:
    query = "?" + urllib.parse.urlencode(params) if params else ""
    url = f"{_audiomuse_base_url()}{path}{query}"
    req = urllib.request.Request(url, headers=_audiomuse_headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return _decode_json_body(raw, context=f"AudioMuse GET {path} ({_audiomuse_base_url()})")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ChannelDesignerError(
            _audiomuse_http_error_message(detail, exc.code),
            status=exc.code,
        ) from exc
    except urllib.error.URLError as exc:
        raise ChannelDesignerError(f"Could not reach AudioMuse API: {exc.reason}") from exc


def audiomuse_post(path: str, payload: dict[str, Any], *, timeout: float = 120.0) -> Any:
    url = f"{_audiomuse_base_url()}{path}"
    data = json.dumps(payload).encode("utf-8")
    headers = {**_audiomuse_headers(), "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return _decode_json_body(raw, context=f"AudioMuse POST {path} ({_audiomuse_base_url()})")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        message = _audiomuse_http_error_message(detail, exc.code)
        if exc.code != 401:
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

LIVE_SOURCE_TYPES = frozenset(
    {"clap_query", "lyrics_query", "mood_centroid", "alchemy_anchor", "similar_seed"}
)
PREVIEW_LIMIT_DEFAULT = 30
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
    if isinstance(results, dict):
        for key in ("results", "tracks", "items", "data"):
            nested = results.get(key)
            if isinstance(nested, list):
                results = nested
                break
        else:
            return []
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


def _parse_centroid_index(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _preview_empty_message(
    unfiltered: list[dict[str, Any]], profile: dict[str, Any]
) -> str:
    filters = profile.get("filters") or {}
    if unfiltered and _filters_active(filters):
        return (
            f"Filters removed all {len(unfiltered)} track(s) from programming. "
            "Broaden your filter rules or clear them, then preview again."
        )
    ptype = (profile.get("programming") or {}).get("type", "")
    if ptype == "mood_centroid":
        return "AudioMuse returned no tracks for that mood cluster. Pick a different cluster or mood."
    if ptype == "alchemy_anchor":
        return "AudioMuse returned no tracks for that anchor. Pick a different Song Alchemy anchor."
    if ptype == "similar_seed":
        return "AudioMuse returned no similar tracks for that seed. Try another library track."
    if ptype == "lyrics_query":
        return "AudioMuse returned no tracks for that lyrics theme. Try a broader theme."
    return "AudioMuse returned no tracks for that sonic vibe. Try different wording or a broader query."


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
        centroid_index = _parse_centroid_index(profile["programming"].get("centroid_index"))
        if not mood or centroid_index is None:
            raise ChannelDesignerError("Choose a mood and cluster.")
        data = audiomuse_get(
            "/api/similar_tracks",
            params={
                "mood": mood,
                "centroid_index": str(centroid_index),
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
        centroid_index = _parse_centroid_index(programming.get("centroid_index"))
        if not mood or centroid_index is None:
            raise ChannelDesignerError("Mood cluster programming requires mood and cluster.")
        return f"{mood}:{centroid_index}"
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


# Mirrors AudioMuse config.STRATIFIED_GENRES — used to pick the Genre column from mood_vector tags.
STRATIFIED_GENRES = (
    "rock",
    "pop",
    "alternative",
    "indie",
    "electronic",
    "jazz",
    "metal",
    "classic rock",
    "soul",
    "indie rock",
    "electronica",
    "folk",
    "punk",
    "blues",
    "hard rock",
    "ambient",
    "acoustic",
    "experimental",
    "hip-hop",
    "country",
    "funk",
    "electro",
    "heavy metal",
    "progressive rock",
    "rnb",
    "indie pop",
    "house",
)


def _parse_mood_vector(value: Any) -> dict[str, float]:
    """Parse AudioMuse mood_vector (comma-separated string or dict) into tag -> score."""
    if not value:
        return {}
    if isinstance(value, dict):
        parsed: dict[str, float] = {}
        for key, raw_score in value.items():
            label = str(key).strip()
            if not label:
                continue
            try:
                parsed[label] = float(raw_score)
            except (TypeError, ValueError):
                continue
        return parsed
    if isinstance(value, str):
        parsed: dict[str, float] = {}
        for part in value.split(","):
            label, _, raw_score = part.partition(":")
            label = label.strip()
            if not label:
                continue
            try:
                parsed[label] = float(raw_score)
            except ValueError:
                continue
        return parsed
    return {}


def _top_stratified_genre(mood_scores: dict[str, float]) -> str:
    """Highest-scoring stratified genre label in mood_scores (AudioMuse top_stratified_genre)."""
    if not mood_scores:
        return ""
    scores_by_lower = {label.lower(): (label, score) for label, score in mood_scores.items()}
    candidates: list[tuple[str, float]] = []
    for genre in STRATIFIED_GENRES:
        match = scores_by_lower.get(genre.lower())
        if match:
            candidates.append(match)
    if not candidates:
        return ""
    return max(candidates, key=lambda item: item[1])[0]


def _mood_tags_from_scores(mood_scores: dict[str, float], *, limit: int = 4) -> list[str]:
    top = sorted(mood_scores.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [name for name, _ in top]


def _apply_score_metadata(row: dict[str, Any], score: dict[str, Any]) -> None:
    row["tempo"] = score.get("tempo")
    row["energy"] = score.get("energy")
    row["year"] = score.get("year")
    mood_scores = _parse_mood_vector(score.get("mood_vector") or score.get("moods"))
    row["top_genre"] = (
        score.get("top_genre")
        or score.get("genre")
        or _top_stratified_genre(mood_scores)
        or ""
    )
    row["mood_tags"] = _mood_tags_from_scores(mood_scores)
    row["mood"] = ", ".join(row["mood_tags"][:2])


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
        _apply_score_metadata(row, score)
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


def _search_anchors(query: str) -> list[dict[str, Any]]:
    anchors = _anchors()
    q = query.strip().lower()
    if not q:
        return anchors[:20]
    matched = [
        anchor
        for anchor in anchors
        if q in str(anchor.get("name") or "").lower()
        or q in str(anchor.get("id") or "")
    ]
    return matched[:15]


def _resolve_anchor_id_from_form(form) -> str:
    anchor_id = (form.get("anchor_id") or "").strip()
    if anchor_id:
        return anchor_id
    hint = (form.get("anchor_search") or "").strip()
    if not hint:
        return ""
    exact = [
        anchor
        for anchor in _anchors()
        if str(anchor.get("name") or "").strip().lower() == hint.lower()
    ]
    if len(exact) == 1:
        return str(exact[0]["id"])
    fuzzy = _search_anchors(hint)
    if len(fuzzy) == 1:
        return str(fuzzy[0]["id"])
    hint_l = hint.lower()
    prefix = [
        anchor
        for anchor in fuzzy
        if str(anchor.get("name") or "").strip().lower().startswith(hint_l)
    ]
    if len(prefix) == 1:
        return str(prefix[0]["id"])
    return ""


def _resolve_seed_id_from_form(form) -> str:
    seed_id = (form.get("seed_id") or form.get("pick_seed") or "").strip()
    if seed_id:
        return seed_id
    hint = (form.get("seed_search") or "").strip()
    if len(hint) < 2:
        return ""
    tracks = _search_tracks(hint)
    if len(tracks) == 1:
        return str(tracks[0]["item_id"])
    title_part = hint.split("—")[0].split("-")[0].strip().lower()
    if title_part:
        title_matches = [
            t
            for t in tracks
            if str(t.get("title") or "").strip().lower() == title_part
        ]
        if len(title_matches) == 1:
            return str(title_matches[0]["item_id"])
    return ""


def _search_genres(query: str) -> list[str]:
    q = query.strip().lower()
    genres: set[str] = set(STRATIFIED_GENRES)
    if len(q) >= 2:
        for track in _search_tracks(q):
            genre = _track_genre(track)
            if genre:
                genres.add(genre)
    if q:
        return sorted(g for g in genres if q in g.lower())[:15]
    return sorted(genres)[:15]


def _search_moods(query: str) -> list[str]:
    labels = _audiomuse_mood_labels()
    q = query.strip().lower()
    if not q:
        return labels[:20]
    return [label for label in labels if q in label.lower()][:15]


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
    """Navidrome playlist search — matches playlist title only (AudioMuse /api/search_playlists)."""
    query = query.strip()
    if len(query) < 2:
        return []
    try:
        data = audiomuse_get("/api/search_playlists", params={"query": query})
    except ChannelDesignerError:
        return []
    if not isinstance(data, list):
        return []
    needle = query.lower()
    results: list[dict[str, Any]] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        pl_id = row.get("id") or row.get("Id")
        name = str(row.get("name") or row.get("Name") or "").strip()
        if not pl_id or not name:
            continue
        if needle not in name.lower():
            continue
        count = row.get("count")
        if count is None:
            count = row.get("songCount")
        if count is None:
            count = row.get("ChildCount")
        results.append({"id": str(pl_id), "name": name, "count": count})
    return results[:50]


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
        "<p class='hint'>Search Navidrome playlists above and pick a result, or paste a playlist id — "
        "verification runs automatically.</p>"
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


def _mediaserver_playlist_track_ids(playlist_id: str) -> list[str]:
    """Load Navidrome/Jellyfin playlist track IDs via AudioMuse mediaserver (in-process)."""
    try:
        from tasks.mediaserver import get_playlist_track_ids
    except ImportError as exc:
        raise ChannelDesignerError(
            "Bootstrap verify needs AudioMuse mediaserver access. Run the plugin inside AudioMuse "
            "(not a standalone zip test server)."
        ) from exc
    try:
        raw = get_playlist_track_ids(playlist_id) or []
    except Exception as exc:
        raise ChannelDesignerError(f"Navidrome playlist lookup failed: {exc}") from exc
    return [str(item_id) for item_id in raw if item_id]


def _playlist_track_ids(playlist_id: str, *, limit: int) -> list[str]:
    playlist_id = playlist_id.strip()
    if not playlist_id:
        return []
    ids = _mediaserver_playlist_track_ids(playlist_id)
    if not ids:
        return []
    if limit and len(ids) > limit:
        ids = ids[:limit]
    try:
        scores = get_score_data_by_ids(ids)
    except Exception:
        scores = []
    known = {str(row.get("item_id")) for row in scores if row.get("item_id")}
    analyzed = [item_id for item_id in ids if item_id in known]
    if not analyzed:
        raise ChannelDesignerError(
            f"Playlist resolved to {len(ids)} track(s) on the media server, but none are in the "
            "AudioMuse analysis database. Run library analysis first."
        )
    return analyzed


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
        {"userInput": prompt},
    )
    results = None
    if isinstance(data, dict):
        if data.get("error"):
            raise ChannelDesignerError(str(data["error"]))
        response = data.get("response")
        if isinstance(response, dict):
            if response.get("message") and response.get("ai_provider_used") == "NONE":
                raise ChannelDesignerError(
                    "AudioMuse chat AI is not configured. Set an AI provider in AudioMuse settings."
                )
            results = response.get("query_results")
        if results is None:
            results = (
                data.get("query_results")
                or data.get("results")
                or data.get("tracks")
                or data.get("playlist")
            )
    rows = _track_rows_from_results(results)
    if limit and len(rows) > limit:
        return rows[:limit]
    return rows


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


def _station_identity_from_form(form, *, for_deploy: bool) -> dict[str, Any]:
    editing_slug = (form.get("editing_slug") or "").strip()
    name = (form.get("name") or "").strip()
    slug = (form.get("slug") or "").strip()

    if for_deploy and not name:
        raise ChannelDesignerError("Channel name is required.")

    if not name:
        for key in ("chat_prompt", "clap_query", "lyrics_query"):
            candidate = (form.get(key) or "").strip()
            if candidate:
                name = candidate[:60]
                break
        if not name:
            name = "New Channel"

    if editing_slug:
        slug = editing_slug
    elif (form.get("draft_slug") or "").strip():
        slug = (form.get("draft_slug") or "").strip()
    elif not slug:
        slug = _slugify(name) or "draft-channel"

    return {
        "name": name,
        "slug": slug,
        "description": (form.get("description") or "").strip(),
        "icecast_mount": _normalize_mount(form.get("icecast_mount") or slug),
        "enabled": form.get("enabled") == "on",
        "bootstrap_queue": form.get("bootstrap_queue") == "on",
        "queue_target": max(5, min(200, int(form.get("queue_target") or 30))),
        "refresh_threshold": max(1, min(100, int(form.get("refresh_threshold") or 10))),
        "artist_separation_minutes": max(0, int(form.get("artist_separation_minutes") or 90)),
    }


def profile_from_form(form, *, for_deploy: bool = False) -> dict[str, Any]:
    station = _station_identity_from_form(form, for_deploy=for_deploy)
    ptype = (form.get("programming_type") or "clap_query").strip()
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
        centroid_index = _parse_centroid_index(form.get("centroid_index"))
        if not programming["mood"] or centroid_index is None:
            raise ChannelDesignerError("Choose a mood and cluster.")
        programming["centroid_index"] = centroid_index
    elif ptype == "alchemy_anchor":
        programming["anchor_id"] = _resolve_anchor_id_from_form(form)
        if not programming["anchor_id"]:
            hint = (form.get("anchor_search") or "").strip()
            if hint:
                raise ChannelDesignerError(
                    f"No unique Song Alchemy anchor for “{hint}”. "
                    "Click the matching row under the search box, or type the exact anchor name."
                )
            raise ChannelDesignerError(
                "Choose a Song Alchemy anchor — search by name and click a result, "
                "or type the exact anchor name if there is only one match."
            )
    elif ptype == "similar_seed":
        programming["seed_id"] = _resolve_seed_id_from_form(form)
        if not programming["seed_id"]:
            raise ChannelDesignerError(
                "Pick a seed track from search results or paste a track item id."
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
        "station": station,
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
    cur.execute(
        "ALTER TABLE " + channels + " ADD COLUMN IF NOT EXISTS "
        "pending_flash_json TEXT NOT NULL DEFAULT ''"
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
    unfiltered_preview_ids: list[str] | None = None,
    station: dict[str, Any] | None = None,
    action: str = "",
) -> None:
    db = get_db()
    cur = db.cursor()
    channels = table("channels")
    slug = profile["station"]["slug"]
    anchor_id = profile.get("anchor_id")
    if unfiltered_preview_ids:
        profile = dict(profile)
        profile["last_unfiltered_preview_ids"] = [str(i) for i in unfiltered_preview_ids if i]
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


def _channel_slug_from_values(values: dict[str, Any], form: Any | None = None) -> str:
    slug = (values.get("editing_slug") or values.get("slug") or "").strip()
    if not slug and form is not None:
        slug = (form.get("editing_slug") or form.get("slug") or "").strip()
    if not slug and form is not None:
        name = (form.get("name") or "").strip()
        if name:
            slug = _slugify(name)
    return slug


def _try_restore_preview_state(
    values: dict[str, Any],
    form: Any | None = None,
    *,
    reapply_filters: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Restore Preview Results after auxiliary form posts (e.g. artist search)."""
    slug = _channel_slug_from_values(values, form)
    if not slug:
        return [], values
    loaded = _load_saved_channel(slug)
    if not loaded:
        return [], values
    profile, preview_ids = loaded
    unfiltered_ids = profile.get("last_unfiltered_preview_ids") or preview_ids
    if not unfiltered_ids and not preview_ids:
        return [], values

    current_profile = profile
    if reapply_filters and form is not None:
        try:
            current_profile = profile_from_form(form, for_deploy=False)
            current_profile = {
                **profile,
                "filters": current_profile.get("filters") or {},
                "station": {**(profile.get("station") or {}), **(current_profile.get("station") or {})},
            }
        except ChannelDesignerError:
            current_profile = profile

    if reapply_filters and unfiltered_ids:
        unfiltered = _preview_tracks_from_ids(unfiltered_ids)
        preview_tracks = apply_track_filters(unfiltered, current_profile)
        _apply_filter_feedback(values, current_profile, unfiltered, preview_tracks)
    else:
        preview_tracks = _preview_tracks_from_ids(preview_ids)

    saved_values = _form_values_from_profile(profile)
    for key in (
        "clap_query",
        "lyrics_query",
        "chat_prompt",
        "programming_type",
        "name",
        "slug",
        "design_notes",
    ):
        if not (values.get(key) or "").strip() and saved_values.get(key):
            values[key] = saved_values[key]
    if not (values.get("chat_prompt") or "").strip() and saved_values.get("design_notes"):
        values["chat_prompt"] = saved_values["design_notes"]
    return preview_tracks, values


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


def _channel_last_error(slug: str) -> str:
    slug = slug.strip()
    if not slug:
        return ""
    db = get_db()
    cur = db.cursor()
    channels = table("channels")
    try:
        cur.execute(
            "SELECT last_error FROM " + channels + " WHERE slug = %s",
            (slug,),
        )
        row = cur.fetchone()
    except Exception:
        db.rollback()
        return ""
    finally:
        cur.close()
    if not row:
        return ""
    return str(row[0] or "").strip()


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
            _apply_score_metadata(track, scores[0])
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
    role = "alert" if level == "error" else "status"
    return (
        f'<p class="afm-flash {level_class}" role="{role}">'
        f"{html.escape(message)}</p>"
    )


def _pinned_deploy_error_html(message: str) -> str:
    text = (message or "").strip()
    if not text:
        return ""
    return (
        '<div id="afm-deploy-error-pinned" class="afm-deploy-error-pinned" role="alert" '
        'aria-live="assertive">'
        "<strong>Deploy failed — fix this before trying again</strong>"
        f"<p>{html.escape(text)}</p>"
        "</div>"
    )


class _FlashQueue:
    """Contextual status messages keyed by UI anchor (section id)."""

    _ANCHORS = frozenset(
        {
            "global",
            "designer",
            "stations",
            "discover",
            "chat-designer",
            "step-programming",
            "step-preview",
            "step-filters",
            "bootstrap-opener",
            "preview-results",
            "step-deploy",
            "edit-toolbar",
        }
    )

    def __init__(self) -> None:
        self._by_anchor: dict[str, list[str]] = {}

    def add(self, message: str, level: str = "ok", *, anchor: str = "global") -> None:
        if anchor not in self._ANCHORS:
            anchor = "global"
        self._by_anchor.setdefault(anchor, []).append(_flash_html(message, level))

    def html_for(self, anchor: str) -> str:
        return "".join(self._by_anchor.get(anchor, []))

    def to_payload(self) -> dict[str, list[str]]:
        return {anchor: list(items) for anchor, items in self._by_anchor.items() if items}

    def load_payload(self, payload: dict[str, list[str]]) -> None:
        for anchor, items in payload.items():
            if anchor not in self._ANCHORS or not items:
                continue
            self._by_anchor.setdefault(anchor, []).extend(items)


def _stash_flashes(slug: str, flashes: _FlashQueue) -> None:
    """Persist flashes for the next GET of this channel across the post-action redirect.

    Not a Flask session: AudioMuse's host app doesn't reliably issue session
    cookies for plugin routes, so a cookie-based stash silently loses messages
    (including bootstrap-failure warnings) on some deployments. Keying by slug
    in the channels table survives the redirect regardless of cookie support.
    """
    slug = (slug or "").strip()
    payload = flashes.to_payload()
    if not slug or not payload:
        return
    try:
        db = get_db()
        cur = db.cursor()
        channels = table("channels")
        cur.execute(
            "INSERT INTO " + channels + " (name, slug, profile_json, pending_flash_json) "
            "VALUES (%s, %s, '{}', %s) "
            "ON CONFLICT (slug) DO UPDATE SET pending_flash_json = EXCLUDED.pending_flash_json",
            (slug, slug, json.dumps(payload)),
        )
        db.commit()
        cur.close()
    except Exception:
        logger.exception("alchemy_fm_bridge failed to stash flashes for slug=%s", slug)


def _restore_stashed_flashes(slug: str, flashes: _FlashQueue) -> None:
    slug = (slug or "").strip()
    if not slug:
        return
    try:
        db = get_db()
        cur = db.cursor()
        channels = table("channels")
        cur.execute(
            "SELECT pending_flash_json FROM " + channels + " WHERE slug = %s",
            (slug,),
        )
        row = cur.fetchone()
        raw = row[0] if row else ""
        if raw:
            cur.execute(
                "UPDATE " + channels + " SET pending_flash_json = '' WHERE slug = %s",
                (slug,),
            )
            db.commit()
        cur.close()
    except Exception:
        logger.exception("alchemy_fm_bridge failed to restore flashes for slug=%s", slug)
        return
    if not raw:
        return
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return
    if isinstance(payload, dict):
        flashes.load_payload(payload)


def _action_loading_html(
    element_id: str,
    *,
    title: str,
    detail: str,
) -> str:
    return (
        f'<div id="{html.escape(element_id)}" class="afm-action-loading" hidden role="status" aria-live="polite">'
        '<span class="afm-action-loading-spinner" aria-hidden="true"></span>'
        '<span class="afm-action-loading-copy">'
        f'<strong class="afm-action-loading-title">{html.escape(title)}</strong>'
        f'<span class="afm-action-loading-detail">{html.escape(detail)}</span>'
        "</span></div>"
    )


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


def _designer_flow_overview_html(*, flash_html: str = "") -> str:
    return (
        '<section class="afm-panel afm-flow-overview-panel">'
        + _panel_heading(
            "How to Build a Station",
            "Follow the Steps Below — Only Step 2 Is Required Before Preview.",
        )
        + flash_html
        + "<ol class='afm-flow-steps'>"
        "<li><strong>Name It</strong> — What listeners see (mount, homepage).</li>"
        "<li><strong>Program It</strong> — How AudioMuse finds music (CLAP, lyrics, mood, etc.). "
        "This is re-queried when the queue needs more tracks.</li>"
        "<li><strong>Preview It</strong> — Sanity-check tracks before anything goes on air.</li>"
        "<li><strong>Optional Extras</strong> — Filters, opener playlist, living pool (skip on first try).</li>"
        "<li><strong>Playback Rules</strong> — What happens when the pool runs low.</li>"
        "<li><strong>Deploy</strong> — Creates or updates the station on Alchemy FM.</li>"
        "</ol>"
        "<p class='hint'>Chat Designer and Discover Channels (collapsed helpers below) prefill Step 2 only — nothing deploys until Step 6.</p>"
        "</section>"
    )


def _page_header_html() -> str:
    return (
        '<header class="afm-page-header">'
        f'<a href="{html.escape(HELP_DOC_URL)}" class="afm-help-link" target="_blank" '
        'rel="noopener noreferrer">Channel Designer Help</a>'
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
/* Light mode only — bridge AudioMuse theme tokens; dark mode rules unchanged */
html:not(.dark-mode) .afm-shell {
  --text: var(--text-main, #1f2937);
  --muted: var(--text-muted, #4b5563);
  --border: var(--border-color, #e5e7eb);
  --field: var(--bg-input, #ffffff);
  --bg: var(--bg-card, #ffffff);
  --accent: var(--color-primary, #2563eb);
}
html:not(.dark-mode) .afm-shell .afm-panel,
html:not(.dark-mode) .afm-shell .afm-table-wrap,
html:not(.dark-mode) .afm-shell .afm-edit-bar {
  background: var(--bg-card, #ffffff);
}
html:not(.dark-mode) .afm-shell .afm-flash-ok {
  color: #166534;
  background: color-mix(in srgb, var(--color-success, #16a34a) 12%, #ffffff);
  border-color: color-mix(in srgb, var(--color-success, #16a34a) 35%, #e5e7eb);
}
html:not(.dark-mode) .afm-shell .afm-flash-error {
  color: #991b1b;
  background: color-mix(in srgb, var(--color-danger, #dc2626) 10%, #ffffff);
  border-color: color-mix(in srgb, var(--color-danger, #dc2626) 32%, #e5e7eb);
}
html:not(.dark-mode) .afm-shell .afm-action-loading {
  background: color-mix(in srgb, var(--accent, #6366f1) 10%, #ffffff);
  border-color: color-mix(in srgb, var(--accent, #6366f1) 38%, #e5e7eb);
}
html:not(.dark-mode) .afm-shell .afm-action-loading-detail {
  color: var(--color-text-muted, #6b7280);
}
html:not(.dark-mode) .afm-shell .afm-badge-live {
  color: #15803d;
  background: color-mix(in srgb, var(--color-success, #16a34a) 14%, #ffffff);
  border-color: color-mix(in srgb, var(--color-success, #16a34a) 28%, #e5e7eb);
}
html:not(.dark-mode) .afm-shell .afm-badge-off {
  color: var(--text-muted, #4b5563);
  background: color-mix(in srgb, var(--text-muted, #4b5563) 10%, #ffffff);
  border-color: var(--border-color, #e5e7eb);
}
html:not(.dark-mode) .afm-shell .afm-badge-queue {
  color: #1d4ed8;
  background: color-mix(in srgb, var(--color-primary, #2563eb) 12%, #ffffff);
  border-color: color-mix(in srgb, var(--color-primary, #2563eb) 24%, #e5e7eb);
}
html:not(.dark-mode) .afm-shell .afm-badge-saved {
  color: #6d28d9;
  background: color-mix(in srgb, #8b5cf6 12%, #ffffff);
  border-color: color-mix(in srgb, #8b5cf6 24%, #e5e7eb);
}
html:not(.dark-mode) .afm-shell .afm-badge-remote {
  color: #c2410c;
  background: color-mix(in srgb, #f97316 12%, #ffffff);
  border-color: color-mix(in srgb, #f97316 24%, #e5e7eb);
}
html:not(.dark-mode) .afm-shell .afm-badge-living {
  color: #0e7490;
  background: color-mix(in srgb, #06b6d4 12%, #ffffff);
  border-color: color-mix(in srgb, #06b6d4 24%, #e5e7eb);
}
html:not(.dark-mode) .afm-shell .afm-step-badge {
  color: #1d4ed8;
  background: color-mix(in srgb, var(--color-primary, #2563eb) 12%, #ffffff);
  border-color: color-mix(in srgb, var(--color-primary, #2563eb) 24%, #e5e7eb);
}
html:not(.dark-mode) .afm-shell .afm-step-badge-optional,
html:not(.dark-mode) .afm-shell .afm-helper-badge {
  color: #475569;
  background: color-mix(in srgb, #64748b 10%, #ffffff);
  border-color: color-mix(in srgb, #64748b 22%, #e5e7eb);
}
html:not(.dark-mode) .afm-shell .afm-btn-danger {
  color: #b91c1c;
  border-color: color-mix(in srgb, var(--color-danger, #dc2626) 40%, #e5e7eb);
}
html:not(.dark-mode) .afm-shell .afm-btn-danger:hover {
  color: #991b1b;
  background: color-mix(in srgb, var(--color-danger, #dc2626) 10%, #ffffff);
  border-color: color-mix(in srgb, var(--color-danger, #dc2626) 55%, #e5e7eb);
  box-shadow: 0 2px 10px color-mix(in srgb, var(--color-danger, #dc2626) 18%, transparent);
}
html:not(.dark-mode) .afm-shell .afm-btn:hover {
  box-shadow: 0 2px 10px rgba(15, 23, 42, 0.08);
}
html:not(.dark-mode) .afm-shell .afm-select-menu {
  box-shadow: 0 10px 28px rgba(15, 23, 42, 0.12);
}
html:not(.dark-mode) .afm-shell .afm-select-option:hover,
html:not(.dark-mode) .afm-shell .afm-select-option.is-selected {
  background: color-mix(in srgb, var(--color-primary, #2563eb) 12%, #ffffff);
  color: var(--text-main, #1f2937);
}
html:not(.dark-mode) .afm-shell .afm-filter-term-ok { color: #15803d; }
html:not(.dark-mode) .afm-shell .afm-filter-term-warn,
html:not(.dark-mode) .afm-shell .afm-mood-inline-hint { color: #c2410c; }
html:not(.dark-mode) .afm-shell .afm-collapsible-explainer,
html:not(.dark-mode) .afm-shell .afm-filter-feedback {
  background: color-mix(in srgb, var(--color-primary, #2563eb) 6%, #ffffff);
}
.afm-page-header {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  margin: 0 0 1.35rem;
  padding-bottom: 0.85rem;
  border-bottom: 1px solid var(--border, rgba(255, 255, 255, 0.1));
}
.afm-help-link {
  font-size: 1.02rem;
  font-weight: 600;
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
.afm-panel .afm-flash,
.afm-edit-bar .afm-flash,
.afm-preview-results-body > .afm-flash:first-child {
  margin-top: 0;
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
.afm-picker-selected {
  margin: 0.5rem 0 0;
  padding: 0.55rem 0.75rem;
  border-radius: 8px;
  background: color-mix(in srgb, #22c55e 12%, transparent);
  border: 1px solid color-mix(in srgb, #22c55e 40%, transparent);
}
.afm-picker-selected[hidden] { display: none !important; }
.afm-picker-clear {
  margin-left: 0.5rem;
  padding: 0.2rem 0.55rem;
  font-size: 0.8rem;
}
.afm-step2-status {
  margin: 0 0 1rem;
  padding: 0.75rem 1rem;
  border-radius: 10px;
  border: 1px solid color-mix(in srgb, var(--border, rgba(255,255,255,0.2)) 80%, transparent);
  background: color-mix(in srgb, var(--accent, #6366f1) 8%, transparent);
}
.afm-step2-status .afm-step2-current { margin: 0 0 0.35rem; }
.afm-step2-status .hint { margin: 0.25rem 0 0; }
.afm-step2-status .afm-step2-warn { margin: 0.5rem 0 0; }
.afm-deploy-readiness {
  margin: 0 0 1rem;
  padding: 0.85rem 1rem;
  border-radius: 10px;
  border: 1px solid color-mix(in srgb, var(--border, rgba(255,255,255,0.2)) 80%, transparent);
  line-height: 1.45;
}
.afm-deploy-readiness ul {
  margin: 0.35rem 0 0;
  padding-left: 1.2rem;
}
.afm-deploy-readiness li + li { margin-top: 0.25rem; }
.afm-deploy-readiness-ok {
  background: color-mix(in srgb, #22c55e 12%, transparent);
  border-color: color-mix(in srgb, #22c55e 45%, transparent);
}
.afm-deploy-readiness-blocked {
  background: color-mix(in srgb, #ef4444 14%, transparent);
  border: 1px solid color-mix(in srgb, #ef4444 45%, transparent);
  border-radius: 8px;
  padding: 0.65rem 0.75rem;
  margin-bottom: 0.5rem;
}
.afm-deploy-readiness-warn {
  background: color-mix(in srgb, #f59e0b 12%, transparent);
  border: 1px solid color-mix(in srgb, #f59e0b 40%, transparent);
  border-radius: 8px;
  padding: 0.65rem 0.75rem;
}
.afm-btn[disabled][data-deploy-blocked] {
  opacity: 0.55;
  cursor: not-allowed;
}
.afm-deploy-error-pinned {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 1000;
  margin: 0;
  padding: 0.85rem 1.25rem;
  background: color-mix(in srgb, #ef4444 92%, #1a1a1a);
  border-bottom: 2px solid #fca5a5;
  color: #fff;
  box-shadow: 0 6px 28px rgba(0, 0, 0, 0.35);
  line-height: 1.45;
}
.afm-deploy-error-pinned strong {
  display: block;
  font-size: 0.95rem;
  margin-bottom: 0.35rem;
}
.afm-deploy-error-pinned p {
  margin: 0;
  font-size: 0.9rem;
  white-space: pre-wrap;
  word-break: break-word;
}
.afm-shell.has-deploy-error-pinned {
  padding-top: 4.5rem;
}
.afm-deploy-status,
.afm-preview-status {
  position: sticky;
  top: 0.75rem;
  z-index: 5;
  margin: 0 0 1rem;
}
.afm-deploy-status[hidden],
.afm-preview-status[hidden] { display: none !important; }
.afm-deploy-status .afm-flash,
.afm-preview-status .afm-flash {
  box-shadow: 0 4px 24px rgba(0, 0, 0, 0.22);
}
.afm-deploy-status .afm-flash + .afm-flash,
.afm-preview-status .afm-flash + .afm-flash {
  margin-top: 0.5rem;
}
.afm-action-loading {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  padding: 0.9rem 1rem;
  margin: 0 0 1rem;
  border-radius: 10px;
  border: 1px solid color-mix(in srgb, var(--accent, #6366f1) 50%, transparent);
  background: color-mix(in srgb, var(--accent, #6366f1) 16%, transparent);
  color: var(--text, inherit);
  line-height: 1.45;
}
.afm-action-loading[hidden] { display: none !important; }
.afm-action-loading-copy {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  min-width: 0;
}
.afm-action-loading-title {
  font-size: 0.95rem;
  font-weight: 600;
}
.afm-action-loading-detail {
  font-size: 0.86rem;
  color: var(--muted, #94a3b8);
}
.afm-action-loading-spinner {
  flex: 0 0 auto;
  width: 1.1rem;
  height: 1.1rem;
  margin-top: 0.1rem;
  border: 2px solid color-mix(in srgb, var(--accent, #6366f1) 35%, transparent);
  border-top-color: var(--accent, #6366f1);
  border-radius: 50%;
  animation: afm-spin 0.75s linear infinite;
}
@keyframes afm-spin {
  to { transform: rotate(360deg); }
}
.afm-btn.is-loading {
  pointer-events: none;
  opacity: 0.82;
}
.afm-btn.is-loading::before {
  content: "";
  width: 0.85rem;
  height: 0.85rem;
  border: 2px solid currentColor;
  border-right-color: transparent;
  border-radius: 50%;
  animation: afm-spin 0.75s linear infinite;
}
.afm-panel.is-working,
.afm-collapsible-helper.is-working {
  border-color: color-mix(in srgb, var(--accent, #6366f1) 55%, transparent);
  animation: afm-panel-pulse 1.6s ease-in-out infinite;
}
.afm-shell.is-busy {
  cursor: progress;
}
.afm-shell.is-busy .afm-designer-form input,
.afm-shell.is-busy .afm-designer-form textarea,
.afm-shell.is-busy .afm-designer-form select,
.afm-shell.is-busy .afm-designer-form button:not([data-afm-ajax-preview]) {
  pointer-events: none;
  opacity: 0.72;
}
#afm-chat-error[hidden] { display: none !important; }
@keyframes afm-panel-pulse {
  0%, 100% {
    box-shadow: 0 0 0 1px color-mix(in srgb, var(--accent, #6366f1) 22%, transparent);
  }
  50% {
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent, #6366f1) 34%, transparent);
  }
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
.afm-table tbody tr[hidden] { display: none; }
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
.afm-sort-btn {
  background: none;
  border: none;
  padding: 0;
  margin: 0;
  font: inherit;
  font-weight: 600;
  color: inherit;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
}
.afm-sort-btn:hover,
.afm-sort-btn:focus-visible {
  color: var(--text, inherit);
  outline: none;
}
.afm-sort-btn::after {
  content: '↕';
  opacity: 0.35;
  font-size: 0.72em;
  line-height: 1;
}
.afm-sort-btn[aria-sort='ascending']::after {
  content: '▲';
  opacity: 0.9;
}
.afm-sort-btn[aria-sort='descending']::after {
  content: '▼';
  opacity: 0.9;
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
#step-programming,
#step-preview,
#step-filters,
#step-deploy,
#preview-results,
#bootstrap-opener,
#chat-designer,
#discover,
#stations,
#designer,
.afm-preview-results-panel {
  scroll-margin-top: 1rem;
}
.afm-jump-nav {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin: 0 0 0.85rem;
}
.afm-jump-btn {
  font-size: 0.84rem;
  padding: 0.38rem 0.7rem;
}
.afm-preview-results-jump {
  margin-left: auto;
  flex: 0 0 auto;
}
.afm-preview-results-panel > summary .afm-preview-results-jump {
  margin-left: 0;
}
@media (min-width: 640px) {
  .afm-preview-results-panel > summary .afm-preview-results-jump {
    margin-left: auto;
  }
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
.afm-programming-panel .afm-type-field[hidden] {
  display: none !important;
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
.afm-seed-search-field { position: relative; }
.afm-seed-track-results { margin-top: 0.4rem; }
.afm-seed-track-results-label {
  margin: 0 0 0.35rem;
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--muted, #94a3b8);
}
.afm-seed-track-results-empty {
  margin: 0.45rem 0 0;
  font-size: 0.84rem;
  line-height: 1.5;
  color: var(--muted, #94a3b8);
}
.afm-seed-track-menu {
  list-style: none;
  margin: 0;
  padding: 0.3rem;
  max-height: 14rem;
  overflow-y: auto;
  border-radius: 8px;
  border: 1px solid var(--border, rgba(255, 255, 255, 0.14));
  background: var(--bg, #0f172a);
  box-shadow: 0 8px 22px rgba(0, 0, 0, 0.28);
}
html:not(.dark-mode) .afm-shell .afm-seed-track-menu {
  background: var(--bg-card, #ffffff);
  box-shadow: 0 8px 22px rgba(15, 23, 42, 0.1);
}
.afm-seed-track-pick {
  display: block;
  width: 100%;
  box-sizing: border-box;
  text-align: left;
  padding: 0.5rem 0.65rem;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text, inherit);
  font: inherit;
  line-height: 1.35;
  cursor: pointer;
}
.afm-seed-track-pick:hover,
.afm-seed-track-pick:focus-visible,
.afm-seed-track-pick.is-active {
  background: color-mix(in srgb, var(--accent, #6366f1) 16%, transparent);
  outline: none;
}
.afm-bootstrap-search-field { position: relative; }
.afm-bootstrap-results { margin-top: 0.4rem; }
.afm-bootstrap-results-label {
  margin: 0 0 0.35rem;
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--muted, #94a3b8);
}
.afm-bootstrap-results-empty {
  margin: 0.45rem 0 0;
  font-size: 0.84rem;
  line-height: 1.5;
  color: var(--muted, #94a3b8);
}
.afm-bootstrap-menu {
  list-style: none;
  margin: 0;
  padding: 0.3rem;
  max-height: 14rem;
  overflow-y: auto;
  border-radius: 8px;
  border: 1px solid var(--border, rgba(255, 255, 255, 0.14));
  background: var(--bg, #0f172a);
  box-shadow: 0 8px 22px rgba(0, 0, 0, 0.28);
}
html:not(.dark-mode) .afm-shell .afm-bootstrap-menu {
  background: var(--bg-card, #ffffff);
  box-shadow: 0 8px 22px rgba(15, 23, 42, 0.1);
}
.afm-bootstrap-pick {
  display: block;
  width: 100%;
  box-sizing: border-box;
  text-align: left;
  padding: 0.5rem 0.65rem;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text, inherit);
  font: inherit;
  line-height: 1.35;
  cursor: pointer;
}
.afm-bootstrap-pick:hover,
.afm-bootstrap-pick:focus-visible,
.afm-bootstrap-pick.is-active {
  background: color-mix(in srgb, var(--accent, #6366f1) 16%, transparent);
  outline: none;
}
.afm-artist-picker {
  position: relative;
  margin-top: 0.5rem;
}
.afm-artist-suggestions {
  position: absolute;
  z-index: 50;
  top: calc(100% + 0.35rem);
  left: 0;
  right: 0;
  list-style: none;
  margin: 0;
  padding: 0.3rem;
  max-height: 12rem;
  overflow-y: auto;
  border-radius: 8px;
  border: 1px solid var(--border, rgba(255, 255, 255, 0.14));
  background: var(--bg, #0f172a);
  box-shadow: 0 8px 22px rgba(0, 0, 0, 0.28);
}
.afm-artist-suggestions[hidden] { display: none !important; }
html:not(.dark-mode) .afm-shell .afm-artist-suggestions {
  background: var(--bg-card, #ffffff);
  box-shadow: 0 8px 22px rgba(15, 23, 42, 0.1);
}
.afm-artist-suggestion {
  display: block;
  width: 100%;
  box-sizing: border-box;
  text-align: left;
  padding: 0.5rem 0.65rem;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text, inherit);
  font: inherit;
  line-height: 1.35;
  cursor: pointer;
}
.afm-artist-suggestion:hover,
.afm-artist-suggestion:focus-visible,
.afm-artist-suggestion.is-active {
  background: color-mix(in srgb, var(--accent, #6366f1) 16%, transparent);
  outline: none;
}
#bootstrap-opener { scroll-margin-top: 1rem; }
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
    except ChannelDesignerError as exc:
        logger.warning("Could not load mood centroids for Step 2: %s", exc)
        return {}


def _mood_centroids_step2_warning() -> str:
    try:
        audiomuse_get("/api/mood_centroids", timeout=10)
    except ChannelDesignerError as exc:
        if exc.status == 401:
            return (
                "Mood clusters could not load (AudioMuse 401). Set "
                "<strong>audiomuse_api_token</strong> in plugin settings."
            )
        return f"Mood clusters could not load: {exc}"
    return ""


def _programming_signature(programming: dict[str, Any]) -> tuple[Any, ...]:
    ptype = (programming.get("type") or "").strip()
    if ptype in ("clap_query", "lyrics_query"):
        return (ptype, (programming.get("query") or "").strip())
    if ptype == "mood_centroid":
        return (
            ptype,
            (programming.get("mood") or "").strip().lower(),
            str(_parse_centroid_index(programming.get("centroid_index"))),
        )
    if ptype == "alchemy_anchor":
        return (ptype, str(programming.get("anchor_id") or "").strip())
    if ptype == "similar_seed":
        return (ptype, str(programming.get("seed_id") or "").strip())
    return (ptype,)


def _programming_changed(current: dict[str, Any], saved: dict[str, Any]) -> bool:
    current_prog = current.get("programming") if isinstance(current.get("programming"), dict) else {}
    saved_prog = saved.get("programming") if isinstance(saved.get("programming"), dict) else {}
    return _programming_signature(current_prog) != _programming_signature(saved_prog)


def _deploy_unfiltered_tracks(profile: dict[str, Any], slug: str) -> list[dict[str, Any]]:
    """Tracks for deploy — prefer last successful preview when programming unchanged."""
    slug = slug.strip()
    loaded = _load_saved_channel(slug)
    if loaded:
        saved_profile, preview_ids = loaded
        if not _programming_changed(profile, saved_profile):
            fallback_ids = saved_profile.get("last_unfiltered_preview_ids") or preview_ids
            if fallback_ids:
                tracks = _preview_tracks_from_ids(fallback_ids)
                if tracks:
                    return tracks
    try:
        return _merged_programming_tracks_unfiltered(profile, slug)
    except ChannelDesignerError:
        if not loaded:
            raise
        saved_profile, preview_ids = loaded
        fallback_ids = saved_profile.get("last_unfiltered_preview_ids") or preview_ids
        if fallback_ids:
            tracks = _preview_tracks_from_ids(fallback_ids)
            if tracks:
                return tracks
        raise


def _profile_from_values(values: dict[str, Any]) -> dict[str, Any]:
    """Build a channel profile dict from designer form values (no POST required)."""
    ptype = (values.get("programming_type") or "clap_query").strip()
    programming: dict[str, Any] = {
        "type": ptype,
        "limit": int(values.get("preview_limit") or PREVIEW_LIMIT_DEFAULT),
    }
    if ptype in ("clap_query", "lyrics_query"):
        programming["query"] = (values.get("clap_query") or values.get("lyrics_query") or "").strip()
    elif ptype == "mood_centroid":
        programming["mood"] = (values.get("mood_name") or "").strip().lower()
        programming["centroid_index"] = _parse_centroid_index(values.get("centroid_index"))
    elif ptype == "alchemy_anchor":
        programming["anchor_id"] = _resolve_anchor_id_from_form(values)
    elif ptype == "similar_seed":
        programming["seed_id"] = (values.get("seed_id") or "").strip()
        if not programming["seed_id"]:
            programming["seed_id"] = _resolve_seed_id_from_form(values)
    slug = (
        (values.get("editing_slug") or values.get("slug") or values.get("draft_slug") or "").strip()
        or _slugify(values.get("name") or "")
        or "draft-channel"
    )
    profile: dict[str, Any] = {
        "programming": programming,
        "refresh": {"mode": (values.get("refresh_mode") or "similar_to_last").strip()},
        "filters": {
            "tempo_min": _parse_optional_float(values.get("filter_tempo_min")),
            "tempo_max": _parse_optional_float(values.get("filter_tempo_max")),
            "energy_min": _parse_optional_float(values.get("filter_energy_min")),
            "energy_max": _parse_optional_float(values.get("filter_energy_max")),
            "year_min": _parse_optional_int(values.get("filter_year_min")),
            "year_max": _parse_optional_int(values.get("filter_year_max")),
            "genre_include": _parse_csv_list(values.get("filter_genre_include")),
            "genre_exclude": _parse_csv_list(values.get("filter_genre_exclude")),
            "mood_include": _parse_csv_list(values.get("filter_mood_include")),
            "exclude_artists": _parse_csv_list(values.get("filter_exclude_artists")),
        },
        "living": {
            "enabled": bool(values.get("living_enabled")),
            "auto_add_on_analyze": bool(values.get("living_auto_add")),
            "auto_refresh_alchemy": bool(values.get("living_auto_refresh")),
        },
        "station": {
            "name": (values.get("name") or "").strip(),
            "slug": slug,
        },
    }
    if values.get("bootstrap_enabled"):
        playlist_id = (values.get("bootstrap_playlist_id") or "").strip()
        if playlist_id:
            profile["bootstrap"] = {
                "type": "navidrome_playlist",
                "playlist_id": playlist_id,
                "track_limit": int(values.get("bootstrap_track_limit") or BOOTSTRAP_TRACK_LIMIT_DEFAULT),
            }
    return profile


def _deploy_readiness(values: dict[str, Any]) -> dict[str, Any]:
    """Pre-deploy checks mirroring the push path — shown in Step 6 before Deploy."""
    blockers: list[str] = []
    warnings: list[str] = []
    slug = _channel_slug_from_values(values)
    profile = _profile_from_values(values)
    if not _text((profile.get("station") or {}).get("name")):
        blockers.append("Step 1: Channel name is required before deploy.")

    programming = profile.get("programming") or {}
    merged = _merge_saved_programming_if_needed(profile, slug) if slug else profile
    if _programming_incomplete(programming):
        loaded = _load_saved_channel(slug) if slug else None
        if not loaded or _programming_changed(merged, loaded[0]):
            blockers.append(
                "Step 2: Programming is incomplete in the form. Fill the active programming fields "
                "or run Step 3 Preview (which saves programming)."
            )

    saved_preview_count = 0
    loaded = _load_saved_channel(slug) if slug else None
    if loaded:
        saved_profile, preview_ids = loaded
        saved_preview_count = len(
            saved_profile.get("last_unfiltered_preview_ids") or preview_ids or []
        )

    track_count = 0
    unfiltered_count = 0
    if not blockers:
        preview_tracks = _preview_tracks_for_values(values)
        track_count = len(preview_tracks)
        try:
            unfiltered = _deploy_unfiltered_tracks(merged, slug or "")
            unfiltered_count = len(unfiltered)
        except ChannelDesignerError as exc:
            if track_count == 0:
                blockers.append(str(exc))
            else:
                unfiltered_count = track_count
        if track_count == 0 and not blockers:
            if _filters_active(merged.get("filters") or {}) and unfiltered_count > 0:
                blockers.append(
                    f"Step 4 filters removed all {unfiltered_count} track(s). Broaden filters or clear them, "
                    "then run Step 3 Preview again."
                )
            elif saved_preview_count == 0:
                blockers.append(
                    "No tracks to deploy. Run Step 3 Preview Programming first (shows track list below)."
                )
            else:
                blockers.append(
                    "No tracks to deploy after applying current programming and filters. "
                    "Run Step 3 Preview again."
                )
        elif unfiltered_count > track_count and _filters_active(merged.get("filters") or {}):
            warnings.append(
                f"Step 4 filters will deploy {track_count} of {unfiltered_count} programming track(s)."
            )

    bootstrap_check = values.get("bootstrap_check")
    if values.get("bootstrap_enabled") and (values.get("bootstrap_playlist_id") or "").strip():
        if isinstance(bootstrap_check, dict) and not bootstrap_check.get("ok"):
            err = bootstrap_check.get("error") or "Bootstrap playlist could not be verified."
            warnings.append(
                f"Optional bootstrap opener: {err} Deploy will continue without the opener playlist."
            )
        elif not bootstrap_check:
            warnings.append(
                "Optional bootstrap opener: pick a Navidrome playlist and wait for the green verify check, "
                "or uncheck Use Navidrome Playlist Opener."
            )

    if saved_preview_count > 0 and track_count > 0:
        warnings.append(f"Ready to deploy {track_count} track(s) (last preview saved {saved_preview_count}).")

    return {
        "ok": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "track_count": track_count,
    }


def _preview_tracks_for_values(values: dict[str, Any]) -> list[dict[str, Any]]:
    """Tracks for preview table / deploy readiness — same source, same count."""
    slug = _channel_slug_from_values(values)
    profile = _profile_from_values(values)
    programming = profile.get("programming") or {}
    if _programming_incomplete(programming):
        return []
    merged = _merge_saved_programming_if_needed(profile, slug) if slug else profile
    loaded = _load_saved_channel(slug) if slug else None
    if loaded:
        saved_profile, preview_ids = loaded
        fallback_ids = saved_profile.get("last_unfiltered_preview_ids") or preview_ids
        if fallback_ids:
            unfiltered = _preview_tracks_from_ids(fallback_ids)
            tracks = apply_track_filters(unfiltered, merged)
            if tracks:
                return tracks
    try:
        unfiltered = _deploy_unfiltered_tracks(merged, slug or "")
        return apply_track_filters(unfiltered, merged)
    except ChannelDesignerError:
        return []


def _deploy_readiness_html(readiness: dict[str, Any]) -> str:
    blockers = readiness.get("blockers") or []
    warnings = readiness.get("warnings") or []
    if not blockers and not warnings:
        count = int(readiness.get("track_count") or 0)
        if count > 0:
            return (
                '<div id="afm-deploy-readiness" class="afm-deploy-readiness afm-deploy-readiness-ok" role="status">'
                f"<strong>Ready to deploy</strong>"
                f"<p>{count} track(s) will be sent to Alchemy FM.</p>"
                "</div>"
            )
        return (
            '<div id="afm-deploy-readiness" class="afm-deploy-readiness afm-deploy-readiness-warn" role="status">'
            "<strong>Before you deploy</strong>"
            "<p>Run Step 3 Preview Programming to load tracks into this channel.</p>"
            "</div>"
        )
    parts: list[str] = ['<div id="afm-deploy-readiness" class="afm-deploy-readiness" role="status">']
    if blockers:
        parts.append('<div class="afm-deploy-readiness-blocked">')
        parts.append("<strong>Deploy blocked</strong><ul>")
        for msg in blockers:
            parts.append(f"<li>{html.escape(msg)}</li>")
        parts.append("</ul></div>")
    if warnings:
        parts.append('<div class="afm-deploy-readiness-warn">')
        parts.append("<strong>Notes</strong><ul>")
        for msg in warnings:
            parts.append(f"<li>{html.escape(msg)}</li>")
        parts.append("</ul></div>")
    parts.append("</div>")
    return "".join(parts)


def _programming_incomplete(programming: dict[str, Any]) -> bool:
    ptype = programming.get("type", "clap_query")
    if ptype in ("clap_query", "lyrics_query"):
        return len((programming.get("query") or "").strip()) < 3
    if ptype == "mood_centroid":
        return not programming.get("mood") or _parse_centroid_index(programming.get("centroid_index")) is None
    if ptype == "alchemy_anchor":
        return not str(programming.get("anchor_id") or "").strip()
    if ptype == "similar_seed":
        return not str(programming.get("seed_id") or "").strip()
    return False


def _merge_saved_programming_if_needed(profile: dict[str, Any], slug: str) -> dict[str, Any]:
    programming = profile.get("programming") or {}
    if not _programming_incomplete(programming):
        return profile
    loaded = _load_saved_channel(slug.strip())
    if not loaded:
        return profile
    saved_profile, _ = loaded
    saved_programming = saved_profile.get("programming")
    if not isinstance(saved_programming, dict) or _programming_incomplete(saved_programming):
        return profile
    merged = dict(profile)
    merged["programming"] = saved_programming
    return merged


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


def _sortable_th(label: str, key: str, *, sort_type: str = "text") -> str:
    return (
        f'<th scope="col"><button type="button" class="afm-sort-btn" '
        f'data-sort-key="{html.escape(key)}" data-sort-type="{sort_type}" '
        f'aria-sort="none">{html.escape(label)}</button></th>'
    )


def _preview_table_html(tracks: list[dict[str, Any]]) -> str:
    if not tracks:
        return "<p class='hint'>No tracks matched this programming yet. Run a preview.</p>"
    rows = []
    for track in tracks:
        tempo = track.get("tempo")
        energy = track.get("energy")
        tempo_s = f"{float(tempo):.0f}" if tempo is not None else "—"
        energy_s = f"{float(energy):.2f}" if energy is not None else "—"
        title_sort = str(track.get("title") or "").strip().lower()
        author_sort = str(track.get("author") or "").strip().lower()
        genre_sort = str(track.get("top_genre") or "").strip().lower()
        mood_sort = str(track.get("mood") or "").strip().lower()
        tempo_sort = f"{float(tempo):.4f}" if tempo is not None else ""
        energy_sort = f"{float(energy):.6f}" if energy is not None else ""
        rows.append(
            "<tr"
            f' data-sort-title="{html.escape(title_sort)}"'
            f' data-sort-artist="{html.escape(author_sort)}"'
            f' data-sort-genre="{html.escape(genre_sort)}"'
            f' data-sort-tempo="{html.escape(tempo_sort)}"'
            f' data-sort-energy="{html.escape(energy_sort)}"'
            f' data-sort-mood="{html.escape(mood_sort)}"'
            ">"
            f"<td>{html.escape(track['title'])}</td>"
            f"<td>{html.escape(track['author'])}</td>"
            f"<td>{html.escape(str(track.get('top_genre') or '—'))}</td>"
            f"<td>{tempo_s}</td>"
            f"<td>{energy_s}</td>"
            f"<td>{html.escape(track.get('mood') or '—')}</td>"
            "</tr>"
        )
    return (
        f"<p><strong>{len(tracks)}</strong> tracks in preview (from your AudioMuse library analysis). "
        "Click a column header to sort.</p>"
        '<div class="afm-table-wrap"><table class="afm-table afm-sortable-table">'
        "<thead><tr>"
        + _sortable_th("Title", "title")
        + _sortable_th("Artist", "artist")
        + _sortable_th("Genre", "genre")
        + _sortable_th("BPM", "tempo", sort_type="number")
        + _sortable_th("Energy", "energy", sort_type="number")
        + _sortable_th("Mood", "mood")
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def _jump_nav_button(label: str, *, target_id: str, direction: str = "down") -> str:
    arrow = "↓" if direction == "down" else "↑"
    return (
        f'<button type="button" class="afm-btn afm-btn-secondary afm-jump-btn" '
        f'data-afm-jump="{html.escape(target_id)}">'
        f"{html.escape(label)} {arrow}</button>"
    )


def _preview_results_html(
    preview_tracks: list[dict[str, Any]],
    *,
    highlight: bool = False,
    flash_html: str = "",
) -> str:
    count = len(preview_tracks)
    open_attr = " open" if count else ""
    highlight_class = " afm-preview-results-highlight" if highlight else ""
    jump_back = ""
    if count:
        jump_back = (
            '<span class="afm-preview-results-jump">'
            + _jump_nav_button("Step 3", target_id="step-preview", direction="up")
            + _jump_nav_button("Filters", target_id="step-filters", direction="up")
            + "</span>"
        )
    if count:
        meta = f"{count} Track(s) From Your Last Step 3 Preview"
        body = (
            '<div class="afm-jump-nav">'
            + _jump_nav_button("Back to Step 3", target_id="step-preview", direction="up")
            + _jump_nav_button("Back to Filters", target_id="step-filters", direction="up")
            + "</div>"
            + _preview_table_html(preview_tracks)
        )
    else:
        meta = "Run Preview Programming in Step 3 to See Tracks Here"
        body = "<p class='hint'>No preview yet. Set programming above, then click <strong>Preview Programming</strong>.</p>"
    return (
        f'<details id="preview-results" class="afm-panel afm-preview-results-panel{highlight_class}"{open_attr}>'
        "<summary>"
        '<span class="afm-preview-results-title">Preview Results</span>'
        f'<span class="afm-preview-results-meta">{html.escape(meta)}</span>'
        f"{jump_back}"
        "</summary>"
        f'<div class="afm-preview-results-body">{flash_html}{body}</div>'
        "</details>"
    )


def _programming_detail_from_values(values: dict[str, Any]) -> str:
    ptype = _text(values.get("programming_type") or "clap_query")
    label = PROGRAMMING_TYPE_LABELS.get(ptype, ptype.replace("_", " ").title())
    if ptype in ("clap_query", "lyrics_query"):
        query = _text(values.get("clap_query" if ptype == "clap_query" else "lyrics_query"))
        if len(query) < 3:
            return f"{label} — not set (need at least 3 characters)"
        return f"{label}: {query[:80]}"
    if ptype == "mood_centroid":
        mood = (values.get("mood_name") or "").strip()
        cluster = values.get("centroid_index")
        if not mood or _parse_centroid_index(cluster) is None:
            return f"{label} — choose mood and cluster"
        return f"{label}: {mood.title()} / cluster {cluster}"
    if ptype == "alchemy_anchor":
        anchor = (values.get("anchor_id") or "").strip()
        if anchor:
            name = anchor
            for item in _anchors():
                if str(item.get("id")) == anchor:
                    name = str(item.get("name") or anchor)
                    break
            return f"{label}: {name} (id {anchor})"
        search = (values.get("anchor_search") or "").strip()
        if search:
            return f"{label} — click a search result for “{search[:40]}” (not selected yet)"
        return f"{label}: pick an anchor from search"
    if ptype == "similar_seed":
        seed = (values.get("seed_id") or "").strip()
        return f"{label}: {seed or 'pick a seed track from search'}"
    return label


def _step2_programming_status_html(values: dict[str, Any]) -> str:
    slug = _channel_slug_from_values(values)
    detail = _programming_detail_from_values(values)
    incomplete = _programming_incomplete(_profile_from_values(values).get("programming") or {})
    parts = [f"<p class='afm-step2-current'><strong>Current:</strong> {html.escape(detail)}</p>"]
    loaded = _load_saved_channel(slug) if slug else None
    if loaded:
        saved_profile, preview_ids = loaded
        saved_prog = saved_profile.get("programming") or {}
        saved_detail = _programming_detail_from_values(_form_values_from_profile(saved_profile))
        count = len(saved_profile.get("last_unfiltered_preview_ids") or preview_ids or [])
        if count:
            parts.append(
                f"<p class='hint afm-step2-saved'>Last preview ({count} tracks): "
                f"{html.escape(saved_detail)}</p>"
            )
        if incomplete and count:
            parts.append(
                "<p class='afm-flash afm-flash-warn afm-step2-warn'>Step 2 fields look empty in the form — "
                "deploy will use your last preview programming.</p>"
            )
        elif not incomplete and loaded and _programming_changed(
            _profile_from_values(values), saved_profile
        ):
            parts.append(
                "<p class='afm-flash afm-flash-warn afm-step2-warn'>Step 2 changed since last preview — "
                "run Step 3 Preview again before deploy.</p>"
            )
    elif incomplete:
        parts.append(
            "<p class='afm-flash afm-flash-error afm-step2-warn'>Step 2 is incomplete. "
            "Fill in the active programming type, then run Step 3 Preview.</p>"
        )
    return (
        '<div id="afm-step2-status" class="afm-step2-status" role="status">'
        + "".join(parts)
        + "</div>"
    )


def _programming_fields_html(
    values: dict[str, Any],
    *,
    track_search_url: str = "",
    anchor_search_url: str = "",
    flash_html: str = "",
) -> str:
    ptype = values.get("programming_type", "clap_query")
    hidden = lambda key: "" if ptype == key else " hidden"
    step2_warning = _mood_centroids_step2_warning() if ptype == "mood_centroid" else ""
    step2_warning_html = (
        f"<p class='afm-flash afm-flash-warn'>{step2_warning}</p>" if step2_warning else ""
    )

    mood_opts, centroid_opts = _mood_options(
        str(values.get("mood_name", "")),
        str(values.get("centroid_index", "")),
    )

    anchor_list = _anchors()
    selected_anchor_id = str(values.get("anchor_id", "")).strip()
    anchor_display = ""
    if selected_anchor_id:
        for anchor in anchor_list:
            if str(anchor.get("id")) == selected_anchor_id:
                anchor_display = str(anchor.get("name") or selected_anchor_id)
                break
        if not anchor_display:
            anchor_display = selected_anchor_id

    anchor_sel_class = "afm-picker-selected is-set" if selected_anchor_id else "afm-picker-selected"
    anchor_sel_hidden = "" if selected_anchor_id else " hidden"

    return (
        "<section class='afm-panel afm-programming-panel afm-step-panel' id='step-programming'>"
        + _step_panel_heading(
            "Step 2",
            "Programming",
            "Defines what music fits this station. Alchemy FM re-runs this query when the queue needs more tracks.",
        )
        + _programming_explainer_html()
        + flash_html
        + step2_warning_html
        + _step2_programming_status_html(values)
        + "<div class='afm-field'>"
        + _field_label("Programming Type", mandatory=True)
        + f"<select name='programming_type' id='programming_type' class='afm-select'>"
        + f"{_select_options(PROGRAMMING_TYPES, ptype)}</select></div>"
        + f"<div id='field-clap' class='afm-field afm-type-field'{hidden('clap_query')}>"
        + _field_label("Sonic Vibe (Describe the Sound)", mandatory=True)
        + f"<input name='clap_query' class='afm-text-input' placeholder='e.g. late night rock' "
        + f"value='{html.escape(str(values.get('clap_query', '')))}'>"
        + "<p class='hint'>Matches how tracks <strong>sound</strong> — not lyrics. For theme or meaning, use "
        "<strong>Lyrics Theme</strong> instead of Sonic Vibe.</p></div>"
        + f"<div id='field-lyrics' class='afm-field afm-type-field'{hidden('lyrics_query')}>"
        + _field_label("Lyrics Theme", mandatory=True)
        + f"<input name='lyrics_query' class='afm-text-input' placeholder='e.g. songs about the open road' "
        + f"value='{html.escape(str(values.get('lyrics_query', '')))}'>"
        + "<p class='hint'>Semantic lyrics search — meaning and themes, not just keywords.</p></div>"
        + f"<div id='field-mood' class='afm-field afm-field-grid afm-type-field'{hidden('mood_centroid')}>"
        + "<div>"
        + _field_label("Mood", mandatory=True)
        + "<select name='mood_name' class='afm-select'>"
        + mood_opts
        + "</select></div>"
        + "<div>"
        + _field_label("Cluster", mandatory=True)
        + "<select name='centroid_index' id='centroid_index' class='afm-select'"
        + f" data-initial-value='{html.escape(str(values.get('centroid_index', '')))}'>"
        + centroid_opts
        + "</select></div>"
        + "<p class='hint' style='grid-column:1/-1;'>Each mood has sub-clusters from your library analysis — pick one that matches "
        + "the vibe (tags show the dominant traits in that cluster).</p></div>"
        + f"<div id='field-anchor' class='afm-field afm-type-field'{hidden('alchemy_anchor')}>"
        + _field_label("Song Alchemy Anchor", mandatory=True)
        + f"<div class='afm-seed-search-field' id='afm-anchor-picker' "
        + f"data-anchor-search-url='{html.escape(anchor_search_url)}'>"
        + f"<input name='anchor_search' id='anchor_search' class='afm-text-input' autocomplete='off' role='combobox' "
        + "aria-expanded='false' aria-controls='afm-anchor-results' "
        + f"placeholder='Anchor name…' value='{html.escape(anchor_display)}'>"
        + f"<input type='hidden' name='anchor_id' id='anchor_id' "
        + f"value='{html.escape(selected_anchor_id)}'>"
        + f"<p id='afm-anchor-selected' class='{anchor_sel_class}'{anchor_sel_hidden}>"
        + "<strong>Selected anchor:</strong> "
        + f"<span id='afm-anchor-selected-name'>{html.escape(anchor_display or selected_anchor_id)}</span> "
        + f"<span class='hint'>(id {html.escape(selected_anchor_id)})</span> "
        + "<button type='button' class='afm-btn afm-btn-secondary afm-picker-clear' "
        + "id='afm-anchor-clear'>Change</button></p>"
        + "<div id='afm-anchor-results' class='afm-seed-track-results'></div>"
        + "</div>"
        + "<p class='hint'>Song Alchemy anchors appear as you type. Pick one to set the station vibe source.</p></div>"
        + f"<div id='field-seed' class='afm-field afm-seed-field afm-type-field'{hidden('similar_seed')}>"
        + _field_label("Search Seed Track")
        + f"<div class='afm-seed-search-field' id='afm-seed-track-picker' "
        + f"data-track-search-url='{html.escape(track_search_url)}'>"
        + f"<input name='seed_search' id='seed_search' class='afm-text-input afm-seed-search-input' "
        + "autocomplete='off' role='combobox' aria-expanded='false' "
        + "aria-controls='afm-seed-track-results' "
        + f"placeholder='Title or artist…' value='{html.escape(str(values.get('seed_search', '')))}'>"
        + "<div id='afm-seed-track-results' class='afm-seed-track-results'></div>"
        + "</div>"
        + "<p class='hint'>Type a title or artist — matching library tracks appear as you type. "
        + "Pick one or paste a track item id below.</p>"
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


def _station_identity_explainer_html() -> str:
    return _collapsible_explainer_html(
        "<p><strong>What this controls:</strong> Listener-facing labels on Alchemy FM — not which tracks play. "
        "Encoding, color themes, and heart playlist are in <strong>Alchemy FM Admin</strong>.</p>"
        "<p><strong>Channel Name:</strong> Display name on the station picker and tune-in page. "
        "<strong>Required before deploy</strong> — helpers and preview can suggest a draft name first.</p>"
        "<p><strong>Slug:</strong> Permanent id for this station (URL-safe). Auto-filled from the name; "
        "<strong>fixed after first deploy</strong> — pick carefully on a new channel.</p>"
        "<p><strong>Description:</strong> Short homepage blurb (120 characters max).</p>"
        "<p><strong>Icecast Mount:</strong> Stream path listeners tune to, e.g. <code>/yachtrock</code>. "
        "Defaults from slug if left blank.</p>"
    )


def _programming_explainer_html() -> str:
    return _collapsible_explainer_html(
        "<p><strong>What this controls:</strong> The core music identity of the station. Alchemy FM "
        "<strong>re-runs this query</strong> when the on-air queue needs more tracks (with Step 5 playback rules).</p>"
        "<p><strong>Sonic Vibe (CLAP):</strong> Describe how tracks <em>sound</em> — not lyrics.</p>"
        "<p><strong>Lyrics Theme:</strong> Search by meaning, story, or theme in lyrics.</p>"
        "<p><strong>Mood Cluster:</strong> Pick from your library analysis — mood + sub-cluster.</p>"
        "<p><strong>Song Alchemy Anchor:</strong> Search anchors as you type and pick one.</p>"
        "<p><strong>Similar to Seed Track:</strong> Search by title or artist as you type, then pick one library track.</p>"
        "<p><strong>Preview Size:</strong> How many tracks to fetch per preview run (Step 3).</p>"
    )


def _preview_explainer_html() -> str:
    return _collapsible_explainer_html(
        "<p><strong>What this does:</strong> Runs your Step 2 query in AudioMuse and shows matching tracks "
        "in <strong>Preview Results</strong> below the form. Nothing goes on air until Step 6 deploy.</p>"
        "<p><strong>Filters apply here:</strong> If you set optional filters, they trim the preview list. "
        "Tweak filters → preview again → use jump buttons to move between Step 3, Filters, and results.</p>"
        "<p><strong>Living pool:</strong> If living is enabled, preview also merges tracks already in this "
        "channel's AudioMuse pool.</p>"
        "<p><strong>Before deploy:</strong> Review tempo, energy, mood, and genre in the table. Fix programming "
        "or filters if the list is empty or off-vibe.</p>"
    )


def _bootstrap_explainer_html() -> str:
    return _collapsible_explainer_html(
        "<p><strong>What this controls:</strong> A one-time <strong>cold-start opener</strong> — a Navidrome "
        "playlist that plays first when you deploy. After the opener, <strong>Step 2 programming</strong> "
        "takes over for all ongoing playback.</p>"
        "<p><strong>Search:</strong> Type a playlist name — results appear as you type (title match only). "
        "Pick a result or paste a playlist id from Navidrome.</p>"
        "<p><strong>Verify:</strong> Runs automatically when you pick a playlist or change the playlist id.</p>"
        "<p><strong>Opener Track Limit:</strong> Max tracks to import from the opener (rest of queue comes from programming).</p>"
        "<p><strong>Skip on first try:</strong> Leave unchecked until your Step 3 preview looks right.</p>"
    )


def _deploy_explainer_html() -> str:
    return _collapsible_explainer_html(
        "<p><strong>What this does:</strong> Creates or updates the station on Alchemy FM with your saved "
        "programming, playback rules, and optional bootstrap/living settings.</p>"
        "<p><strong>Start On Air After Push:</strong> Enables broadcast immediately if checked.</p>"
        "<p><strong>Bootstrap Queue Immediately:</strong> Fills the play queue on deploy (programming batch + "
        "optional opener).</p>"
        "<p><strong>Required before deploy:</strong> Channel name (Step 1), working programming (Step 2), "
        "and a successful preview with at least some tracks.</p>"
        "<p><strong>Not here:</strong> Stream encoding, station artwork themes, and global admin settings — "
        "use <strong>Alchemy FM Admin</strong> after deploy.</p>"
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
        "<p><strong>How to use:</strong> Set your filter fields, then click "
        "<strong>Apply Filters to Preview</strong> to trim the last preview list without re-running "
        "programming. Under <strong>Exclude Artists</strong>, type to see library matches — "
        "click a name to add it to the comma-separated list above.</p>"
        "<p><strong>Full refresh:</strong> To re-query AudioMuse from scratch (new tracks), use "
        "<strong>Preview Programming</strong> in Step 3.</p>"
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


def _filters_fields_html(
    values: dict[str, Any],
    *,
    mood_labels: list[str] | None = None,
    show_results_jump: bool = False,
    flash_html: str = "",
    genre_search_url: str = "",
    mood_search_url: str = "",
) -> str:
    mood_labels = mood_labels if mood_labels is not None else _audiomuse_mood_labels()
    feedback = _filter_feedback_html(values.get("filter_feedback"))
    artist_search_url = html.escape(url_for("alchemy_fm_bridge.search_artists_api"))
    results_jump = ""
    if show_results_jump:
        results_jump = _jump_nav_button("View Preview Results", target_id="preview-results", direction="down")
    return (
        "<section class='afm-panel afm-step-panel' id='step-filters'>"
        + _step_panel_heading(
            "Optional",
            "Filters",
            "Trims Preview and Living pool tracks. Does not control on-air refills when the queue runs low.",
            optional=True,
        )
        + _filters_explainer_html()
        + flash_html
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
        + f"<input name='filter_genre_include' id='filter_genre_include' class='afm-text-input' "
        + "placeholder='e.g. rock — exact Top Genre from Preview Results' "
        + f"value='{html.escape(str(values.get('filter_genre_include', '')))}'>"
        + f"<div class='afm-artist-picker' id='afm-genre-include-picker' "
        + f"data-genre-search-url='{html.escape(genre_search_url)}' data-target-field='filter_genre_include'>"
        + '<input type="text" id="genre_include_typeahead" class="afm-text-input" autocomplete="off" '
        + 'placeholder="Start typing a genre…" role="combobox" aria-expanded="false" '
        + 'aria-controls="afm-genre-include-suggestions" aria-autocomplete="list">'
        + '<ul id="afm-genre-include-suggestions" class="afm-artist-suggestions" role="listbox" hidden></ul>'
        + "</div>"
        + "<p class='hint'>Genres from your library appear as you type. Click to add comma-separated includes.</p></div>"
        + "<div class='afm-field'>"
        + _field_label("Genre Exclude")
        + f"<input name='filter_genre_exclude' id='filter_genre_exclude' class='afm-text-input' "
        + "placeholder='e.g. classical — exact Top Genre spelling' "
        + f"value='{html.escape(str(values.get('filter_genre_exclude', '')))}'>"
        + f"<div class='afm-artist-picker' id='afm-genre-exclude-picker' "
        + f"data-genre-search-url='{html.escape(genre_search_url)}' data-target-field='filter_genre_exclude'>"
        + '<input type="text" id="genre_exclude_typeahead" class="afm-text-input" autocomplete="off" '
        + 'placeholder="Start typing a genre…" role="combobox" aria-expanded="false" '
        + 'aria-controls="afm-genre-exclude-suggestions" aria-autocomplete="list">'
        + '<ul id="afm-genre-exclude-suggestions" class="afm-artist-suggestions" role="listbox" hidden></ul>'
        + "</div>"
        + "<p class='hint'>Click suggestions to build a comma-separated exclude list.</p></div>"
        + "<div class='afm-field'>"
        + _field_label("Mood Tags Include")
        + f"<input name='filter_mood_include' id='filter_mood_include' class='afm-text-input' "
        + f"placeholder='melancholic, dreamy' "
        + f"value='{html.escape(str(values.get('filter_mood_include', '')))}'>"
        + f"<div class='afm-artist-picker' id='afm-mood-include-picker' "
        + f"data-mood-search-url='{html.escape(mood_search_url)}' data-target-field='filter_mood_include'>"
        + '<input type="text" id="mood_include_typeahead" class="afm-text-input" autocomplete="off" '
        + 'placeholder="Start typing a mood tag…" role="combobox" aria-expanded="false" '
        + 'aria-controls="afm-mood-include-suggestions" aria-autocomplete="list">'
        + '<ul id="afm-mood-include-suggestions" class="afm-artist-suggestions" role="listbox" hidden></ul>'
        + "</div>"
        + "<p class='hint'>Mood tags from AudioMuse appear as you type. Each tag must match how tracks were labeled.</p>"
        + "<p id='afm-mood-inline-hint' class='afm-mood-inline-hint' aria-live='polite'></p></div>"
        + "<div class='afm-field'>"
        + _field_label("Exclude Artists")
        + f"<input name='filter_exclude_artists' id='filter_exclude_artists' class='afm-text-input' "
        + f"placeholder='Artists to exclude — added from picker below or type comma-separated' "
        + f"value='{html.escape(str(values.get('filter_exclude_artists', '')))}'>"
        + f'<div class="afm-artist-picker" id="afm-artist-picker" data-artist-search-url="{artist_search_url}">'
        + '<input type="text" id="exclude_artist_typeahead" class="afm-text-input" autocomplete="off" '
        + 'placeholder="Start typing an artist name…" role="combobox" aria-expanded="false" '
        + 'aria-controls="afm-artist-suggestions" aria-autocomplete="list">'
        + '<ul id="afm-artist-suggestions" class="afm-artist-suggestions" role="listbox" hidden></ul>'
        + "</div>"
        + "<p class='hint'>Artists from your library appear as you type. Click one to add it to the list "
        "above (comma-separated). Pick another to keep building the list. Then click "
        "<strong>Apply Filters to Preview</strong>.</p></div>"
        + f"{feedback}"
        + "<p class='hint afm-filters-workflow-hint'><strong>Filters fine-tune your last preview</strong> — "
        "they trim tracks already in Preview Results; they do not fetch new ones.</p>"
        + '<div class="afm-form-actions afm-form-actions-inline">'
        + "<button type='submit' name='action' value='apply_filters' formnovalidate "
        + "class='afm-btn afm-btn-secondary'>Apply Filters to Preview</button>"
        + results_jump
        + "</div>"
        + "</section>"
    )


def _bootstrap_playlist_results_html(results: list[dict[str, Any]], *, query: str = "") -> str:
    if not results:
        if query.strip():
            return (
                "<p class='afm-bootstrap-results-empty'>"
                f"No Navidrome playlists with <strong>{html.escape(query.strip())}</strong> in the title. "
                "Try a shorter name or paste a playlist id below.</p>"
            )
        return ""
    items = []
    for pl in results:
        pl_id = str(pl.get("id") or "")
        name = pl.get("name") or pl_id or "Playlist"
        count = pl.get("count")
        count_s = f" · {count} tracks" if count is not None else ""
        items.append(
            "<li role='presentation'>"
            f'<button type="submit" name="pick_bootstrap_playlist" value="{html.escape(pl_id)}" '
            'formnovalidate class="afm-bootstrap-pick" role="option">'
            f"{html.escape(name)}{html.escape(count_s)}"
            "</button></li>"
        )
    count_label = f"{len(results)} playlist{'s' if len(results) != 1 else ''}"
    return (
        "<div class='afm-bootstrap-results'>"
        f"<p class='afm-bootstrap-results-label'>{html.escape(count_label)} — pick one</p>"
        "<ul class='afm-bootstrap-menu' role='listbox'>"
        + "".join(items)
        + "</ul></div>"
    )


def _bootstrap_fields_html(values: dict[str, Any], *, flash_html: str = "", verify_url: str = "") -> str:
    bootstrap_enabled = values.get("bootstrap_enabled", False)
    playlist_results = values.get("bootstrap_playlist_results") or []
    search_query = str(values.get("bootstrap_playlist_search", ""))
    results_html = _bootstrap_playlist_results_html(playlist_results, query=search_query)
    feedback = _bootstrap_feedback_html(values.get("bootstrap_check"))
    playlist_search_url = html.escape(url_for("alchemy_fm_bridge.search_playlists_api"))
    return (
        "<section class='afm-panel afm-bootstrap-panel afm-step-panel' id='bootstrap-opener'>"
        + _step_panel_heading(
            "Optional",
            "Bootstrap Opener",
            "A Navidrome playlist that plays first at deploy only. After that, Step 2 programming takes over.",
            optional=True,
        )
        + _bootstrap_explainer_html()
        + flash_html
        + "<div class='afm-check-group'>"
        + "<label class='afm-check-label'><input type='checkbox' name='bootstrap_enabled' id='bootstrap_enabled'"
        + f"{' checked' if bootstrap_enabled else ''}> Use Navidrome Playlist Opener</label>"
        + "</div>"
        + f"<div class='afm-field afm-bootstrap-search-field' id='afm-bootstrap-playlist-picker' "
        + f"data-playlist-search-url='{playlist_search_url}' "
        + f"data-verify-url='{html.escape(verify_url)}'>"
        + _field_label("Search Navidrome Playlists")
        + f"<input name='bootstrap_playlist_search' id='bootstrap_playlist_search' "
        + "class='afm-text-input' "
        + f"placeholder='Playlist name…' value='{html.escape(search_query)}' autocomplete='off' "
        + "aria-expanded='false' aria-controls='afm-bootstrap-playlist-results'>"
        + f"<div id='afm-bootstrap-playlist-results' aria-live='polite'>{results_html}</div>"
        + "<p class='hint'>Playlists from Navidrome appear as you type. Pick one to fill Playlist ID and verify automatically.</p>"
        + "</div>"
        + "<div class='afm-field'>"
        + _field_label("Playlist ID")
        + f"<input name='bootstrap_playlist_id' id='bootstrap_playlist_id' class='afm-text-input' "
        + f"placeholder='From search or Navidrome' "
        + f"value='{html.escape(str(values.get('bootstrap_playlist_id', '')))}'>"
        + "<p class='hint'>Paste an id manually — verification runs automatically when the id changes.</p>"
        + "</div>"
        + "<div class='afm-field'>"
        + _field_label("Opener Track Limit")
        + f"<input type='number' name='bootstrap_track_limit' id='bootstrap_track_limit' min='5' max='80' "
        + f"value='{html.escape(str(values.get('bootstrap_track_limit', BOOTSTRAP_TRACK_LIMIT_DEFAULT)))}'>"
        + "</div>"
        + f"<div id='afm-bootstrap-live-feedback'>{feedback}</div>"
        + "</section>"
    )


def _chat_designer_fields_html(values: dict[str, Any], *, flash_html: str = "") -> str:
    keep_open = bool((values.get("chat_prompt") or "").strip()) or bool(
        values.get("chat_designer_open")
    )
    open_attr = " open" if keep_open else ""
    return (
        f'<details class="afm-panel afm-collapsible-helper" id="chat-designer"{open_attr}>'
        "<summary>"
        '<span class="afm-helper-badge">Helper</span>'
        '<span><span class="afm-collapsible-title">Chat Designer</span>'
        '<span class="afm-collapsible-hint">Use <strong>before Step 2</strong> when you are not sure what to program. '
        "<strong>Generate Playlist Preview</strong> sets Step 2 to a <strong>Sonic Vibe (CLAP)</strong> query from your "
        "description and shows Preview Results — it does not deploy.</span></span>"
        "</summary>"
        '<div class="afm-collapsible-body">'
        f"{flash_html}"
        "<p class='hint'>Slow LLM call — requires AudioMuse chat/AI configured. Tweak Programming after preview, "
        "then use Step 3 Preview Programming before deploy.</p>"
        + "<div class='afm-field'>"
        + _field_label("Describe Your Station")
        + f"<textarea name='chat_prompt' rows='3' class='afm-text-input' "
        + f"placeholder='e.g. upbeat 80s synthpop for a morning commute'>{html.escape(str(values.get('chat_prompt', '')))}</textarea>"
        + "</div>"
        + _action_loading_html(
            "afm-chat-loading",
            title="Generating playlist preview…",
            detail="AudioMuse chat is running — often 30–90 seconds. Stay on this page.",
        )
        + '<p id="afm-chat-error" class="afm-flash afm-flash-error" hidden role="alert"></p>'
        + '<div class="afm-form-actions afm-form-actions-inline">'
        + "<button type='submit' name='action' value='chat_preview' formnovalidate "
        + 'class="afm-btn afm-btn-secondary" data-afm-loading="afm-chat-loading" '
        + 'data-afm-loading-panel="chat-designer" data-afm-loading-no-scroll="true" '
        + 'data-afm-ajax-preview="true" data-loading-label="Generating…">'
        "Generate Playlist Preview</button>"
        + "</div>"
        + f"<input type='hidden' name='design_notes' value='{html.escape(str(values.get('design_notes', '')))}'>"
        + "</div></details>"
    )


def _discover_channels_html(*, flash_html: str = "") -> str:
    task = _last_clustering_task()
    task_note = ""
    if task:
        status = task.get("status") or task.get("state") or "unknown"
        task_note = f"<p class='hint'>Last Clustering Task: {html.escape(str(status))}</p>"
    playlists = _clustering_playlists()
    rows: list[str] = []
    for pl in playlists:
        pl_id = str(pl.get("id") or pl.get("playlist_id") or "")
        name = str(pl.get("name") or pl_id or "Cluster playlist")
        mood = str(pl.get("mood") or pl.get("description") or "")
        if not mood and pl.get("track_count"):
            mood = f"{int(pl['track_count'])} Tracks"
        deploy_query = name
        search_blob = html.escape(f"{name} {mood}".lower())
        rows.append(
            f"<tr data-discover-search='{search_blob}'>"
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
            '<div class="afm-field" id="afm-discover-filter-wrap">'
            + '<label for="discover_filter">Filter Playlists</label>'
            + '<input type="text" id="discover_filter" class="afm-text-input" '
            + 'placeholder="Playlist name or mood…" autocomplete="off">'
            + '<p class="hint">Clustering playlists filter as you type.</p></div>'
            + '<div class="afm-table-wrap"><table class="afm-table" id="afm-discover-table">'
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
        f"{flash_html}"
        '<div class="afm-form-actions afm-form-actions-inline" style="margin-bottom:0.75rem;">'
        '<button type="submit" name="action" value="start_clustering" formnovalidate '
        'class="afm-btn afm-btn-secondary">Run Clustering</button>'
        "</div>"
        f"{task_note}{table}"
        "</div></details>"
    )


def _living_explainer_html() -> str:
    return _collapsible_explainer_html(
        "<p><strong>When living runs:</strong> In AudioMuse only — not Alchemy FM on-air refills. "
        "Uses your <strong>Step 2 Programming</strong> query and <strong>Filters</strong> for this station.</p>"
        "<p><strong>Enable Living Channel:</strong> Master switch for this slug's evolving track pool. "
        "Off = pool stays frozen at whatever you last previewed or deployed.</p>"
        "<p><strong>Auto-Add:</strong> When a new song finishes analysis in AudioMuse, add it to this "
        "station's pool if it passes your filters. Requires the worker to reach AudioMuse "
        "(plugin Settings → API URL if needed).</p>"
        "<p><strong>Refresh:</strong> Nightly cron re-runs programming, merges new matches into the pool, "
        "and can push updates to Alchemy FM. Enable the global task under "
        "<strong>Administration → Scheduled Tasks → Alchemy FM</strong> once for all living stations.</p>"
        "<p><strong>Pool count:</strong> Tracks stored for this channel in AudioMuse — used by preview, "
        "living auto-add, and cron. Alchemy FM has its own on-air queue; living grows the design-time pool.</p>"
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
        + _living_explainer_html()
        + f"{pool_note}"
        + "<div class='afm-check-group'>"
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


def _alchemy_station_id_hidden(values: dict[str, Any]) -> str:
    station_id = int(values.get("edit_station_id") or values.get("alchemy_station_id") or 0)
    if not station_id:
        return ""
    return f"<input type='hidden' name='alchemy_station_id' value='{station_id}'>"


def _station_identity_fields_html(values: dict[str, Any]) -> str:
    editing_slug = (values.get("editing_slug") or "").strip()
    draft_slug = (values.get("draft_slug") or "").strip()
    slug_readonly = " readonly class='is-readonly'" if editing_slug else ""
    slug_extra = ""
    if editing_slug:
        slug_extra = (
            f"<input type='hidden' name='editing_slug' value='{html.escape(editing_slug)}'>"
            "<p class='hint'>Slug is fixed after first deploy.</p>"
        )
    elif draft_slug:
        slug_extra = f"<input type='hidden' name='draft_slug' value='{html.escape(draft_slug)}'>"
    return (
        "<section class='afm-panel afm-step-panel'>"
        + _step_panel_heading(
            "Step 1",
            "Station Identity",
            "What listeners see on Alchemy FM — name, URL mount, and homepage description. Does not affect track selection.",
        )
        + _station_identity_explainer_html()
        + _alchemy_station_id_hidden(values)
        + "<div class='afm-field'>"
        + _field_label("Channel Name", mandatory=True)
        + f"<input name='name' value='{html.escape(str(values.get('name', '')))}'>"
        + "<p class='hint'>Required before deploy — preview can use a draft name from your programming.</p></div>"
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


def _preview_status_html(values: dict[str, Any], *, flash_html: str = "") -> str:
    parts: list[str] = []
    flash = (flash_html or "").strip()
    if flash:
        parts.append(flash)
    last_err = (values.get("preview_last_error") or "").strip()
    if last_err and "afm-flash-error" not in flash:
        parts.append(_flash_html(f"Last preview failed: {last_err}", "error"))
    if not parts:
        return (
            '<div id="afm-preview-status" class="afm-preview-status" hidden '
            'role="status" aria-live="polite"></div>'
        )
    return (
        '<div id="afm-preview-status" class="afm-preview-status" role="status" aria-live="polite">'
        + "".join(parts)
        + "</div>"
    )


def _preview_step_html(
    values: dict[str, Any],
    *,
    show_results_jump: bool = False,
    flash_html: str = "",
) -> str:
    results_jump = ""
    if show_results_jump:
        results_jump = _jump_nav_button("View Preview Results", target_id="preview-results", direction="down")
    return (
        "<section class='afm-panel afm-step-panel' id='step-preview'>"
        + _step_panel_heading(
            "Step 3",
            "Preview Programming",
            "Runs your Step 2 query in AudioMuse and shows matching tracks below. Nothing goes on air until Step 6 deploy.",
        )
        + _preview_explainer_html()
        + _preview_status_html(values, flash_html=flash_html)
        + _action_loading_html(
            "afm-preview-loading",
            title="Running preview…",
            detail="Querying AudioMuse for tracks that match your programming.",
        )
        + "<div class='afm-form-actions afm-form-actions-inline'>"
        + "<button type='submit' name='action' value='preview' formnovalidate class='afm-btn afm-btn-primary' "
        + 'data-afm-loading="afm-preview-loading" data-afm-loading-panel="step-preview" '
        + 'data-afm-loading-no-scroll="true" '
        + 'data-loading-label="Previewing…">'
        "Preview Programming</button>"
        + results_jump
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


def _deploy_status_html(values: dict[str, Any], *, flash_html: str = "") -> str:
    parts: list[str] = []
    flash = (flash_html or "").strip()
    if flash:
        parts.append(flash)
    last_err = (values.get("deploy_last_error") or "").strip()
    if last_err and "afm-flash-error" not in flash:
        parts.append(_flash_html(f"Last deploy failed: {last_err}", "error"))
    if not parts:
        return (
            '<div id="afm-deploy-status" class="afm-deploy-status" hidden '
            'role="status" aria-live="polite"></div>'
        )
    return (
        '<div id="afm-deploy-status" class="afm-deploy-status" role="status" aria-live="polite">'
        + "".join(parts)
        + "</div>"
    )


def _deploy_actions_fields_html(values: dict[str, Any], *, flash_html: str = "") -> str:
    editing_slug = (values.get("editing_slug") or "").strip()
    deploy_label = "Save Changes to Alchemy FM" if editing_slug else "Deploy to Alchemy FM"
    readiness = _deploy_readiness(values)
    deploy_blocked = not readiness.get("ok")
    deploy_disabled = " disabled" if deploy_blocked else ""
    deploy_blocked_attr = ' data-deploy-blocked="true"' if deploy_blocked else ""
    blocker_title = html.escape(readiness["blockers"][0]) if readiness.get("blockers") else ""
    title_attr = f' title="{blocker_title}"' if blocker_title else ""
    return (
        "<section class='afm-panel afm-step-panel afm-deploy-panel' id='step-deploy'>"
        + _step_panel_heading(
            "Step 6",
            "Deploy to Alchemy FM",
            "Creates or updates the station and optionally fills the play queue. Encoding and themes are in Alchemy FM Admin.",
        )
        + _deploy_explainer_html()
        + _deploy_readiness_html(readiness)
        + _deploy_status_html(values, flash_html=flash_html)
        + _alchemy_station_id_hidden(values)
        + _action_loading_html(
            "afm-deploy-loading",
            title="Deploying to Alchemy FM…",
            detail="Creating or updating your station and queue.",
        )
        + "<div class='afm-check-group'>"
        + "<label class='afm-check-label'><input type='checkbox' name='enabled'"
        + f"{' checked' if values.get('enabled', True) else ''}> Start On Air After Push</label>"
        + "<label class='afm-check-label'><input type='checkbox' name='bootstrap_queue'"
        + f"{' checked' if values.get('bootstrap_queue', True) else ''}> Bootstrap Queue Immediately</label>"
        + "</div>"
        + f"<input type='hidden' name='saved_anchor_id' value='{html.escape(str(values.get('saved_anchor_id', '')))}'>"
        + "<div class='afm-form-actions'>"
        + f"<button type='submit' name='action' value='push' class='afm-btn afm-btn-primary' formnovalidate "
        + f'data-afm-loading="afm-deploy-loading" data-afm-loading-panel="step-deploy" '
        + f'data-loading-label="Deploying…"{deploy_disabled}{deploy_blocked_attr}{title_attr}>'
        + html.escape(deploy_label)
        + "</button>"
        + "<button type='submit' name='action' value='test' formnovalidate class='afm-btn afm-btn-secondary'>Test Alchemy Connection</button>"
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


def _edit_toolbar_html(values: dict[str, Any], *, flash_html: str = "") -> str:
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
        f"{flash_html}"
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
        'class="afm-btn afm-btn-primary" data-afm-loading="afm-deploy-loading" '
        'data-afm-loading-panel="step-deploy" data-loading-label="Saving…">Save Changes</button>'
        "</div></div>"
    )


def _stations_section_html(editing_slug: str | None = None, *, flash_html: str = "") -> str:
    try:
        stations = _client().test_connection(skip_deploy_check=True)
    except ChannelDesignerError as exc:
        return (
            '<section class="afm-section">'
            f'<p class="afm-empty">Could not load Alchemy FM stations: {html.escape(str(exc))}</p>'
            "</section>"
        )
    except Exception as exc:
        logger.exception("Could not render stations section")
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
            search_blob = html.escape(f"{name} {slug} {type_label}".lower())
            rows.append(
                f"<tr{row_class} data-station-search='{search_blob}'>"
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
            '<div class="afm-field" id="afm-stations-filter-wrap">'
            + '<label for="afm-stations-filter">Filter Stations</label>'
            + '<input type="text" id="afm-stations-filter" class="afm-text-input" '
            + 'placeholder="Name, slug, or programming type…" autocomplete="off">'
            + '<p class="hint">Your stations filter as you type.</p></div>'
            + '<div class="afm-table-wrap"><table class="afm-table" id="afm-stations-table">'
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
        f"{flash_html}"
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
        values["clap_query"] = programming.get("query") or ""
    elif ptype == "lyrics_query":
        values["lyrics_query"] = programming.get("query") or ""
    elif ptype == "mood_centroid":
        values["mood_name"] = programming.get("mood") or ""
        values["centroid_index"] = programming.get("centroid_index") or ""
    elif ptype == "alchemy_anchor":
        anchor_id = _text(programming.get("anchor_id"))
        values["anchor_id"] = anchor_id
        if anchor_id:
            for anchor in _anchors():
                if str(anchor.get("id")) == anchor_id:
                    values["anchor_search"] = str(anchor.get("name") or anchor_id)
                    break
    elif ptype == "similar_seed":
        values["seed_id"] = programming.get("seed_id") or ""
    return values


def _is_chat_preview_ajax() -> bool:
    if request.headers.get("X-AFM-Chat-Preview") == "1":
        return True
    if (request.form.get("afm_ajax") or "").strip() == "chat_preview":
        return True
    return False


def _chat_preview_redirect(slug: str) -> str:
    return url_for("alchemy_fm_bridge.home", draft=slug, chat_preview_ok=1) + "#preview-results"


def _run_chat_preview_from_form(form) -> dict[str, Any]:
    profile = profile_from_form(form, for_deploy=False)
    values: dict[str, Any] = _form_values_from_profile(profile)
    if (form.get("editing_slug") or "").strip():
        values["editing_slug"] = form.get("editing_slug").strip()
    slug = profile["station"]["slug"]

    prompt = (form.get("chat_prompt") or "").strip()
    values["chat_designer_open"] = True
    values["chat_prompt"] = prompt
    raw = _chat_playlist_tracks(prompt)
    if not raw:
        raise ChannelDesignerError("Chat designer returned no tracks.")
    profile["design_notes"] = prompt
    profile["programming"] = {
        "type": "clap_query",
        "query": prompt[:120],
        "limit": PREVIEW_LIMIT_DEFAULT,
    }
    if not (form.get("name") or "").strip():
        profile["station"]["name"] = prompt[:60]
        if not (form.get("editing_slug") or "").strip():
            profile["station"]["slug"] = _slugify(profile["station"]["name"]) or "draft-channel"
            profile["station"]["icecast_mount"] = _normalize_mount(profile["station"]["slug"])
    slug = profile["station"]["slug"]
    unfiltered = enrich_preview(raw)
    preview_tracks = apply_track_filters(unfiltered, profile)
    _apply_filter_feedback(values, profile, unfiltered, preview_tracks)
    item_ids = [t["item_id"] for t in preview_tracks]
    _record_audition(slug, item_ids)
    _save_channel(
        profile,
        preview_ids=item_ids,
        unfiltered_preview_ids=[t["item_id"] for t in unfiltered],
    )
    return {
        "ok": True,
        "track_count": len(preview_tracks),
        "slug": slug,
        "redirect": _chat_preview_redirect(slug),
    }


def _apply_draft_channel(
    draft_slug: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    loaded = _load_saved_channel(draft_slug)
    if not loaded:
        raise ChannelDesignerError(f"No saved draft found for '{draft_slug}'.")
    profile, preview_ids = loaded
    values = _form_values_from_profile(profile)
    values["chat_prompt"] = profile.get("design_notes") or values.get("chat_prompt", "")
    values["chat_designer_open"] = True
    values["slug"] = draft_slug
    values["draft_slug"] = draft_slug
    preview_tracks = _preview_tracks_from_ids(preview_ids)
    return values, preview_tracks


def _page_script(
    mood_centroids: dict[str, Any] | None = None,
    mood_labels: list[str] | None = None,
    *,
    scroll_anchor: str = "",
    scroll_to_preview: bool = False,
    scroll_to_bootstrap: bool = False,
    instant_scroll: bool = False,
) -> str:
    mood_json = json.dumps(mood_centroids or {})
    mood_labels_json = json.dumps(mood_labels or [])
    if scroll_to_preview and not scroll_anchor:
        scroll_anchor = "preview-results"
    if scroll_to_bootstrap and not scroll_anchor:
        scroll_anchor = "bootstrap-opener"
    scroll_anchor_json = json.dumps(scroll_anchor or "")
    instant_scroll_flag = "true" if instant_scroll else "false"
    return f"""
<script type="application/json" id="mood-centroids-data">{mood_json}</script>
<script type="application/json" id="mood-labels-data">{mood_labels_json}</script>
<script>
(function() {{
  if ('scrollRestoration' in history) {{
    history.scrollRestoration = 'manual';
  }}
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
      const show = key === t;
      el.hidden = !show;
      el.style.display = show ? '' : 'none';
      /* Do NOT disable hidden fields — disabled inputs are omitted from POST and
         Step 2 programming was lost on deploy. Hidden inactive fields are ignored
         server-side via programming_type. */
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
    const prev = clusterSelect.value || clusterSelect.getAttribute('data-initial-value') || '';
    const list = moodData[mood];
    if (!Array.isArray(list)) {{
      if (clusterSelect.options.length > 1 && prev) return;
      if (clusterSelect.options.length > 1) return;
      clusterSelect.innerHTML = '<option value="">Choose cluster…</option>';
      return;
    }}
    clusterSelect.innerHTML = '<option value="">Choose cluster…</option>';
    list.forEach((meta, idx) => {{
      if (!meta || typeof meta !== 'object') return;
      const opt = document.createElement('option');
      const clusterIdx = meta.index != null ? String(meta.index) : String(idx);
      opt.value = clusterIdx;
      opt.textContent = clusterLabel(meta, idx);
      if (clusterIdx === prev) opt.selected = true;
      clusterSelect.appendChild(opt);
    }});
    if (prev && clusterSelect.value !== prev) {{
      const fallback = document.createElement('option');
      fallback.value = prev;
      fallback.textContent = 'Cluster ' + prev + ' (saved)';
      fallback.selected = true;
      clusterSelect.appendChild(fallback);
    }}
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
        if (select.id === 'programming_type') syncType();
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
  syncType();
  document.addEventListener('click', (event) => {{
    if (!event.target.closest('.afm-select-wrap')) closeAllSelectMenus(null);
  }});

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

  function scrollToAfmAnchor(targetId) {{
    const el = document.getElementById(targetId);
    if (!el) return;
    if (el.tagName === 'DETAILS') el.open = true;
    const instantTargets = ['preview-results', 'step-deploy', 'step-programming', 'afm-deploy-error-pinned', 'afm-deploy-status'];
    const behavior = ({instant_scroll_flag} && instantTargets.includes(targetId)) ? 'instant' : 'smooth';
    const block = 'start';
    const run = () => {{
      el.scrollIntoView({{ behavior: behavior, block: block }});
    }};
    window.requestAnimationFrame(run);
    if (targetId === 'preview-results') {{
      window.setTimeout(run, 60);
      window.setTimeout(run, 180);
    }}
  }}

  function scrollToPreviewResults() {{
    scrollToAfmAnchor('preview-results');
  }}

  function scrollToAfmJump(targetId) {{
    scrollToAfmAnchor(targetId);
  }}

  document.querySelectorAll('[data-afm-jump]').forEach((btn) => {{
    btn.addEventListener('click', () => {{
      const targetId = btn.getAttribute('data-afm-jump');
      if (targetId) scrollToAfmJump(targetId);
    }});
  }});
  function resetAfmLoadingChrome() {{
    document.querySelectorAll('.afm-action-loading').forEach((el) => {{
      el.hidden = true;
    }});
    document.querySelectorAll('.is-working').forEach((el) => {{
      el.classList.remove('is-working');
    }});
    const shell = document.querySelector('.afm-shell');
    if (shell) shell.classList.remove('is-busy');
    const busyForm = document.getElementById('afm-designer-form');
    if (busyForm) {{
      busyForm.removeAttribute('aria-busy');
      busyForm.querySelectorAll('button[type="submit"]').forEach((btn) => {{
        btn.disabled = false;
        btn.classList.remove('is-loading');
      }});
    }}
  }}

  function scrollToDeployFailure() {{
    const pinned = document.getElementById('afm-deploy-error-pinned');
    const deployStatus = document.getElementById('afm-deploy-status');
    const target = pinned || (
      deployStatus && deployStatus.querySelector('.afm-flash-error') ? deployStatus : null
    );
    if (!target) return false;
    target.scrollIntoView({{ behavior: 'instant', block: 'start' }});
    return true;
  }}

  resetAfmLoadingChrome();
  window.addEventListener('pageshow', () => {{
    resetAfmLoadingChrome();
  }});

  const scrollAnchor = {scroll_anchor_json};
  const hashAnchor = (window.location.hash || '').replace(/^#/, '');
  const targetAnchor = scrollAnchor || hashAnchor;
  if (targetAnchor) {{
    const runScroll = () => {{
      if (targetAnchor === 'step-deploy' && scrollToDeployFailure()) return;
      scrollToAfmAnchor(targetAnchor);
    }};
    if (document.readyState === 'loading') {{
      document.addEventListener('DOMContentLoaded', runScroll);
    }} else {{
      runScroll();
    }}
  }} else if (scrollToDeployFailure()) {{
    /* error persisted from last deploy — keep banner in view */
  }}
  if (targetAnchor === 'bootstrap-opener') {{
    window.requestAnimationFrame(() => {{
      const searchInput = document.getElementById('bootstrap_playlist_search');
      if (searchInput) searchInput.focus({{ preventScroll: true }});
    }});
  }}

  const designerForm = document.getElementById('afm-designer-form');
  const afmShell = document.querySelector('.afm-shell');

  if (designerForm) {{
    // This form has many submit buttons (Preview, Deploy, Test Connection, etc.).
    // Enter in a plain text field implicitly submits with no button "activated",
    // so the browser omits any action=... field entirely and the server silently
    // no-ops. Block implicit Enter-submission here; explicit button clicks are
    // unaffected, and typeahead fields handle Enter themselves (pick a result).
    designerForm.addEventListener('keydown', (event) => {{
      if (event.key !== 'Enter') return;
      const target = event.target;
      if (!target || target.tagName !== 'INPUT') return;
      const type = (target.getAttribute('type') || 'text').toLowerCase();
      const textLikeTypes = ['text', 'search', 'email', 'url', 'tel', 'number', 'password'];
      if (!textLikeTypes.includes(type)) return;
      event.preventDefault();
    }});
  }}

  function showAfmActionLoading(submitter) {{
    const loadingId = submitter.getAttribute('data-afm-loading');
    const loading = loadingId ? document.getElementById(loadingId) : null;
    const panelId = submitter.getAttribute('data-afm-loading-panel');
    const panel = panelId ? document.getElementById(panelId) : null;

    if (panel) {{
      if (panel.tagName === 'DETAILS') panel.open = true;
      panel.classList.add('is-working');
      const skipScroll = submitter.getAttribute('data-afm-loading-no-scroll') === 'true';
      if (!skipScroll) {{
        window.requestAnimationFrame(() => {{
          panel.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
        }});
      }}
    }}
    if (loading) loading.hidden = false;

    const loadingLabel = submitter.getAttribute('data-loading-label') || 'Working…';
    submitter.dataset.originalLabel = (submitter.textContent || '').trim();
    submitter.textContent = loadingLabel;
    submitter.classList.add('is-loading');
    submitter.disabled = true;

    if (designerForm) {{
      designerForm.querySelectorAll('button[type="submit"]').forEach((btn) => {{
        if (btn !== submitter) btn.disabled = true;
      }});
      designerForm.setAttribute('aria-busy', 'true');
    }}
    if (afmShell) afmShell.classList.add('is-busy');
  }}

  function resetAfmActionLoading(submitter) {{
    const loadingId = submitter.getAttribute('data-afm-loading');
    const loading = loadingId ? document.getElementById(loadingId) : null;
    const panelId = submitter.getAttribute('data-afm-loading-panel');
    const panel = panelId ? document.getElementById(panelId) : null;
    if (loading) loading.hidden = true;
    if (panel) panel.classList.remove('is-working');
    if (submitter.dataset.originalLabel) {{
      submitter.textContent = submitter.dataset.originalLabel;
    }}
    submitter.classList.remove('is-loading');
    submitter.disabled = false;
    if (designerForm) {{
      designerForm.querySelectorAll('button[type="submit"]').forEach((btn) => {{
        btn.disabled = false;
      }});
      designerForm.removeAttribute('aria-busy');
    }}
    if (afmShell) afmShell.classList.remove('is-busy');
  }}

  async function runChatPreviewAjax(submitter) {{
    const chatError = document.getElementById('afm-chat-error');
    if (chatError) {{
      chatError.hidden = true;
      chatError.textContent = '';
    }}
    showAfmActionLoading(submitter);
    const formData = new FormData(designerForm);
    formData.set('action', 'chat_preview');
    formData.set('afm_ajax', 'chat_preview');
    const chatPreviewUrl = (
      designerForm.getAttribute('data-chat-preview-url')
      || designerForm.action
      || window.location.href
    );
    try {{
      const resp = await fetch(chatPreviewUrl, {{
        method: 'POST',
        body: formData,
        credentials: 'same-origin',
        headers: {{
          'X-AFM-Chat-Preview': '1',
          'Accept': 'application/json',
        }},
      }});
      const rawText = await resp.text();
      let data = null;
      try {{
        data = rawText ? JSON.parse(rawText) : null;
      }} catch (parseErr) {{
        const snippet = rawText.replace(/\\s+/g, ' ').trim().slice(0, 140);
        const contentType = resp.headers.get('content-type') || 'unknown type';
        throw new Error(
          'Chat preview returned an unexpected response (HTTP '
          + resp.status
          + ', '
          + contentType
          + ').'
          + (snippet ? ' ' + snippet : '')
        );
      }}
      if (!resp.ok || !data || !data.ok) {{
        throw new Error((data && data.error) || 'Chat preview failed.');
      }}
      if (data.redirect) {{
        window.location.assign(data.redirect);
        return;
      }}
      throw new Error('Chat preview succeeded but no redirect was provided.');
    }} catch (err) {{
      resetAfmActionLoading(submitter);
      if (chatError) {{
        chatError.textContent = err && err.message ? err.message : 'Chat preview failed.';
        chatError.hidden = false;
      }}
    }}
  }}

  if (designerForm) {{
    designerForm.addEventListener('submit', (event) => {{
      const submitter = event.submitter;
      if (!submitter || submitter.disabled) return;
      if (
        submitter.getAttribute('data-deploy-blocked') === 'true'
        && submitter.name === 'action'
        && submitter.value === 'push'
      ) {{
        event.preventDefault();
        const readiness = document.getElementById('afm-deploy-readiness');
        if (readiness) readiness.scrollIntoView({{ behavior: 'instant', block: 'start' }});
        return;
      }}
      Object.values(sections).forEach((el) => {{
        if (!el) return;
        el.querySelectorAll('input, select, textarea').forEach((input) => {{
          input.disabled = false;
        }});
      }});
      if (
        submitter.getAttribute('data-afm-ajax-preview') === 'true'
        && submitter.name === 'action'
        && submitter.value === 'chat_preview'
      ) {{
        event.preventDefault();
        runChatPreviewAjax(submitter);
        return;
      }}
      const loadingId = submitter.getAttribute('data-afm-loading');
      if (!loadingId) return;
      showAfmActionLoading(submitter);
    }});
  }}

  (function initExcludeArtistTypeahead() {{
    const picker = document.getElementById('afm-artist-picker');
    const input = document.getElementById('exclude_artist_typeahead');
    const menu = document.getElementById('afm-artist-suggestions');
    const excludeField = document.getElementById('filter_exclude_artists');
    if (!picker || !input || !menu || !excludeField) return;

    const apiUrl = picker.getAttribute('data-artist-search-url') || '';
    let debounceTimer = null;
    let activeIndex = -1;
    let currentArtists = [];

    function closeMenu() {{
      menu.innerHTML = '';
      menu.hidden = true;
      input.setAttribute('aria-expanded', 'false');
      activeIndex = -1;
      currentArtists = [];
    }}

    function appendArtist(name) {{
      const term = String(name || '').trim();
      if (!term) return;
      const parts = (excludeField.value || '')
        .split(',')
        .map((part) => part.trim())
        .filter(Boolean);
      if (!parts.some((part) => part.toLowerCase() === term.toLowerCase())) {{
        parts.push(term);
      }}
      excludeField.value = parts.join(', ');
      input.value = '';
      closeMenu();
      input.focus();
    }}

    function setActive(index) {{
      const buttons = menu.querySelectorAll('.afm-artist-suggestion');
      buttons.forEach((btn, idx) => {{
        btn.classList.toggle('is-active', idx === index);
      }});
      activeIndex = index;
      const active = buttons[index];
      if (active) active.scrollIntoView({{ block: 'nearest' }});
    }}

    function renderMenu(artists) {{
      menu.innerHTML = '';
      currentArtists = artists;
      if (!artists.length) {{
        closeMenu();
        return;
      }}
      artists.forEach((artist, idx) => {{
        const item = document.createElement('li');
        item.setAttribute('role', 'presentation');
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'afm-artist-suggestion';
        btn.setAttribute('role', 'option');
        btn.textContent = artist;
        btn.addEventListener('mousedown', (event) => {{
          event.preventDefault();
          appendArtist(artist);
        }});
        btn.addEventListener('mouseenter', () => setActive(idx));
        item.appendChild(btn);
        menu.appendChild(item);
      }});
      menu.hidden = false;
      input.setAttribute('aria-expanded', 'true');
      setActive(0);
    }}

    async function fetchArtists(query) {{
      if (!apiUrl) return [];
      const resp = await fetch(apiUrl + '?q=' + encodeURIComponent(query), {{
        headers: {{ Accept: 'application/json' }},
      }});
      if (!resp.ok) return [];
      const data = await resp.json();
      return Array.isArray(data.artists) ? data.artists : [];
    }}

    input.addEventListener('input', () => {{
      clearTimeout(debounceTimer);
      const query = input.value.trim();
      if (query.length < 2) {{
        closeMenu();
        return;
      }}
      debounceTimer = setTimeout(async () => {{
        try {{
          const artists = await fetchArtists(query);
          renderMenu(artists);
        }} catch (err) {{
          closeMenu();
        }}
      }}, 280);
    }});

    input.addEventListener('keydown', (event) => {{
      if (event.key === 'Enter') {{
        event.preventDefault();
        if (activeIndex >= 0 && currentArtists[activeIndex]) {{
          appendArtist(currentArtists[activeIndex]);
        }}
        return;
      }}
      const buttons = menu.querySelectorAll('.afm-artist-suggestion');
      if (!buttons.length) return;
      if (event.key === 'ArrowDown') {{
        event.preventDefault();
        setActive(Math.min(activeIndex + 1, buttons.length - 1));
      }} else if (event.key === 'ArrowUp') {{
        event.preventDefault();
        setActive(Math.max(activeIndex - 1, 0));
      }} else if (event.key === 'Escape') {{
        closeMenu();
      }}
    }});

    document.addEventListener('click', (event) => {{
      if (!picker.contains(event.target)) closeMenu();
    }});
  }})();

  (function initBootstrapPlaylistTypeahead() {{
    const picker = document.getElementById('afm-bootstrap-playlist-picker');
    const input = document.getElementById('bootstrap_playlist_search');
    const results = document.getElementById('afm-bootstrap-playlist-results');
    const playlistIdField = document.getElementById('bootstrap_playlist_id');
    const bootstrapEnabled = document.getElementById('bootstrap_enabled');
    if (!picker || !input || !results) return;

    const apiUrl = picker.getAttribute('data-playlist-search-url') || '';
    const verifyUrl = picker.getAttribute('data-verify-url') || '';
    const limitField = document.getElementById('bootstrap_track_limit');
    let debounceTimer = null;
    let verifyTimer = null;
    let activeIndex = -1;
    let currentPlaylists = [];

    function escapeHtml(text) {{
      return String(text || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }}

    function renderBootstrapFeedback(check) {{
      const box = document.getElementById('afm-bootstrap-live-feedback');
      if (!box) return;
      if (!check || !check.playlist_id) {{
        box.innerHTML = '';
        return;
      }}
      const pid = escapeHtml(check.playlist_id);
      if (check.ok) {{
        const count = check.resolved_count || 0;
        const limit = check.limit || 0;
        box.innerHTML = (
          '<section class="afm-filter-feedback afm-bootstrap-feedback">'
          + '<h4 class="afm-filter-feedback-title">Bootstrap Check</h4>'
          + '<p class="afm-filter-term-ok">✓ Playlist <strong>' + pid + '</strong> resolves to '
          + '<strong>' + count + '</strong> opener track(s) (limit ' + limit + ').</p></section>'
        );
      }} else {{
        const err = escapeHtml(check.error || 'Verification failed.');
        box.innerHTML = (
          '<section class="afm-filter-feedback afm-bootstrap-feedback">'
          + '<h4 class="afm-filter-feedback-title">Bootstrap Check</h4>'
          + '<p class="afm-filter-term-warn">✗ ' + err + '</p></section>'
        );
      }}
    }}

    async function verifyBootstrapPlaylist(playlistId) {{
      if (!verifyUrl || !(playlistId || '').trim()) {{
        renderBootstrapFeedback(null);
        return;
      }}
      const limit = limitField ? limitField.value : '';
      const url = verifyUrl
        + '?playlist_id=' + encodeURIComponent(playlistId.trim())
        + '&limit=' + encodeURIComponent(limit || '30');
      try {{
        const resp = await fetch(url, {{ headers: {{ Accept: 'application/json' }} }});
        const data = await resp.json();
        renderBootstrapFeedback(data);
      }} catch (err) {{
        renderBootstrapFeedback({{
          ok: false,
          playlist_id: playlistId,
          error: 'Could not verify playlist.',
        }});
      }}
    }}

    function clearResults() {{
      results.innerHTML = '';
      input.setAttribute('aria-expanded', 'false');
      activeIndex = -1;
      currentPlaylists = [];
    }}

    function pickPlaylist(playlist) {{
      if (!playlist || !playlist.id) return;
      if (playlistIdField) playlistIdField.value = playlist.id;
      if (bootstrapEnabled) bootstrapEnabled.checked = true;
      clearResults();
      verifyBootstrapPlaylist(playlist.id);
      if (playlistIdField) playlistIdField.focus({{ preventScroll: true }});
    }}

    function setActive(index) {{
      const buttons = results.querySelectorAll('.afm-bootstrap-pick');
      buttons.forEach((btn, idx) => {{
        btn.classList.toggle('is-active', idx === index);
      }});
      activeIndex = index;
      const active = buttons[index];
      if (active) active.scrollIntoView({{ block: 'nearest' }});
    }}

    function renderResults(playlists, query) {{
      currentPlaylists = playlists;
      if (!playlists.length) {{
        if (query.length >= 2) {{
          results.innerHTML = (
            '<p class="afm-bootstrap-results-empty">No Navidrome playlists with '
            + '<strong>' + escapeHtml(query) + '</strong> in the title. '
            + 'Try a shorter name or paste a playlist id below.</p>'
          );
          input.setAttribute('aria-expanded', 'true');
        }} else {{
          clearResults();
        }}
        return;
      }}
      const countLabel = playlists.length + ' playlist' + (playlists.length === 1 ? '' : 's');
      const items = playlists.map((pl, idx) => {{
        const name = pl.name || pl.id || 'Playlist';
        const count = pl.count != null ? ' · ' + pl.count + ' tracks' : '';
        return (
          '<li role="presentation">'
          + '<button type="button" class="afm-bootstrap-pick" role="option" data-index="' + idx + '">'
          + escapeHtml(name) + escapeHtml(count)
          + '</button></li>'
        );
      }}).join('');
      results.innerHTML = (
        '<div class="afm-bootstrap-results">'
        + '<p class="afm-bootstrap-results-label">' + escapeHtml(countLabel) + ' — pick one</p>'
        + '<ul class="afm-bootstrap-menu" role="listbox">' + items + '</ul></div>'
      );
      input.setAttribute('aria-expanded', 'true');
      results.querySelectorAll('.afm-bootstrap-pick').forEach((btn) => {{
        btn.addEventListener('mousedown', (event) => {{
          event.preventDefault();
          const idx = parseInt(btn.getAttribute('data-index') || '-1', 10);
          if (idx >= 0 && currentPlaylists[idx]) pickPlaylist(currentPlaylists[idx]);
        }});
        btn.addEventListener('mouseenter', () => {{
          setActive(parseInt(btn.getAttribute('data-index') || '-1', 10));
        }});
      }});
      setActive(0);
    }}

    async function fetchPlaylists(query) {{
      if (!apiUrl) return [];
      const resp = await fetch(apiUrl + '?q=' + encodeURIComponent(query), {{
        headers: {{ Accept: 'application/json' }},
      }});
      if (!resp.ok) return [];
      const data = await resp.json();
      return Array.isArray(data.playlists) ? data.playlists : [];
    }}

    async function runSearch(query) {{
      const trimmed = query.trim();
      if (trimmed.length < 2) {{
        clearResults();
        return;
      }}
      try {{
        const playlists = await fetchPlaylists(trimmed);
        renderResults(playlists, trimmed);
      }} catch (err) {{
        clearResults();
      }}
    }}

    input.addEventListener('input', () => {{
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => runSearch(input.value), 280);
    }});

    input.addEventListener('keydown', (event) => {{
      if (event.key === 'Enter') {{
        event.preventDefault();
        if (activeIndex >= 0 && currentPlaylists[activeIndex]) {{
          pickPlaylist(currentPlaylists[activeIndex]);
        }}
        return;
      }}
      const buttons = results.querySelectorAll('.afm-bootstrap-pick');
      if (!buttons.length) return;
      if (event.key === 'ArrowDown') {{
        event.preventDefault();
        setActive(Math.min(activeIndex + 1, buttons.length - 1));
      }} else if (event.key === 'ArrowUp') {{
        event.preventDefault();
        setActive(Math.max(activeIndex - 1, 0));
      }} else if (event.key === 'Escape') {{
        clearResults();
      }}
    }});

    document.addEventListener('click', (event) => {{
      if (!picker.contains(event.target)) clearResults();
    }});

    if ((input.value || '').trim().length >= 2) {{
      runSearch(input.value);
    }}

    if (playlistIdField) {{
      playlistIdField.addEventListener('input', () => {{
        clearTimeout(verifyTimer);
        verifyTimer = setTimeout(() => verifyBootstrapPlaylist(playlistIdField.value), 400);
      }});
      if ((playlistIdField.value || '').trim()) {{
        verifyBootstrapPlaylist(playlistIdField.value);
      }}
    }}
    if (limitField) {{
      limitField.addEventListener('change', () => {{
        if (playlistIdField && (playlistIdField.value || '').trim()) {{
          verifyBootstrapPlaylist(playlistIdField.value);
        }}
      }});
    }}
  }})();

  (function initSeedTrackTypeahead() {{
    const picker = document.getElementById('afm-seed-track-picker');
    const input = document.getElementById('seed_search');
    const results = document.getElementById('afm-seed-track-results');
    const seedIdField = document.querySelector('input[name="seed_id"]');
    const typeSelect = document.getElementById('programming_type');
    if (!picker || !input || !results) return;

    const apiUrl = picker.getAttribute('data-track-search-url') || '';
    let debounceTimer = null;
    let activeIndex = -1;
    let currentTracks = [];

    function escapeHtml(text) {{
      return String(text || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }}

    function clearResults() {{
      results.innerHTML = '';
      input.setAttribute('aria-expanded', 'false');
      activeIndex = -1;
      currentTracks = [];
    }}

    function pickTrack(track) {{
      if (!track || !track.item_id) return;
      if (seedIdField) seedIdField.value = String(track.item_id);
      const title = track.title || 'Unknown';
      const artist = track.artist || 'Unknown';
      input.value = title + ' — ' + artist;
      if (typeSelect) {{
        typeSelect.value = 'similar_seed';
        typeSelect.dispatchEvent(new Event('change', {{ bubbles: true }}));
      }}
      clearResults();
      if (seedIdField) seedIdField.focus({{ preventScroll: true }});
    }}

    function setActive(index) {{
      const buttons = results.querySelectorAll('.afm-seed-track-pick');
      buttons.forEach((btn, idx) => {{
        btn.classList.toggle('is-active', idx === index);
      }});
      activeIndex = index;
      const active = buttons[index];
      if (active) active.scrollIntoView({{ block: 'nearest' }});
    }}

    function renderResults(tracks, query) {{
      currentTracks = tracks;
      if (!tracks.length) {{
        if (query.length >= 2) {{
          results.innerHTML = (
            '<p class="afm-seed-track-results-empty">No library tracks matching '
            + '<strong>' + escapeHtml(query) + '</strong>. Try a shorter query or paste an item id below.</p>'
          );
          input.setAttribute('aria-expanded', 'true');
        }} else {{
          clearResults();
        }}
        return;
      }}
      const countLabel = tracks.length + ' track' + (tracks.length === 1 ? '' : 's');
      const items = tracks.map((track, idx) => {{
        const title = track.title || 'Unknown';
        const artist = track.artist || 'Unknown';
        return (
          '<li role="presentation">'
          + '<button type="button" class="afm-seed-track-pick" role="option" data-index="' + idx + '">'
          + escapeHtml(title) + ' — ' + escapeHtml(artist)
          + '</button></li>'
        );
      }}).join('');
      results.innerHTML = (
        '<div class="afm-seed-track-results-panel">'
        + '<p class="afm-seed-track-results-label">' + escapeHtml(countLabel) + ' — pick one</p>'
        + '<ul class="afm-seed-track-menu" role="listbox">' + items + '</ul></div>'
      );
      input.setAttribute('aria-expanded', 'true');
      results.querySelectorAll('.afm-seed-track-pick').forEach((btn) => {{
        btn.addEventListener('mousedown', (event) => {{
          event.preventDefault();
          const idx = parseInt(btn.getAttribute('data-index') || '-1', 10);
          if (idx >= 0 && currentTracks[idx]) pickTrack(currentTracks[idx]);
        }});
        btn.addEventListener('mouseenter', () => {{
          setActive(parseInt(btn.getAttribute('data-index') || '-1', 10));
        }});
      }});
      setActive(0);
    }}

    async function fetchTracks(query) {{
      if (!apiUrl) return [];
      const resp = await fetch(apiUrl + '?q=' + encodeURIComponent(query), {{
        headers: {{ Accept: 'application/json' }},
      }});
      if (!resp.ok) return [];
      const data = await resp.json();
      return Array.isArray(data.tracks) ? data.tracks : [];
    }}

    async function runSearch(query) {{
      const trimmed = query.trim();
      if (trimmed.length < 2) {{
        clearResults();
        return;
      }}
      try {{
        const tracks = await fetchTracks(trimmed);
        renderResults(tracks, trimmed);
      }} catch (err) {{
        clearResults();
      }}
    }}

    input.addEventListener('input', () => {{
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => runSearch(input.value), 280);
    }});

    input.addEventListener('keydown', (event) => {{
      if (event.key === 'Enter') {{
        event.preventDefault();
        if (activeIndex >= 0 && currentTracks[activeIndex]) {{
          pickTrack(currentTracks[activeIndex]);
        }}
        return;
      }}
      const buttons = results.querySelectorAll('.afm-seed-track-pick');
      if (!buttons.length) return;
      if (event.key === 'ArrowDown') {{
        event.preventDefault();
        setActive(Math.min(activeIndex + 1, buttons.length - 1));
      }} else if (event.key === 'ArrowUp') {{
        event.preventDefault();
        setActive(Math.max(activeIndex - 1, 0));
      }} else if (event.key === 'Escape') {{
        clearResults();
      }}
    }});

    document.addEventListener('click', (event) => {{
      if (!picker.contains(event.target)) clearResults();
    }});

    if ((input.value || '').trim().length >= 2 && !(seedIdField && seedIdField.value)) {{
      runSearch(input.value);
    }}
  }})();

  (function initAnchorTypeahead() {{
    const picker = document.getElementById('afm-anchor-picker');
    const input = document.getElementById('anchor_search');
    const results = document.getElementById('afm-anchor-results');
    const anchorIdField = document.getElementById('anchor_id');
    const selectedPanel = document.getElementById('afm-anchor-selected');
    const selectedNameEl = document.getElementById('afm-anchor-selected-name');
    const clearBtn = document.getElementById('afm-anchor-clear');
    const typeSelect = document.getElementById('programming_type');
    if (!picker || !input || !results || !anchorIdField) return;

    const apiUrl = picker.getAttribute('data-anchor-search-url') || '';
    let debounceTimer = null;
    let activeIndex = -1;
    let currentAnchors = [];
    let selectedAnchorName = (input.value || '').trim();

    function escapeHtml(text) {{
      return String(text || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }}

    function clearResults() {{
      results.innerHTML = '';
      input.setAttribute('aria-expanded', 'false');
      activeIndex = -1;
      currentAnchors = [];
    }}

    function showSelected(anchor) {{
      if (!selectedPanel) return;
      const label = anchor.name || String(anchor.id);
      if (selectedNameEl) selectedNameEl.textContent = label;
      selectedPanel.classList.add('is-set');
      selectedPanel.hidden = false;
    }}

    function clearSelected() {{
      anchorIdField.value = '';
      selectedAnchorName = '';
      if (selectedPanel) {{
        selectedPanel.classList.remove('is-set');
        selectedPanel.hidden = true;
      }}
    }}

    function pickAnchor(anchor) {{
      if (!anchor || !anchor.id) return;
      anchorIdField.value = String(anchor.id);
      selectedAnchorName = anchor.name || String(anchor.id);
      input.value = selectedAnchorName;
      showSelected(anchor);
      if (typeSelect) {{
        typeSelect.value = 'alchemy_anchor';
        typeSelect.dispatchEvent(new Event('change', {{ bubbles: true }}));
      }}
      clearResults();
    }}

    function tryAutoPick(anchors, query) {{
      if (!anchors.length) return false;
      if (anchors.length === 1) {{
        pickAnchor(anchors[0]);
        return true;
      }}
      const q = (query || '').trim().toLowerCase();
      if (!q) return false;
      const exact = anchors.filter(
        (anchor) => String(anchor.name || '').trim().toLowerCase() === q
      );
      if (exact.length === 1) {{
        pickAnchor(exact[0]);
        return true;
      }}
      const prefix = anchors.filter(
        (anchor) => String(anchor.name || '').trim().toLowerCase().startsWith(q)
      );
      if (prefix.length === 1) {{
        pickAnchor(prefix[0]);
        return true;
      }}
      return false;
    }}

    function setActive(index) {{
      const buttons = results.querySelectorAll('.afm-seed-track-pick');
      buttons.forEach((btn, idx) => {{
        btn.classList.toggle('is-active', idx === index);
      }});
      activeIndex = index;
      const active = buttons[index];
      if (active) active.scrollIntoView({{ block: 'nearest' }});
    }}

    function renderResults(anchors, query) {{
      currentAnchors = anchors;
      if (!anchors.length) {{
        if (query.length >= 1) {{
          results.innerHTML = (
            '<p class="afm-seed-track-results-empty">No anchors matching '
            + '<strong>' + escapeHtml(query) + '</strong>.</p>'
          );
          input.setAttribute('aria-expanded', 'true');
        }} else {{
          clearResults();
        }}
        return;
      }}
      if (tryAutoPick(anchors, query)) return;
      const items = anchors.map((anchor, idx) => (
        '<li role="presentation">'
        + '<button type="button" class="afm-seed-track-pick" role="option" data-index="' + idx + '">'
        + escapeHtml(anchor.name || anchor.id)
        + '</button></li>'
      )).join('');
      results.innerHTML = (
        '<div class="afm-seed-track-results-panel">'
        + '<ul class="afm-seed-track-menu" role="listbox">' + items + '</ul></div>'
      );
      input.setAttribute('aria-expanded', 'true');
      results.querySelectorAll('.afm-seed-track-pick').forEach((btn) => {{
        btn.addEventListener('mousedown', (event) => {{
          event.preventDefault();
          const idx = parseInt(btn.getAttribute('data-index') || '-1', 10);
          if (idx >= 0 && currentAnchors[idx]) pickAnchor(currentAnchors[idx]);
        }});
        btn.addEventListener('mouseenter', () => {{
          setActive(parseInt(btn.getAttribute('data-index') || '-1', 10));
        }});
      }});
      setActive(0);
    }}

    async function runSearch(query) {{
      if (!apiUrl) return;
      try {{
        const resp = await fetch(apiUrl + '?q=' + encodeURIComponent(query), {{
          headers: {{ Accept: 'application/json' }},
        }});
        if (!resp.ok) return clearResults();
        const data = await resp.json();
        renderResults(Array.isArray(data.anchors) ? data.anchors : [], query.trim());
      }} catch (err) {{
        clearResults();
      }}
    }}

    input.addEventListener('input', () => {{
      const typed = (input.value || '').trim();
      if (
        anchorIdField.value
        && selectedAnchorName
        && typed.toLowerCase() !== selectedAnchorName.toLowerCase()
      ) {{
        clearSelected();
      }}
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => runSearch(input.value), 280);
    }});

    input.addEventListener('keydown', (event) => {{
      if (event.key === 'Enter') {{
        event.preventDefault();
        if (activeIndex >= 0 && currentAnchors[activeIndex]) {{
          pickAnchor(currentAnchors[activeIndex]);
        }}
        return;
      }}
      const buttons = results.querySelectorAll('.afm-seed-track-pick');
      if (!buttons.length) return;
      if (event.key === 'ArrowDown') {{
        event.preventDefault();
        setActive(Math.min(activeIndex + 1, buttons.length - 1));
      }} else if (event.key === 'ArrowUp') {{
        event.preventDefault();
        setActive(Math.max(activeIndex - 1, 0));
      }} else if (event.key === 'Escape') {{
        clearResults();
      }}
    }});

    if (clearBtn) {{
      clearBtn.addEventListener('click', () => {{
        clearSelected();
        input.value = '';
        clearResults();
        input.focus({{ preventScroll: true }});
      }});
    }}

    document.addEventListener('click', (event) => {{
      if (!picker.contains(event.target)) clearResults();
    }});

    if (anchorIdField.value && selectedAnchorName) {{
      showSelected({{ id: anchorIdField.value, name: selectedAnchorName }});
    }}
    if ((input.value || '').trim() && !anchorIdField.value) {{
      runSearch(input.value);
    }}
  }})();

  (function initCsvTermPickers() {{
    function setupPicker({{ picker, input, menu, targetField, apiUrl, dataKey, minChars }}) {{
      if (!picker || !input || !menu || !targetField || !apiUrl) return;
      let debounceTimer = null;
      let activeIndex = -1;
      let currentTerms = [];

      function closeMenu() {{
        menu.innerHTML = '';
        menu.hidden = true;
        input.setAttribute('aria-expanded', 'false');
        activeIndex = -1;
        currentTerms = [];
      }}

      function appendTerm(term) {{
        const value = String(term || '').trim();
        if (!value) return;
        const parts = (targetField.value || '')
          .split(',')
          .map((part) => part.trim())
          .filter(Boolean);
        if (!parts.some((part) => part.toLowerCase() === value.toLowerCase())) {{
          parts.push(value);
        }}
        targetField.value = parts.join(', ');
        input.value = '';
        closeMenu();
        input.focus();
        if (targetField.id === 'filter_mood_include') {{
          targetField.dispatchEvent(new Event('input', {{ bubbles: true }}));
        }}
      }}

      function setActive(index) {{
        const buttons = menu.querySelectorAll('.afm-artist-suggestion');
        buttons.forEach((btn, idx) => {{
          btn.classList.toggle('is-active', idx === index);
        }});
        activeIndex = index;
        const active = buttons[index];
        if (active) active.scrollIntoView({{ block: 'nearest' }});
      }}

      function renderMenu(terms) {{
        menu.innerHTML = '';
        currentTerms = terms;
        if (!terms.length) {{
          closeMenu();
          return;
        }}
        terms.forEach((term, idx) => {{
          const item = document.createElement('li');
          item.setAttribute('role', 'presentation');
          const btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'afm-artist-suggestion';
          btn.setAttribute('role', 'option');
          btn.textContent = term;
          btn.addEventListener('mousedown', (event) => {{
            event.preventDefault();
            appendTerm(term);
          }});
          btn.addEventListener('mouseenter', () => setActive(idx));
          item.appendChild(btn);
          menu.appendChild(item);
        }});
        menu.hidden = false;
        input.setAttribute('aria-expanded', 'true');
        setActive(0);
      }}

      async function fetchTerms(query) {{
        const resp = await fetch(apiUrl + '?q=' + encodeURIComponent(query), {{
          headers: {{ Accept: 'application/json' }},
        }});
        if (!resp.ok) return [];
        const data = await resp.json();
        return Array.isArray(data[dataKey]) ? data[dataKey] : [];
      }}

      input.addEventListener('input', () => {{
        clearTimeout(debounceTimer);
        const query = input.value.trim();
        if (query.length < minChars) {{
          closeMenu();
          return;
        }}
        debounceTimer = setTimeout(async () => {{
          try {{
            renderMenu(await fetchTerms(query));
          }} catch (err) {{
            closeMenu();
          }}
        }}, 280);
      }});

      input.addEventListener('keydown', (event) => {{
        if (event.key === 'Enter') {{
          event.preventDefault();
          if (activeIndex >= 0 && currentTerms[activeIndex]) {{
            appendTerm(currentTerms[activeIndex]);
          }}
          return;
        }}
        const buttons = menu.querySelectorAll('.afm-artist-suggestion');
        if (!buttons.length) return;
        if (event.key === 'ArrowDown') {{
          event.preventDefault();
          setActive(Math.min(activeIndex + 1, buttons.length - 1));
        }} else if (event.key === 'ArrowUp') {{
          event.preventDefault();
          setActive(Math.max(activeIndex - 1, 0));
        }} else if (event.key === 'Escape') {{
          closeMenu();
        }}
      }});

      document.addEventListener('click', (event) => {{
        if (!picker.contains(event.target)) closeMenu();
      }});
    }}

    setupPicker({{
      picker: document.getElementById('afm-genre-include-picker'),
      input: document.getElementById('genre_include_typeahead'),
      menu: document.getElementById('afm-genre-include-suggestions'),
      targetField: document.getElementById('filter_genre_include'),
      apiUrl: document.getElementById('afm-genre-include-picker')?.getAttribute('data-genre-search-url') || '',
      dataKey: 'genres',
      minChars: 1,
    }});
    setupPicker({{
      picker: document.getElementById('afm-genre-exclude-picker'),
      input: document.getElementById('genre_exclude_typeahead'),
      menu: document.getElementById('afm-genre-exclude-suggestions'),
      targetField: document.getElementById('filter_genre_exclude'),
      apiUrl: document.getElementById('afm-genre-exclude-picker')?.getAttribute('data-genre-search-url') || '',
      dataKey: 'genres',
      minChars: 1,
    }});
    setupPicker({{
      picker: document.getElementById('afm-mood-include-picker'),
      input: document.getElementById('mood_include_typeahead'),
      menu: document.getElementById('afm-mood-include-suggestions'),
      targetField: document.getElementById('filter_mood_include'),
      apiUrl: document.getElementById('afm-mood-include-picker')?.getAttribute('data-mood-search-url') || '',
      dataKey: 'moods',
      minChars: 1,
    }});
  }})();

  (function initTableFilters() {{
    function filterRows(inputId, rowSelector, attrName) {{
      const input = document.getElementById(inputId);
      if (!input) return;
      const rows = Array.from(document.querySelectorAll(rowSelector));
      if (!rows.length) return;
      const run = () => {{
        const q = (input.value || '').trim().toLowerCase();
        let visible = 0;
        rows.forEach((row) => {{
          const blob = (row.getAttribute(attrName) || '').toLowerCase();
          const show = !q || blob.includes(q);
          row.hidden = !show;
          if (show) visible += 1;
        }});
        input.setAttribute('aria-expanded', q ? 'true' : 'false');
        input.dataset.visibleCount = String(visible);
      }};
      input.addEventListener('input', run);
      run();
    }}
    filterRows('discover_filter', '#afm-discover-table tbody tr', 'data-discover-search');
    filterRows('afm-stations-filter', '#afm-stations-table tbody tr', 'data-station-search');
  }})();

  (function initPreviewTableSort() {{
    const table = document.querySelector('#preview-results .afm-sortable-table');
    if (!table) return;
    const tbody = table.querySelector('tbody');
    if (!tbody) return;
    const headers = table.querySelectorAll('.afm-sort-btn');
    let activeKey = null;
    let activeDir = 1;

    function sortRows(key, type, dir) {{
      const rows = Array.from(tbody.querySelectorAll('tr'));
      const mult = dir;
      rows.sort((a, b) => {{
        const av = a.getAttribute('data-sort-' + key) ?? '';
        const bv = b.getAttribute('data-sort-' + key) ?? '';
        if (type === 'number') {{
          const an = av === '' ? NaN : parseFloat(av);
          const bn = bv === '' ? NaN : parseFloat(bv);
          const aMissing = Number.isNaN(an);
          const bMissing = Number.isNaN(bn);
          if (aMissing && bMissing) return 0;
          if (aMissing) return 1;
          if (bMissing) return -1;
          return (an - bn) * mult;
        }}
        return av.localeCompare(bv, undefined, {{ sensitivity: 'base' }}) * mult;
      }});
      rows.forEach((row) => tbody.appendChild(row));
    }}

    headers.forEach((btn) => {{
      btn.addEventListener('click', () => {{
        const key = btn.getAttribute('data-sort-key');
        const type = btn.getAttribute('data-sort-type') || 'text';
        if (!key) return;
        let dir = 1;
        if (activeKey === key) {{
          dir = activeDir === 1 ? -1 : 1;
        }}
        activeKey = key;
        activeDir = dir;
        headers.forEach((header) => {{
          header.setAttribute(
            'aria-sort',
            header === btn ? (dir === 1 ? 'ascending' : 'descending') : 'none'
          );
        }});
        sortRows(key, type, dir);
      }});
    }});
  }})();
}})();
</script>
"""


_FILTER_REAPPLY_ACTIONS = frozenset({"apply_filters"})


@bp.route("/api/search-artists")
def search_artists_api():
    query = (request.args.get("q") or request.args.get("query") or "").strip()
    return jsonify({"artists": _search_artists(query)})


@bp.route("/api/search-playlists")
def search_playlists_api():
    query = (request.args.get("q") or request.args.get("query") or "").strip()
    return jsonify({"playlists": _search_playlists(query)})


@bp.route("/api/search-tracks")
def search_tracks_api():
    query = (request.args.get("q") or request.args.get("query") or "").strip()
    tracks = []
    for track in _search_tracks(query):
        tracks.append(
            {
                "item_id": str(track.get("item_id") or ""),
                "title": track.get("title") or "Unknown",
                "artist": track.get("author") or track.get("artist") or "Unknown",
            }
        )
    return jsonify({"tracks": tracks})


@bp.route("/api/search-anchors")
def search_anchors_api():
    query = (request.args.get("q") or request.args.get("query") or "").strip()
    anchors = [
        {
            "id": str(anchor.get("id") or ""),
            "name": str(anchor.get("name") or anchor.get("id") or "Anchor"),
        }
        for anchor in _search_anchors(query)
    ]
    return jsonify({"anchors": anchors})


@bp.route("/api/search-genres")
def search_genres_api():
    query = (request.args.get("q") or request.args.get("query") or "").strip()
    return jsonify({"genres": _search_genres(query)})


@bp.route("/api/search-moods")
def search_moods_api():
    query = (request.args.get("q") or request.args.get("query") or "").strip()
    return jsonify({"moods": _search_moods(query)})


@bp.route("/api/verify-bootstrap")
def verify_bootstrap_api():
    playlist_id = (request.args.get("playlist_id") or "").strip()
    try:
        limit = max(
            5,
            min(80, int(request.args.get("limit") or BOOTSTRAP_TRACK_LIMIT_DEFAULT)),
        )
    except ValueError:
        limit = BOOTSTRAP_TRACK_LIMIT_DEFAULT
    return jsonify(_verify_bootstrap_playlist(playlist_id, limit=limit))


@bp.route("/api/chat-preview", methods=["POST"])
def chat_preview_api():
    try:
        return jsonify(_run_chat_preview_from_form(request.form))
    except ChannelDesignerError as exc:
        slug = (
            request.form.get("editing_slug")
            or request.form.get("slug")
            or _slugify(request.form.get("name") or "channel")
        ).strip()
        _record_channel_error(slug, str(exc))
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        logger.exception("chat preview API failed")
        return jsonify({"ok": False, "error": f"Chat preview failed: {exc}"}), 500


@bp.route("/", methods=["GET", "POST"])
def home():
    try:
        return _home_page()
    except ChannelDesignerError as exc:
        return _plugin_error_page("Channel Designer", str(exc))
    except Exception as exc:
        logger.exception("alchemy_fm_bridge home failed")
        return _plugin_error_page(
            "Channel Designer",
            f"Unexpected error: {exc}. Check the AudioMuse container logs for the full traceback.",
        )


def _home_page():
    flashes = _FlashQueue()
    scroll_anchor = ""
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

    instant_scroll = False

    if request.method == "GET":
        edit_slug = (request.args.get("edit") or "").strip()
        draft_slug = (request.args.get("draft") or "").strip()
        if edit_slug:
            _restore_stashed_flashes(edit_slug, flashes)
            try:
                values, preview_tracks, channel_name = _apply_loaded_channel(edit_slug)
                if request.args.get("preview_ok"):
                    scroll_anchor = "preview-results"
                    values["scroll_to_preview"] = True
                    instant_scroll = True
                    if not flashes.html_for("preview-results"):
                        flashes.add(
                            f"Preview ready — {len(preview_tracks)} tracks from AudioMuse. "
                            "Review below, then deploy to Alchemy FM.",
                            "ok",
                            anchor="preview-results",
                        )
                    loaded = _load_saved_channel(edit_slug)
                    if loaded:
                        profile, _ = loaded
                        bootstrap_check = _apply_bootstrap_check(values, profile)
                        if bootstrap_check and not bootstrap_check.get("ok"):
                            flashes.add(
                                f"Bootstrap warning: {bootstrap_check.get('error')}",
                                "error",
                                anchor="bootstrap-opener",
                            )
                if request.args.get("deploy_ok"):
                    scroll_anchor = "step-deploy"
                    instant_scroll = True
                    if not flashes.html_for("step-deploy"):
                        flashes.add(
                            "Channel deployed on Alchemy FM — see Your Stations above and preview results below.",
                            "ok",
                            anchor="step-deploy",
                        )
            except ChannelDesignerError as exc:
                flashes.add(str(exc), "error")
        elif draft_slug:
            try:
                values, preview_tracks = _apply_draft_channel(draft_slug)
                scroll_anchor = "preview-results"
                values["scroll_to_preview"] = True
                instant_scroll = bool(request.args.get("chat_preview_ok"))
                if request.args.get("chat_preview_ok"):
                    flashes.add(
                        f"Chat preview — {len(preview_tracks)} tracks. "
                        "Tweak programming/filters, then deploy.",
                        "ok",
                        anchor="preview-results",
                    )
            except ChannelDesignerError as exc:
                flashes.add(str(exc), "error", anchor="chat-designer")
                scroll_anchor = "chat-designer"
                values["chat_designer_open"] = True
        elif request.args.get("new"):
            flashes.add(
                "New Channel — Design Programming, Preview Tracks, Then Deploy.",
                "ok",
                anchor="designer",
            )
            scroll_anchor = "designer"

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
            flashes.add(
                "Cluster playlist loaded into designer — preview, then deploy.",
                "ok",
                anchor="discover",
            )
            scroll_anchor = "discover"
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
                flashes.add(
                    f"Deleted station '{delete_slug or station_id}' from Alchemy FM.",
                    "ok",
                    anchor="stations",
                )
                scroll_anchor = "stations"
                logger.info("alchemy_fm_bridge deleted station id=%s slug=%s", station_id, delete_slug)
            except ChannelDesignerError as exc:
                flashes.add(str(exc), "error", anchor="stations")
                scroll_anchor = "stations"
            except ValueError as exc:
                flashes.add(str(exc), "error", anchor="stations")
                scroll_anchor = "stations"
        else:
            if (
                not action
                and not (request.form.get("pick_bootstrap_playlist") or "").strip()
                and not (request.form.get("pick_seed") or "").strip()
            ):
                # Implicit Enter-key submission from a plain text field activates no
                # button, so the browser omits action=... entirely. Surface that
                # clearly instead of silently re-rendering with nothing having happened.
                flashes.add(
                    "That didn't submit correctly — no action was recorded. If you "
                    "pressed Enter in a text field, click a button (Preview, Deploy, "
                    "etc.) instead.",
                    "error",
                )
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
                values["scroll_to_bootstrap"] = True
                limit = max(
                    5,
                    min(80, int(request.form.get("bootstrap_track_limit") or BOOTSTRAP_TRACK_LIMIT_DEFAULT)),
                )
                values["bootstrap_check"] = _verify_bootstrap_playlist(pick_bootstrap, limit=limit)

            pick_seed = (request.form.get("pick_seed") or "").strip()
            if pick_seed:
                values["seed_id"] = pick_seed
                values["programming_type"] = "similar_seed"
            elif action == "search_bootstrap_playlist":
                values["bootstrap_playlist_search"] = request.form.get("bootstrap_playlist_search") or ""
                values["bootstrap_playlist_results"] = _search_playlists(values["bootstrap_playlist_search"])
                values["scroll_to_bootstrap"] = True
            elif action == "verify_bootstrap_playlist":
                values["scroll_to_bootstrap"] = True
                limit = max(
                    5,
                    min(80, int(request.form.get("bootstrap_track_limit") or BOOTSTRAP_TRACK_LIMIT_DEFAULT)),
                )
                values["bootstrap_check"] = _verify_bootstrap_playlist(
                    request.form.get("bootstrap_playlist_id") or "",
                    limit=limit,
                )
            elif action == "apply_filters":
                values["scroll_to_preview"] = True
            elif action == "start_clustering":
                try:
                    _clustering_start()
                    flashes.add(
                        "Clustering started in AudioMuse. Check Active Tasks, then refresh this page.",
                        "ok",
                        anchor="discover",
                    )
                    scroll_anchor = "discover"
                except ChannelDesignerError as exc:
                    flashes.add(str(exc), "error", anchor="discover")
                    scroll_anchor = "discover"
            elif action.startswith("op_"):
                edit_slug = (request.form.get("editing_slug") or "").strip()
                station_id = int(request.form.get("op_station_id") or "0")
                if station_id <= 0:
                    flashes.add("No Alchemy FM station id for this operation.", "error", anchor="edit-toolbar")
                    scroll_anchor = "edit-toolbar"
                else:
                    try:
                        client = _client()
                        if action == "op_refresh_queue":
                            client.refresh_queue(station_id)
                            flashes.add("Queue refresh requested.", "ok", anchor="edit-toolbar")
                        elif action == "op_bootstrap":
                            client.bootstrap_station(station_id)
                            flashes.add("Station pool/bootstrap rebuild started.", "ok", anchor="edit-toolbar")
                        elif action == "op_rebuild_m3u":
                            client.rebuild_m3u(station_id)
                            flashes.add("queue.m3u rebuilt from database.", "ok", anchor="edit-toolbar")
                        elif action == "op_toggle_enabled":
                            remote = None
                            for st in client.test_connection():
                                if int(st.get("id") or 0) == station_id:
                                    remote = st
                                    break
                            current = bool(remote.get("enabled")) if remote else False
                            client.set_station_enabled(station_id, not current)
                            flashes.add(
                                "Station taken off air." if current else "Station put on air.",
                                "ok",
                                anchor="edit-toolbar",
                            )
                        elif action == "op_delete_artwork":
                            client.delete_artwork(station_id)
                            flashes.add("Station artwork removed.", "ok", anchor="edit-toolbar")
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
                            flashes.add("Station artwork uploaded.", "ok", anchor="edit-toolbar")
                        if edit_slug:
                            loaded = _apply_loaded_channel(edit_slug)
                            values, preview_tracks, _ = loaded
                        scroll_anchor = "edit-toolbar"
                    except ChannelDesignerError as exc:
                        flashes.add(str(exc), "error", anchor="edit-toolbar")
                        scroll_anchor = "edit-toolbar"
            elif action == "test":
                try:
                    client = _client()
                    deploy_warning = client.verify_deploy_ready()
                    stations = client.test_connection(skip_deploy_check=True)
                    if deploy_warning:
                        flashes.add(deploy_warning, "warn", anchor="step-deploy")
                    flashes.add(
                        f"Alchemy FM connected — {len(stations)} station(s) on air.",
                        "ok",
                        anchor="step-deploy",
                    )
                    scroll_anchor = "step-deploy"
                except ChannelDesignerError as exc:
                    flashes.add(str(exc), "error", anchor="step-deploy")
                    scroll_anchor = "step-deploy"
            elif action in ("preview", "push", "chat_preview"):
                try:
                    for_deploy = action == "push"
                    profile = profile_from_form(request.form, for_deploy=for_deploy)
                    slug = profile["station"]["slug"]
                    if for_deploy:
                        profile = _merge_saved_programming_if_needed(profile, slug)
                        # Step 2: if form still incomplete after merge, fail with clear message
                        if _programming_incomplete(profile.get("programming") or {}):
                            raise ChannelDesignerError(
                                "Step 2 programming is incomplete. Fill in the active programming type "
                                "(sonic vibe, mood cluster, seed track, etc.) or run Step 3 Preview first."
                            )
                    values = _form_values_from_profile(profile)
                    if (request.form.get("editing_slug") or "").strip():
                        values["editing_slug"] = request.form.get("editing_slug").strip()

                    if action == "chat_preview":
                        result = _run_chat_preview_from_form(request.form)
                        if _is_chat_preview_ajax():
                            return jsonify(result)
                        values["scroll_to_preview"] = True
                        values["chat_designer_open"] = True
                        values["chat_prompt"] = (request.form.get("chat_prompt") or "").strip()
                        scroll_anchor = "preview-results"
                        flashes.add(
                            f"Chat preview — {result['track_count']} tracks. Tweak programming/filters, then deploy.",
                            "ok",
                            anchor="preview-results",
                        )
                    elif action == "preview":
                        unfiltered = _merged_programming_tracks_unfiltered(profile, slug)
                        preview_tracks = apply_track_filters(unfiltered, profile)
                        if not preview_tracks:
                            raise ChannelDesignerError(_preview_empty_message(unfiltered, profile))
                        _apply_filter_feedback(values, profile, unfiltered, preview_tracks)
                        bootstrap_check = _apply_bootstrap_check(values, profile)
                        item_ids = [t["item_id"] for t in preview_tracks]
                        _record_audition(slug, item_ids)
                        if (profile.get("living") or {}).get("enabled"):
                            _add_to_pool(slug, item_ids, source="preview")
                        _save_channel(
                            profile,
                            preview_ids=item_ids,
                            unfiltered_preview_ids=[t["item_id"] for t in unfiltered],
                        )
                        edit_slug = (request.form.get("editing_slug") or slug).strip()
                        values["preview_last_error"] = ""
                        _stash_flashes(edit_slug, flashes)
                        return redirect(
                            url_for("alchemy_fm_bridge.home", edit=edit_slug, preview_ok=1)
                            + "#preview-results"
                        )
                    elif action == "push":
                        client = _client()
                        client.verify_credentials()
                        deploy_warning = client.verify_deploy_ready(strict=False)
                        unfiltered = _deploy_unfiltered_tracks(profile, slug)
                        preview_tracks = apply_track_filters(unfiltered, profile)
                        if not preview_tracks:
                            raise ChannelDesignerError(
                                "No tracks to deploy. Preview programming first or broaden your criteria."
                            )
                        _apply_filter_feedback(values, profile, unfiltered, preview_tracks)
                        bootstrap_check = _apply_bootstrap_check(values, profile)
                        if bootstrap_check and not bootstrap_check.get("ok"):
                            err = bootstrap_check.get("error") or "Bootstrap playlist could not be verified."
                            flashes.add(
                                f"Optional bootstrap opener skipped: {err}",
                                "warn",
                                anchor="step-deploy",
                            )
                            profile = dict(profile)
                            profile["bootstrap"] = None
                        payload = channel_profile_to_alchemy_payload(profile, preview_tracks)
                        slug = payload["slug"]
                        editing = (request.form.get("editing_slug") or "").strip()
                        alchemy_station_id = int(
                            request.form.get("alchemy_station_id")
                            or request.form.get("edit_station_id")
                            or 0
                        ) or None
                        create_new = not editing and not alchemy_station_id
                        item_ids = [t["item_id"] for t in preview_tracks]
                        _record_audition(slug, item_ids)
                        if (profile.get("living") or {}).get("enabled"):
                            _add_to_pool(slug, item_ids, source="preview")
                        station, push_action, bootstrap_warning = client.push_station(
                            payload,
                            slug=slug,
                            station_id=alchemy_station_id if editing else None,
                            create_new=create_new,
                            bootstrap=bool(payload.get("bootstrap_queue")),
                        )
                        deploy_slug = str(station.get("slug") or slug).strip()
                        slug_note = ""
                        if push_action == "created" and deploy_slug.lower() != slug.strip().lower():
                            slug_note = (
                                f" Alchemy assigned slug '{deploy_slug}' "
                                f"(requested '{slug}' was already in use)."
                            )
                        _save_channel(
                            profile,
                            preview_ids=item_ids,
                            unfiltered_preview_ids=[t["item_id"] for t in unfiltered],
                            station=station,
                            action=push_action,
                        )
                        if deploy_warning:
                            flashes.add(deploy_warning, "warn", anchor="step-deploy")
                        queued_note = (
                            f", {station.get('queued_count')} queued"
                            if station.get("queued_count") is not None
                            else ""
                        )
                        flashes.add(
                            f"Channel '{station.get('name')}' {push_action} on Alchemy FM "
                            f"(id {station.get('id')}{queued_note}) — see Your Stations above.{slug_note}",
                            "ok",
                            anchor="step-deploy",
                        )
                        if bootstrap_warning:
                            flashes.add(bootstrap_warning, "warn", anchor="step-deploy")
                            flashes.add(bootstrap_warning, "warn", anchor="global")
                        scroll_anchor = "step-deploy"
                        values = _form_values_from_profile(profile)
                        values["editing_slug"] = deploy_slug
                        values["deploy_last_error"] = ""
                        if station:
                            values["edit_station_id"] = int(station.get("id") or 0)
                            values["alchemy_station_id"] = int(station.get("id") or 0)
                            values["edit_on_air"] = bool(station.get("enabled"))
                            values["edit_queued"] = station.get("queued_count", "?")
                        logger.info(
                            "alchemy_fm_bridge deployed slug=%s action=%s type=%s",
                            deploy_slug,
                            push_action,
                            profile["programming"]["type"],
                        )
                        _stash_flashes(deploy_slug, flashes)
                        return redirect(
                            url_for("alchemy_fm_bridge.home", edit=deploy_slug, deploy_ok=1)
                            + "#step-deploy"
                        )
                except ChannelDesignerError as exc:
                    slug = (
                        request.form.get("editing_slug")
                        or request.form.get("slug")
                        or _slugify(request.form.get("name") or "channel")
                    ).strip()
                    _record_channel_error(slug, str(exc))
                    if action == "push":
                        values["deploy_last_error"] = str(exc)
                    if action == "preview":
                        values["preview_last_error"] = str(exc)
                    if action == "chat_preview" and _is_chat_preview_ajax():
                        return jsonify({"ok": False, "error": str(exc)}), 400
                    error_anchor = {
                        "chat_preview": "chat-designer",
                        "preview": "step-programming",
                        "push": "step-deploy",
                    }.get(action, "step-preview")
                    if action == "chat_preview":
                        values["chat_designer_open"] = True
                        values["chat_prompt"] = (request.form.get("chat_prompt") or "").strip()
                    flashes.add(str(exc), "error", anchor=error_anchor)
                    if action == "push":
                        flashes.add(str(exc), "error", anchor="global")
                    scroll_anchor = error_anchor
                except Exception as exc:
                    if action == "chat_preview" and _is_chat_preview_ajax():
                        logger.exception("chat preview failed")
                        return jsonify({"ok": False, "error": f"Chat preview failed: {exc}"}), 500
                    if action == "push":
                        slug = (
                            request.form.get("editing_slug")
                            or request.form.get("slug")
                            or _slugify(request.form.get("name") or "channel")
                        ).strip()
                        message = f"Deploy failed: {exc}"
                        logger.exception("alchemy_fm_bridge deploy failed slug=%s", slug)
                        _record_channel_error(slug, message)
                        values["deploy_last_error"] = message
                        flashes.add(message, "error", anchor="step-deploy")
                        flashes.add(message, "error", anchor="global")
                        scroll_anchor = "step-deploy"
                    elif action == "preview":
                        slug = (
                            request.form.get("editing_slug")
                            or request.form.get("slug")
                            or _slugify(request.form.get("name") or "channel")
                        ).strip()
                        message = f"Preview failed: {exc}"
                        logger.exception("alchemy_fm_bridge preview failed slug=%s", slug)
                        _record_channel_error(slug, message)
                        values["preview_last_error"] = message
                        flashes.add(message, "error", anchor="step-programming")
                        scroll_anchor = "step-programming"
                    else:
                        raise

    if request.method == "POST":
        post_action = (request.form.get("action") or "").strip()
        reapply_filters = post_action in _FILTER_REAPPLY_ACTIONS
        if (not preview_tracks or reapply_filters) and post_action != "preview":
            restored, values = _try_restore_preview_state(
                values,
                request.form,
                reapply_filters=reapply_filters,
            )
            if restored:
                preview_tracks = restored
            if post_action == "apply_filters":
                if preview_tracks:
                    flashes.add(
                        f"Filters applied — {len(preview_tracks)} track(s) in preview.",
                        "ok",
                        anchor="preview-results",
                    )
                    scroll_anchor = "preview-results"
                else:
                    flashes.add(
                        "No tracks left after filters. Broaden your rules or run Preview Programming again.",
                        "error",
                        anchor="step-filters",
                    )
                    scroll_anchor = "step-filters"

    editing_slug = (values.get("editing_slug") or "").strip()
    if not (values.get("deploy_last_error") or "").strip():
        slug_for_error = _channel_slug_from_values(
            values,
            request.form if request.method == "POST" else None,
        )
        if slug_for_error:
            values["deploy_last_error"] = _channel_last_error(slug_for_error)
    if not (values.get("preview_last_error") or "").strip():
        slug_for_preview_error = _channel_slug_from_values(
            values,
            request.form if request.method == "POST" else None,
        )
        if slug_for_preview_error:
            values["preview_last_error"] = _channel_last_error(slug_for_preview_error)
    stations_html = _stations_section_html(
        editing_slug or None,
        flash_html=flashes.html_for("stations"),
    )
    edit_bar = _edit_toolbar_html(values, flash_html=flashes.html_for("edit-toolbar"))
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
    scroll_to_bootstrap = bool(values.get("scroll_to_bootstrap"))
    if scroll_to_preview and not scroll_anchor:
        scroll_anchor = "preview-results"
    if scroll_to_bootstrap and not scroll_anchor:
        scroll_anchor = "bootstrap-opener"
    if scroll_anchor in ("step-deploy", "preview-results", "step-programming"):
        instant_scroll = True
    if not preview_tracks:
        preview_tracks = _preview_tracks_for_values(values)
    has_preview_tracks = len(preview_tracks) > 0
    preview_block = _preview_results_html(
        preview_tracks,
        highlight=scroll_to_preview,
        flash_html=flashes.html_for("preview-results"),
    )
    audition_block = (
        "<section class='afm-panel'>"
        + _panel_heading("Audition History", "Recent Preview Runs for This Channel")
        + f"{_audition_history_html(editing_slug or None)}"
        + "</section>"
    )
    chat_preview_url = html.escape(url_for("alchemy_fm_bridge.chat_preview_api"))
    track_search_url = html.escape(url_for("alchemy_fm_bridge.search_tracks_api"))
    anchor_search_url = html.escape(url_for("alchemy_fm_bridge.search_anchors_api"))
    genre_search_url = html.escape(url_for("alchemy_fm_bridge.search_genres_api"))
    mood_search_url = html.escape(url_for("alchemy_fm_bridge.search_moods_api"))
    bootstrap_verify_url = html.escape(url_for("alchemy_fm_bridge.verify_bootstrap_api"))
    designer_form = (
        f"{designer_section_open}"
        f"<form method='post' id='afm-designer-form' class='afm-designer-form' "
        f"data-chat-preview-url='{chat_preview_url}'>"
        f"{_designer_flow_overview_html(flash_html=flashes.html_for('designer'))}"
        f"{_chat_designer_fields_html(values, flash_html=flashes.html_for('chat-designer'))}"
        f"{_discover_channels_html(flash_html=flashes.html_for('discover'))}"
        f"{_station_identity_fields_html(values)}"
        f"{_programming_fields_html(values, track_search_url=track_search_url, anchor_search_url=anchor_search_url, flash_html=flashes.html_for('step-programming'))}"
        f"{_preview_step_html(values, show_results_jump=has_preview_tracks, flash_html=flashes.html_for('step-preview'))}"
        f"{_filters_fields_html(values, show_results_jump=has_preview_tracks, flash_html=flashes.html_for('step-filters'), genre_search_url=genre_search_url, mood_search_url=mood_search_url)}"
        f"{_bootstrap_fields_html(values, flash_html=flashes.html_for('bootstrap-opener'), verify_url=bootstrap_verify_url)}"
        f"{_living_fields_html(values)}"
        f"{_playback_rules_fields_html(values)}"
        f"{_deploy_actions_fields_html(values, flash_html=flashes.html_for('step-deploy'))}"
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
    deploy_error_message = (values.get("deploy_last_error") or "").strip()
    pinned_deploy_error = _pinned_deploy_error_html(deploy_error_message)
    shell_class = "afm-shell has-deploy-error-pinned" if pinned_deploy_error else "afm-shell"
    body = (
        f"{_page_styles()}"
        f'<div class="{shell_class}">'
        f"{pinned_deploy_error}"
        f"{_page_header_html()}"
        f"{flashes.html_for('global')}"
        f"{main_flow}"
        "</div>"
        f"{_page_script(_mood_centroids_data(), mood_labels, scroll_anchor=scroll_anchor, scroll_to_preview=scroll_to_preview, scroll_to_bootstrap=scroll_to_bootstrap, instant_scroll=instant_scroll)}"
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
        f"placeholder='Required if preview returns Unauthorized' value='{html.escape(audiomuse_api_token)}'>"
        "<p class='hint'>Copy from AudioMuse Settings → API. Needed for Preview/Deploy when API auth is on "
        "and the worker/cron cannot reuse your browser session.</p></div>"
        "<div><label>AudioMuse API URL (optional, for worker/cron)</label>"
        f"<input name='audiomuse_api_url' placeholder='Leave blank for Channel Designer' "
        f"value='{html.escape(audiomuse_api_url)}'>"
        "<p class='hint'><strong>Leave blank</strong> for preview and deploy in the browser. "
        "Only set this for living-channel cron (<code>on_song_analyzed</code>) — use your AudioMuse LAN URL "
        "(e.g. <code>http://192.168.1.10:8387</code>), <em>not</em> the Alchemy FM URL.</p></div>"
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
