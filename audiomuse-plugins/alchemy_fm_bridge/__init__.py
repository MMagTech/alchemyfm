"""Push Alchemy FM radio stations from AudioMuse programming."""

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

from plugin.api import get_db, get_setting, set_setting, render_page, manage_plugins_url, logger, table


class AlchemyFmError(Exception):
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
        }

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=self.headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
                if not body:
                    return None
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            message = detail
            try:
                parsed = json.loads(detail)
                if isinstance(parsed, dict) and parsed.get("detail"):
                    message = str(parsed["detail"])
            except json.JSONDecodeError:
                pass
            raise AlchemyFmError(message or f"HTTP {exc.code}", status=exc.code) from exc
        except urllib.error.URLError as exc:
            raise AlchemyFmError(f"Could not reach Alchemy FM at {self.base_url}: {exc.reason}") from exc

    def test_connection(self) -> list[dict[str, Any]]:
        stations = self._request("GET", "/api/admin/stations")
        if not isinstance(stations, list):
            raise AlchemyFmError("Unexpected response from /api/admin/stations")
        return stations

    def list_stations(self) -> list[dict[str, Any]]:
        return self.test_connection()

    def find_station_by_slug(self, slug: str) -> dict[str, Any] | None:
        slug = slug.strip().lower()
        for station in self.list_stations():
            if str(station.get("slug", "")).lower() == slug:
                return station
        return None

    def create_station(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = self._request("POST", "/api/admin/stations", payload)
        if not isinstance(result, dict):
            raise AlchemyFmError("Unexpected response when creating station")
        return result

    def update_station(self, station_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        result = self._request("PUT", f"/api/admin/stations/{station_id}", payload)
        if not isinstance(result, dict):
            raise AlchemyFmError("Unexpected response when updating station")
        return result

    def bootstrap_station(self, station_id: int) -> dict[str, Any]:
        result = self._request("POST", f"/api/admin/stations/{station_id}/bootstrap")
        if not isinstance(result, dict):
            raise AlchemyFmError("Unexpected response when bootstrapping station")
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


def fetch_audiomuse_json(
    path: str,
    *,
    base_url: str,
    api_token: str = "",
    params: dict[str, str] | None = None,
    timeout: float = 20.0,
) -> Any:
    query = ""
    if params:
        query = "?" + urllib.parse.urlencode(params)
    url = f"{base_url.rstrip('/')}{path}{query}"
    headers = {"Accept": "application/json"}
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise AlchemyFmError(detail or f"AudioMuse API HTTP {exc.code}", status=exc.code) from exc
    except urllib.error.URLError as exc:
        raise AlchemyFmError(f"Could not reach AudioMuse API: {exc.reason}") from exc


bp = Blueprint("alchemy_fm_bridge", __name__)

SOURCE_TYPES = (
    ("alchemy_anchor", "Song Alchemy anchor"),
    ("similar_seed", "Similar tracks (seed track)"),
)

CONTINUATION_MODES = (
    ("source_only", "Stay in source (allow repeats)"),
    ("similar_to_seed", "Similar to source seed"),
    ("similar_to_last", "Similar to last played"),
)


def migrate(db) -> None:
    cur = db.cursor()
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
        raise AlchemyFmError("Set your Alchemy FM URL in plugin settings first.")
    if not password:
        raise AlchemyFmError("Set your Alchemy FM admin password in plugin settings first.")
    return AlchemyFmClient(base_url, username, password)


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


def _slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "station"


def _normalize_mount(value: str) -> str:
    value = value.strip()
    if not value.startswith("/"):
        value = f"/{value}"
    return value


def _station_payload_from_form(form) -> dict[str, Any]:
    name = (form.get("name") or "").strip()
    if not name:
        raise AlchemyFmError("Station name is required.")

    slug = (form.get("slug") or "").strip() or _slugify(name)
    mount = _normalize_mount(form.get("icecast_mount") or slug)
    source_type = (form.get("source_type") or "alchemy_anchor").strip()
    source_ref = (form.get("source_ref") or "").strip()
    if source_type not in {key for key, _label in SOURCE_TYPES}:
        raise AlchemyFmError(f"Unsupported source type: {source_type}")
    if not source_ref:
        raise AlchemyFmError("Programming source is required (anchor id or seed track id).")

    continuation_mode = (form.get("continuation_mode") or "source_only").strip()
    if continuation_mode not in {key for key, _label in CONTINUATION_MODES}:
        raise AlchemyFmError(f"Unsupported continuation mode: {continuation_mode}")

    return {
        "name": name,
        "slug": slug,
        "description": (form.get("description") or "").strip()[:120],
        "icecast_mount": mount,
        "enabled": form.get("enabled") == "on",
        "source_type": source_type,
        "source_ref": source_ref,
        "queue_target": max(5, min(200, int(form.get("queue_target") or 30))),
        "refresh_threshold": max(1, min(100, int(form.get("refresh_threshold") or 10))),
        "artist_separation_minutes": max(0, int(form.get("artist_separation_minutes") or 90)),
        "continuation_mode": continuation_mode,
        "bootstrap_queue": form.get("bootstrap_queue") == "on",
    }


def _save_profile(slug: str, station: dict[str, Any], config: dict[str, Any], action: str) -> None:
    db = get_db()
    cur = db.cursor()
    profiles = table("profiles")
    cur.execute(
        "INSERT INTO "
        + profiles
        + " (slug, alchemy_station_id, config_json, last_pushed_at, last_action, last_error) "
        "VALUES (%s, %s, %s, to_char(now(), 'YYYY-MM-DD HH24:MI:SS'), %s, '') "
        "ON CONFLICT (slug) DO UPDATE SET "
        "alchemy_station_id = EXCLUDED.alchemy_station_id, "
        "config_json = EXCLUDED.config_json, "
        "last_pushed_at = EXCLUDED.last_pushed_at, "
        "last_action = EXCLUDED.last_action, "
        "last_error = ''",
        (slug, int(station.get("id") or 0), json.dumps(config), action),
    )
    db.commit()
    cur.close()


def _record_profile_error(slug: str, message: str) -> None:
    db = get_db()
    cur = db.cursor()
    profiles = table("profiles")
    cur.execute(
        "INSERT INTO "
        + profiles
        + " (slug, config_json, last_error) VALUES (%s, '{}', %s) "
        "ON CONFLICT (slug) DO UPDATE SET last_error = EXCLUDED.last_error",
        (slug, message[:500]),
    )
    db.commit()
    cur.close()


def _profile_rows() -> list[tuple]:
    db = get_db()
    cur = db.cursor()
    profiles = table("profiles")
    try:
        cur.execute(
            "SELECT slug, alchemy_station_id, last_pushed_at, last_action, last_error "
            "FROM "
            + profiles
            + " ORDER BY last_pushed_at DESC NULLS LAST, slug ASC"
        )
        rows = cur.fetchall()
    except Exception:
        db.rollback()
        rows = []
    cur.close()
    return rows


def _anchors() -> list[dict[str, Any]]:
    try:
        data = fetch_audiomuse_json(
            "/api/anchors",
            base_url=_audiomuse_base_url(),
            api_token=_audiomuse_token(),
        )
    except AlchemyFmError:
        return []
    anchors = data.get("anchors") if isinstance(data, dict) else None
    if not isinstance(anchors, list):
        return []
    return [a for a in anchors if isinstance(a, dict) and a.get("id")]


def _search_tracks(query: str) -> list[dict[str, Any]]:
    query = query.strip()
    if len(query) < 2:
        return []
    try:
        data = fetch_audiomuse_json(
            "/api/search_tracks",
            base_url=_audiomuse_base_url(),
            api_token=_audiomuse_token(),
            params={"search_query": query, "end": "20"},
        )
    except AlchemyFmError:
        return []
    if not isinstance(data, list):
        return []
    return [t for t in data if isinstance(t, dict) and t.get("item_id")]


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


def _anchor_options(selected: str) -> str:
    options = ['<option value="">Choose an anchor…</option>']
    for anchor in _anchors():
        anchor_id = str(anchor["id"])
        name = anchor.get("name") or f"Anchor {anchor_id}"
        sel = " selected" if anchor_id == selected else ""
        options.append(
            f'<option value="{html.escape(anchor_id)}"{sel}>{html.escape(name)}</option>'
        )
    return "".join(options)


def _form_html(
  *,
  flash: str = "",
  values: dict[str, Any] | None = None,
  search_query: str = "",
  search_results: list[dict[str, Any]] | None = None,
) -> str:
    values = values or {}
    source_type = values.get("source_type", "alchemy_anchor")
    continuation_mode = values.get("continuation_mode", "source_only")
    seed_hidden = "" if source_type == "similar_seed" else " hidden"
    anchor_hidden = "" if source_type == "alchemy_anchor" else " hidden"

    search_block = ""
    if source_type == "similar_seed":
        results_html = ""
        if search_results:
            items = []
            for track in search_results:
                item_id = str(track.get("item_id"))
                title = track.get("title") or "Unknown"
                artist = track.get("author") or track.get("artist") or "Unknown"
                items.append(
                    "<li>"
                    f'<button type="submit" name="pick_seed" value="{html.escape(item_id)}" '
                    'formnovalidate style="width:100%;text-align:left;padding:0.5rem;">'
                    f"{html.escape(title)} — {html.escape(artist)} "
                    f'<span style="opacity:0.7">({html.escape(item_id)})</span>'
                    "</button></li>"
                )
            results_html = "<ul style='list-style:none;padding:0;margin:0.5rem 0;'>" + "".join(items) + "</ul>"
        search_block = (
            f'<div id="seed-search"{seed_hidden}>'
            '<label>Search seed track</label>'
            f'<input name="seed_search" value="{html.escape(search_query)}" placeholder="Artist or title…"> '
            '<button type="submit" name="action" value="search_seed" formnovalidate>Search</button>'
            f"{results_html}"
            '<label>Seed track id</label>'
            f'<input name="source_ref" id="source_ref_seed" value="{html.escape(str(values.get("source_ref", "")))}"'
            f'{" required" if source_type == "similar_seed" else ""}>'
            "</div>"
        )

    return (
        f"{flash}"
        "<form method='post' class='alchemy-fm-form' style='display:grid;gap:1rem;max-width:42rem;'>"
        "<div><label>Station name</label>"
        f"<input name='name' required value='{html.escape(str(values.get('name', '')))}'></div>"
        "<div><label>Slug (optional)</label>"
        f"<input name='slug' placeholder='auto from name' value='{html.escape(str(values.get('slug', '')))}'></div>"
        "<div><label>Description</label>"
        f"<textarea name='description' maxlength='120' rows='2'>{html.escape(str(values.get('description', '')))}</textarea></div>"
        "<div><label>Icecast mount</label>"
        f"<input name='icecast_mount' placeholder='/yachtrock' value='{html.escape(str(values.get('icecast_mount', '')))}'></div>"
        "<div><label>Source type</label>"
        f"<select name='source_type'>"
        f"{_select_options(SOURCE_TYPES, source_type)}</select></div>"
        f"<div id='anchor-picker'{anchor_hidden}><label>Alchemy anchor</label>"
        f"<select name='source_ref' id='source_ref_anchor'"
        f"{' required' if source_type == 'alchemy_anchor' else ''}>"
        f"{_anchor_options(str(values.get('source_ref', '')))}</select></div>"
        f"{search_block}"
        "<div><label>When pool runs low</label>"
        f"<select name='continuation_mode'>{_select_options(CONTINUATION_MODES, continuation_mode)}</select></div>"
        "<div style='display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0.75rem;'>"
        "<div><label>Queue target</label>"
        f"<input type='number' name='queue_target' min='5' max='200' value='{html.escape(str(values.get('queue_target', 30)))}'></div>"
        "<div><label>Refresh below</label>"
        f"<input type='number' name='refresh_threshold' min='1' max='100' value='{html.escape(str(values.get('refresh_threshold', 10)))}'></div>"
        "<div><label>Artist separation (min)</label>"
        f"<input type='number' name='artist_separation_minutes' min='0' value='{html.escape(str(values.get('artist_separation_minutes', 90)))}'></div>"
        "</div>"
        "<label><input type='checkbox' name='enabled'"
        f"{' checked' if values.get('enabled', True) else ''}> Start station on air</label>"
        "<label><input type='checkbox' name='bootstrap_queue'"
        f"{' checked' if values.get('bootstrap_queue', True) else ''}> Bootstrap queue after push</label>"
        "<div style='display:flex;gap:0.75rem;flex-wrap:wrap;'>"
        "<button type='submit' name='action' value='push'>Push to Alchemy FM</button>"
        "<button type='submit' name='action' value='test' formnovalidate>Test connection</button>"
        "</div>"
        "</form>"
    )


def _profiles_html() -> str:
    rows = _profile_rows()
    if not rows:
        return "<p>No pushes yet. Create a station above to send programming to Alchemy FM.</p>"
    body = (
        "<table style='width:100%;border-collapse:collapse;margin-top:1.5rem;'>"
        "<thead><tr>"
        "<th style='text-align:left;padding:0.5rem;border-bottom:1px solid #ddd;'>Slug</th>"
        "<th style='text-align:left;padding:0.5rem;border-bottom:1px solid #ddd;'>Alchemy id</th>"
        "<th style='text-align:left;padding:0.5rem;border-bottom:1px solid #ddd;'>Last push</th>"
        "<th style='text-align:left;padding:0.5rem;border-bottom:1px solid #ddd;'>Action</th>"
        "<th style='text-align:left;padding:0.5rem;border-bottom:1px solid #ddd;'>Error</th>"
        "</tr></thead><tbody>"
    )
    for slug, station_id, pushed_at, action, error in rows:
        body += (
            "<tr>"
            f"<td style='padding:0.5rem;border-bottom:1px solid #eee;'>{html.escape(str(slug))}</td>"
            f"<td style='padding:0.5rem;border-bottom:1px solid #eee;'>{html.escape(str(station_id or ''))}</td>"
            f"<td style='padding:0.5rem;border-bottom:1px solid #eee;'>{html.escape(str(pushed_at or ''))}</td>"
            f"<td style='padding:0.5rem;border-bottom:1px solid #eee;'>{html.escape(str(action or ''))}</td>"
            f"<td style='padding:0.5rem;border-bottom:1px solid #eee;'>{html.escape(str(error or ''))}</td>"
            "</tr>"
        )
    return body + "</tbody></table>"


def _remote_stations_html() -> str:
    try:
        stations = _client().list_stations()
    except AlchemyFmError as exc:
        return f"<p>Could not load remote stations: {html.escape(str(exc))}</p>"
    if not stations:
        return "<p>Alchemy FM has no stations yet.</p>"
    items = []
    for station in stations:
        name = station.get("name") or station.get("slug") or "Station"
        slug = station.get("slug") or ""
        enabled = "on air" if station.get("enabled") else "off"
        source = f"{station.get('source_type')} / {station.get('source_ref')}"
        items.append(
            "<li>"
            f"<strong>{html.escape(str(name))}</strong> "
            f"({html.escape(str(slug))}, {html.escape(enabled)}) — "
            f"{html.escape(str(source))}"
            "</li>"
        )
    return "<h3>Stations on Alchemy FM</h3><ul>" + "".join(items) + "</ul>"


@bp.route("/")
def home():
    flash = ""
    values: dict[str, Any] = {
        "source_type": request.values.get("source_type", "alchemy_anchor"),
        "continuation_mode": "source_only",
        "queue_target": 30,
        "refresh_threshold": 10,
        "artist_separation_minutes": 90,
        "enabled": True,
        "bootstrap_queue": True,
    }
    search_query = ""
    search_results: list[dict[str, Any]] | None = None

    if request.method == "POST":
        action = (request.form.get("action") or "push").strip()
        values.update({key: request.form.get(key, values.get(key, "")) for key in request.form})
        values["enabled"] = request.form.get("enabled") == "on"
        values["bootstrap_queue"] = request.form.get("bootstrap_queue") == "on"

        pick_seed = (request.form.get("pick_seed") or "").strip()
        if pick_seed:
            values["source_ref"] = pick_seed
            values["source_type"] = "similar_seed"

        if action == "search_seed":
            search_query = (request.form.get("seed_search") or "").strip()
            search_results = _search_tracks(search_query)
        elif action == "test":
            try:
                stations = _client().test_connection()
                flash = _flash_html(
                    f"Connected to Alchemy FM. {len(stations)} station(s) found.",
                    "ok",
                )
            except AlchemyFmError as exc:
                flash = _flash_html(str(exc), "error")
        elif action == "push":
            try:
                payload = _station_payload_from_form(request.form)
                slug = payload["slug"]
                station, push_action = _client().push_station(
                    payload,
                    slug=slug,
                    bootstrap=bool(payload.get("bootstrap_queue")),
                )
                _save_profile(slug, station, payload, push_action)
                flash = _flash_html(
                    f"Station '{station.get('name')}' {push_action} on Alchemy FM "
                    f"(id {station.get('id')}, slug {station.get('slug')}).",
                    "ok",
                )
                logger.info(
                    "alchemy_fm_bridge pushed station slug=%s action=%s id=%s",
                    slug,
                    push_action,
                    station.get("id"),
                )
            except AlchemyFmError as exc:
                slug = (request.form.get("slug") or _slugify(request.form.get("name") or "station")).strip()
                _record_profile_error(slug, str(exc))
                flash = _flash_html(str(exc), "error")

    body = (
        "<p>Design a live radio station in AudioMuse and push it to "
        "<strong>Alchemy FM</strong>. Pushes are idempotent by slug: an existing slug is updated, "
        "not duplicated.</p>"
        f"{_form_html(flash=flash, values=values, search_query=search_query, search_results=search_results)}"
        "<h3 style='margin-top:2rem;'>Recent pushes</h3>"
        f"{_profiles_html()}"
        f"{_remote_stations_html()}"
    )
    return render_page(body, title="Alchemy FM Bridge")


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
        "<p>Connect to any Alchemy FM instance. Use the same admin username and password "
        "you set in <code>ADMIN_USERNAME</code> / <code>ADMIN_PASSWORD</code>.</p>"
        "<div><label>Alchemy FM URL</label>"
        f"<input name='alchemyfm_url' required placeholder='http://192.168.1.10:8080' "
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
