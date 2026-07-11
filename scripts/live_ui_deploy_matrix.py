#!/usr/bin/env python3
"""Test Channel Designer station-build variations on live AudioMuse + Alchemy."""

from __future__ import annotations

import base64
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "audiomuse-plugins" / "alchemy_fm_bridge" / "tests"
sys.path.insert(0, str(TESTS))

# Load repo .env for live probes
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    if not line.strip() or line.strip().startswith("#") or "=" not in line:
        continue
    k, _, v = line.partition("=")
    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from support import fake_plugin_api  # noqa: E402

fake_plugin_api.install()

from bridge_loader import bridge  # noqa: E402

ALCHEMY = os.environ.get("ALCHEMY_URL", "http://192.168.1.10:9246").rstrip("/")
AM = os.environ.get("AUDIOMUSE_URL", "http://192.168.1.10:8387").rstrip("/")
USER = os.environ.get("ADMIN_USERNAME", "admin")
PASS = os.environ.get("ADMIN_PASSWORD", "")
TOKEN = os.environ.get("AUDIOMUSE_API_TOKEN", "")


def am_headers() -> dict[str, str]:
    h = {"Accept": "application/json", "Content-Type": "application/json"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return h


def alchemy_auth(c: httpx.Client) -> dict[str, str]:
    basic = {
        "Authorization": "Basic "
        + base64.b64encode(f"{USER}:{PASS}".encode()).decode(),
        "Accept": "application/json",
    }
    if c.get(f"{ALCHEMY}/api/admin/deploy-check", headers=basic).status_code == 200:
        return basic
    login = c.post(f"{ALCHEMY}/api/admin/login", json={"username": USER, "password": PASS})
    if login.status_code == 200 and login.cookies.get("admin_session"):
        return {"Cookie": f"admin_session={login.cookies.get('admin_session')}"}
    return basic


def discover(c: httpx.Client) -> dict[str, Any]:
    d: dict[str, Any] = {}
    h = am_headers()
    r = c.post(f"{AM}/api/clap/search", json={"query": "classic rock energetic guitar", "limit": 15}, headers=h)
    if r.status_code == 200:
        d["clap_query"] = {"type": "clap_query", "query": "classic rock energetic guitar", "limit": 15}
    r = c.post(f"{AM}/api/lyrics/search/text", json={"query": "open road freedom", "limit": 15}, headers=h)
    if r.status_code == 200:
        d["lyrics_query"] = {"type": "lyrics_query", "query": "open road freedom", "limit": 15}
    r = c.get(f"{AM}/api/mood_centroids", headers=h)
    if r.status_code == 200:
        moods = r.json()
        if moods:
            mood = sorted(moods.keys())[0]
            centroids = moods[mood]
            idx = centroids[0].get("index", 0) if centroids else 0
            d["mood_centroid"] = {"type": "mood_centroid", "mood": mood, "centroid_index": idx, "limit": 15}
    r = c.get(f"{AM}/api/anchors", headers=h)
    if r.status_code == 200:
        anchors = (r.json() or {}).get("anchors") or []
        if anchors:
            d["alchemy_anchor"] = {
                "type": "alchemy_anchor",
                "anchor_id": str(anchors[0]["id"]),
                "limit": 15,
            }
    r = c.get(f"{AM}/api/search_tracks", params={"search_query": "rock", "end": "5"}, headers=h)
    if r.status_code == 200:
        tracks = r.json()
        if isinstance(tracks, list) and tracks:
            d["similar_seed"] = {
                "type": "similar_seed",
                "seed_id": str(tracks[0]["item_id"]),
                "limit": 15,
            }
    r = c.get(f"{AM}/api/search_playlists", params={"query": "a"}, headers=h)
    if r.status_code == 200 and isinstance(r.json(), list) and r.json():
        pl = r.json()[0]
        d["playlist_id"] = str(pl.get("id") or pl.get("Id") or "")
        d["playlist_name"] = str(pl.get("name") or pl.get("Name") or "")
    return d


def base_form(**overrides: Any) -> dict[str, str]:
    data: dict[str, str] = {
        "name": "UI Matrix Test",
        "slug": f"ui-matrix-{uuid.uuid4().hex[:8]}",
        "programming_type": "clap_query",
        "clap_query": "classic rock energetic guitar",
        "refresh_mode": "similar_to_last",
        "preview_limit": "15",
        "bootstrap_queue": "on",
        "enabled": "on",
        "queue_target": "15",
        "refresh_threshold": "5",
        "artist_separation_minutes": "60",
    }
    for k, v in overrides.items():
        if v is None:
            data.pop(k, None)
        else:
            data[k] = str(v) if not isinstance(v, str) else v
    return data


def plugin_pipeline(form: dict[str, str], *, slug: str | None = None) -> dict[str, Any]:
    """Simulate plugin build steps before Alchemy push."""
    row: dict[str, Any] = {"form_keys": sorted(form.keys())}
    try:
        profile = bridge.profile_from_form(form, for_deploy=True)
        slug = slug or profile["station"]["slug"]
        profile = bridge._merge_saved_programming_if_needed(profile, slug)
        row["programming"] = profile["programming"]
        unfiltered = bridge._deploy_unfiltered_tracks(profile, slug)
        row["unfiltered_count"] = len(unfiltered)
        preview_tracks = bridge.apply_track_filters(unfiltered, profile)
        row["filtered_count"] = len(preview_tracks)
        if not preview_tracks:
            row["plugin_phase"] = "blocked"
            row["plugin_error"] = bridge._preview_empty_message(unfiltered, profile)
            return row
        values: dict[str, Any] = {}
        bootstrap_check = bridge._apply_bootstrap_check(values, profile)
        if bootstrap_check and not bootstrap_check.get("ok"):
            row["plugin_phase"] = "blocked"
            row["plugin_error"] = bootstrap_check.get("error") or "Bootstrap playlist verify failed"
            row["bootstrap_check"] = bootstrap_check
            return row
        payload = bridge.channel_profile_to_alchemy_payload(profile, preview_tracks)
        row["plugin_phase"] = "ready"
        row["payload_keys"] = sorted(payload.keys())
        row["source_type"] = payload.get("source_type")
        row["track_count"] = len(preview_tracks)
        row["payload"] = payload
        row["preview_tracks"] = preview_tracks
    except bridge.ChannelDesignerError as exc:
        row["plugin_phase"] = "blocked"
        row["plugin_error"] = str(exc)
    except Exception as exc:
        row["plugin_phase"] = "exception"
        row["plugin_error"] = str(exc)
    return row


def alchemy_deploy(c: httpx.Client, auth: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    create = c.post(
        f"{ALCHEMY}/api/admin/stations",
        json={k: v for k, v in payload.items() if k != "bootstrap_queue"},
        headers={**auth, "Content-Type": "application/json"},
    )
    out = {"create_status": create.status_code}
    if create.status_code not in (200, 201):
        out["alchemy_error"] = create.text[:300]
        return out
    sid = create.json().get("id")
    boot = c.post(f"{ALCHEMY}/api/admin/stations/{sid}/bootstrap", headers=auth)
    out["bootstrap_status"] = boot.status_code
    if boot.status_code == 200:
        out["queued_count"] = boot.json().get("queued_count")
        out["alchemy_ok"] = bool(out.get("queued_count", 0) > 0)
    else:
        out["alchemy_error"] = boot.text[:300]
        out["alchemy_ok"] = False
    if sid:
        c.delete(f"{ALCHEMY}/api/admin/stations/{sid}", headers=auth)
    return out


def main() -> int:
    # Patch bridge to hit live AudioMuse
    def live_get(path, *, params=None, timeout=60.0):
        with httpx.Client(timeout=timeout) as client:
            r = client.get(f"{AM}{path}", params=params, headers=am_headers())
        if r.status_code >= 400:
            raise bridge.ChannelDesignerError(f"HTTP {r.status_code}", status=r.status_code)
        return r.json()

    def live_post(path, payload, *, timeout=120.0):
        with httpx.Client(timeout=timeout) as client:
            r = client.post(f"{AM}{path}", json=payload, headers=am_headers())
        if r.status_code >= 400:
            raise bridge.ChannelDesignerError(f"HTTP {r.status_code}", status=r.status_code)
        return r.json()

    bridge.audiomuse_get = live_get  # type: ignore[assignment]
    bridge.audiomuse_post = live_post  # type: ignore[assignment]
    bridge._load_saved_channel = lambda slug: None  # type: ignore[assignment]

    def live_bootstrap_verify(playlist_id: str, *, limit: int) -> dict[str, Any]:
        """Approximate plugin verify when mediaserver is in-process on AudioMuse."""
        playlist_id = playlist_id.strip()
        if not playlist_id or playlist_id.startswith("invalid-"):
            return {"ok": False, "playlist_id": playlist_id, "error": "Playlist not found or has no playable tracks in AudioMuse."}
        try:
            track_ids = bridge._playlist_track_ids(playlist_id, limit=limit)
        except bridge.ChannelDesignerError as exc:
            return {"ok": False, "playlist_id": playlist_id, "error": str(exc)}
        if not track_ids:
            return {"ok": False, "playlist_id": playlist_id, "error": "Playlist not found or has no playable tracks in AudioMuse."}
        return {"ok": True, "playlist_id": playlist_id, "resolved_count": len(track_ids), "limit": limit}

    bridge._verify_bootstrap_playlist = live_bootstrap_verify  # type: ignore[assignment]

    scenarios: list[tuple[str, dict[str, str]]] = []
    refresh_modes = [m[0] for m in bridge.REFRESH_MODES]

    with httpx.Client(timeout=120.0) as c:
        disc = discover(c)
        auth = alchemy_auth(c)
        ptype = "clap_query"
        prog = disc.get(ptype, {"type": "clap_query", "query": "classic rock", "limit": 15})

        for rm in refresh_modes:
            f = base_form(refresh_mode=rm, programming_type=ptype, clap_query=prog.get("query", "classic rock"))
            scenarios.append((f"clap+refresh_{rm}", f))

        for ptype in ("clap_query", "lyrics_query", "mood_centroid", "alchemy_anchor", "similar_seed"):
            if ptype not in disc:
                continue
            p = disc[ptype]
            f = base_form(programming_type=ptype)
            if ptype == "clap_query":
                f["clap_query"] = p["query"]
            elif ptype == "lyrics_query":
                f["lyrics_query"] = p["query"]
            elif ptype == "mood_centroid":
                f["mood_name"] = p["mood"]
                f["centroid_index"] = str(p["centroid_index"])
            elif ptype == "alchemy_anchor":
                f["anchor_id"] = p["anchor_id"]
            elif ptype == "similar_seed":
                f["seed_id"] = p["seed_id"]
            scenarios.append((f"programming_{ptype}", f))

        # Filters
        scenarios.append(("filter_tempo_narrow", base_form(filter_tempo_min="200", filter_tempo_max="250")))
        scenarios.append(("filter_energy_high", base_form(filter_energy_min="0.95")))
        scenarios.append(("filter_genre_impossible", base_form(filter_genre_include="zzznogenre")))

        # Simulated disabled Step 2 fields (empty clap, type still clap)
        scenarios.append(("empty_clap_query", base_form(clap_query="")))
        scenarios.append(("empty_clap_no_name", base_form(name="", clap_query="")))

        # Bootstrap opener variations
        if disc.get("playlist_id"):
            pid = disc["playlist_id"]
            scenarios.append(
                (
                    "bootstrap_valid_playlist",
                    base_form(bootstrap_enabled="on", bootstrap_playlist_id=pid),
                )
            )
        scenarios.append(
            (
                "bootstrap_invalid_playlist",
                base_form(bootstrap_enabled="on", bootstrap_playlist_id="invalid-playlist-id-999"),
            )
        )
        scenarios.append(("bootstrap_enabled_no_id", base_form(bootstrap_enabled="on")))

        # Living channel
        scenarios.append(("living_enabled", base_form(living_enabled="on", living_auto_add="on")))

        # Deploy queue off
        scenarios.append(("no_bootstrap_queue", base_form(bootstrap_queue="")))

        results: list[dict[str, Any]] = []
        for name, form in scenarios:
            row: dict[str, Any] = {"scenario": name}
            plug = plugin_pipeline(form)
            row.update({k: plug.get(k) for k in ("plugin_phase", "plugin_error", "unfiltered_count", "filtered_count", "bootstrap_check")})
            if plug.get("plugin_phase") == "ready" and plug.get("payload"):
                row["alchemy"] = alchemy_deploy(c, auth, plug["payload"])
            results.append(row)

    summary = {
        "total": len(results),
        "plugin_blocked": [r for r in results if r.get("plugin_phase") == "blocked"],
        "plugin_ready_alchemy_ok": [r for r in results if r.get("alchemy", {}).get("alchemy_ok")],
        "plugin_ready_alchemy_fail": [
            r for r in results if r.get("plugin_phase") == "ready" and not r.get("alchemy", {}).get("alchemy_ok")
        ],
        "all_results": results,
    }
    print(json.dumps(summary, indent=2))
    out_path = ROOT / "scripts" / "live_ui_matrix_results.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}", file=sys.stderr)
    return 0 if not summary["plugin_ready_alchemy_fail"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
