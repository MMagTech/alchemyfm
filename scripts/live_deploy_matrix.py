#!/usr/bin/env python3
"""Live deploy matrix — exercise all programming types against a real Alchemy FM instance."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import uuid
from typing import Any

import httpx

LOG_PATH = os.environ.get("DEBUG_LOG_PATH", "debug-b5959d.log")
SESSION_ID = "b5959d"


def log(message: str, data: dict[str, Any], hypothesis_id: str, run_id: str) -> None:
    entry = {
        "sessionId": SESSION_ID,
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": "scripts/live_deploy_matrix.py",
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    with open(LOG_PATH, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def basic_auth_header(user: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def alchemy_auth_headers(client: httpx.Client, alchemy_url: str, user: str, password: str) -> dict[str, str]:
    """Basic auth (plugin) with session-login fallback."""
    basic = basic_auth_header(user, password)
    probe = client.get(f"{alchemy_url}/api/admin/deploy-check", headers=basic)
    if probe.status_code == 200:
        return basic
    login = client.post(
        f"{alchemy_url}/api/admin/login",
        json={"username": user, "password": password},
    )
    if login.status_code != 200:
        return basic
    cookie = login.cookies.get("admin_session")
    if cookie:
        return {"Cookie": f"admin_session={cookie}"}
    return basic


def audiomuse_headers(token: str) -> dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def discover_programming(
    client: httpx.Client,
    am_url: str,
    am_token: str,
) -> dict[str, dict[str, Any]]:
    """Resolve real programming params from AudioMuse (best effort)."""
    headers = audiomuse_headers(am_token)
    discovered: dict[str, dict[str, Any]] = {}

    # clap_query
    try:
        r = client.post(
            f"{am_url}/api/clap/search",
            json={"query": "classic rock energetic guitar", "limit": 5},
            headers=headers,
        )
        if r.status_code == 200:
            results = r.json().get("results") if isinstance(r.json(), dict) else None
            if isinstance(results, list) and results:
                discovered["clap_query"] = {
                    "type": "clap_query",
                    "query": "classic rock energetic guitar",
                    "limit": 30,
                    "seed_item_id": str(results[0].get("item_id") or results[0].get("id") or ""),
                }
    except Exception as exc:
        log("discover-clap-error", {"error": str(exc)}, "H6", "live-matrix")

    # lyrics_query
    try:
        r = client.post(
            f"{am_url}/api/lyrics/search/text",
            json={"query": "open road freedom", "limit": 5},
            headers=headers,
        )
        if r.status_code == 200:
            results = r.json().get("results") if isinstance(r.json(), dict) else None
            if isinstance(results, list) and results:
                discovered["lyrics_query"] = {
                    "type": "lyrics_query",
                    "query": "open road freedom",
                    "limit": 30,
                }
    except Exception as exc:
        log("discover-lyrics-error", {"error": str(exc)}, "H6", "live-matrix")

    # mood_centroid
    try:
        r = client.get(f"{am_url}/api/mood_centroids", headers=headers)
        if r.status_code == 200 and isinstance(r.json(), dict):
            for mood, centroids in r.json().items():
                if isinstance(centroids, list) and centroids:
                    meta = centroids[0] if isinstance(centroids[0], dict) else {}
                    idx = meta.get("index", 0)
                    discovered["mood_centroid"] = {
                        "type": "mood_centroid",
                        "mood": str(mood).lower(),
                        "centroid_index": int(idx),
                        "limit": 30,
                    }
                    break
    except Exception as exc:
        log("discover-mood-error", {"error": str(exc)}, "H6", "live-matrix")

    # alchemy_anchor
    try:
        r = client.post(
            f"{am_url}/api/alchemy",
            json={"items": [{"id": "1", "op": "ADD", "type": "anchor"}], "n": 3},
            headers=headers,
        )
        if r.status_code == 200:
            results = r.json().get("results") if isinstance(r.json(), dict) else None
            if isinstance(results, list) and results:
                anchor_id = str(results[0].get("anchor_id") or results[0].get("id") or "1")
                discovered["alchemy_anchor"] = {
                    "type": "alchemy_anchor",
                    "anchor_id": anchor_id,
                    "limit": 30,
                }
    except Exception as exc:
        log("discover-anchor-error", {"error": str(exc)}, "H6", "live-matrix")

    # similar_seed — reuse clap result or search tracks
    seed_id = (discovered.get("clap_query") or {}).get("seed_item_id", "")
    if not seed_id:
        try:
            r = client.get(
                f"{am_url}/api/search_tracks",
                params={"search_query": "rock", "end": "5"},
                headers=headers,
            )
            if r.status_code == 200 and isinstance(r.json(), list) and r.json():
                seed_id = str(r.json()[0].get("item_id") or "")
        except Exception:
            pass
    if seed_id:
        discovered["similar_seed"] = {
            "type": "similar_seed",
            "seed_id": seed_id,
            "limit": 30,
        }

    # Fallbacks when AudioMuse discovery fails (bootstrap may still work via Alchemy .env token)
    discovered.setdefault(
        "clap_query",
        {"type": "clap_query", "query": "classic rock energetic guitar", "limit": 30},
    )
    discovered.setdefault(
        "lyrics_query",
        {"type": "lyrics_query", "query": "open road freedom", "limit": 30},
    )
    discovered.setdefault(
        "mood_centroid",
        {"type": "mood_centroid", "mood": "energetic", "centroid_index": 0, "limit": 30},
    )
    discovered.setdefault(
        "alchemy_anchor",
        {"type": "alchemy_anchor", "anchor_id": "1", "limit": 30},
    )
    if seed_id:
        discovered.setdefault(
            "similar_seed",
            {"type": "similar_seed", "seed_id": seed_id, "limit": 30},
        )

    return discovered


def build_payload(programming: dict[str, Any], slug_suffix: str) -> dict[str, Any]:
    ptype = programming["type"]
    source_ref = ""
    identity_anchor_id = ""
    identity_seed_item_id = ""
    if ptype in ("clap_query", "lyrics_query"):
        source_ref = programming["query"]
    elif ptype == "mood_centroid":
        source_ref = f"{programming['mood']}:{programming['centroid_index']}"
    elif ptype == "alchemy_anchor":
        source_ref = str(programming["anchor_id"])
        identity_anchor_id = source_ref
    elif ptype == "similar_seed":
        source_ref = str(programming["seed_id"])
        identity_seed_item_id = source_ref

    slug = f"matrix-{slug_suffix}-{uuid.uuid4().hex[:6]}"
    profile = {
        "programming": programming,
        "refresh": {"mode": "similar_to_last"},
        "station": {"name": f"Matrix {ptype}", "slug": slug},
    }
    return {
        "name": f"Matrix {ptype}",
        "slug": slug,
        "description": f"Live matrix test {ptype}",
        "icecast_mount": f"/{slug}",
        "enabled": False,
        "source_type": ptype,
        "source_ref": source_ref,
        "programming_json": json.dumps(profile),
        "queue_target": 15,
        "refresh_threshold": 5,
        "artist_separation_minutes": 60,
        "continuation_mode": "similar_to_last",
        "identity_anchor_id": identity_anchor_id,
        "identity_seed_item_id": identity_seed_item_id,
        "bootstrap_queue": False,
    }


def run_matrix(
    alchemy_url: str,
    admin_user: str,
    admin_password: str,
    audiomuse_url: str,
    audiomuse_token: str,
    *,
    cleanup: bool = True,
) -> int:
    run_id = "live-matrix"
    results: list[dict[str, Any]] = []

    with httpx.Client(timeout=120.0) as client:
        auth = alchemy_auth_headers(client, alchemy_url, admin_user, admin_password)
        # Preflight
        r = client.get(f"{alchemy_url}/api/admin/deploy-check", headers=auth)
        log(
            "deploy-check",
            {"status": r.status_code, "body": r.text[:500]},
            "H2",
            run_id,
        )
        deploy_check = r.json() if r.status_code == 200 else {}
        if r.status_code == 200 and not deploy_check.get("ok"):
            print("WARN: deploy-check failed:", json.dumps(deploy_check, indent=2))

        discovered = discover_programming(client, audiomuse_url, audiomuse_token)
        log("discovered-programming", discovered, "H6", run_id)

        programming_types = [
            "clap_query",
            "lyrics_query",
            "mood_centroid",
            "alchemy_anchor",
            "similar_seed",
        ]

        for ptype in programming_types:
            if ptype not in discovered:
                results.append(
                    {
                        "type": ptype,
                        "ok": False,
                        "phase": "discover",
                        "error": "Could not discover programming params (AudioMuse 401?)",
                    }
                )
                continue

            programming = discovered[ptype]
            payload = build_payload(programming, ptype.replace("_", "-"))
            slug = payload["slug"]
            row: dict[str, Any] = {"type": ptype, "slug": slug, "ok": False}

            try:
                create = client.post(
                    f"{alchemy_url}/api/admin/stations",
                    json=payload,
                    headers={**auth, "Content-Type": "application/json"},
                )
                row["create_status"] = create.status_code
                if create.status_code not in (200, 201):
                    row["phase"] = "create"
                    row["error"] = create.text[:500]
                    results.append(row)
                    log(f"matrix-{ptype}-create-fail", row, "H5", run_id)
                    continue

                station = create.json()
                station_id = station.get("id")
                row["station_id"] = station_id

                bootstrap = client.post(
                    f"{alchemy_url}/api/admin/stations/{station_id}/bootstrap",
                    headers=auth,
                )
                row["bootstrap_status"] = bootstrap.status_code
                if bootstrap.status_code != 200:
                    row["phase"] = "bootstrap"
                    row["error"] = bootstrap.text[:500]
                    results.append(row)
                    log(f"matrix-{ptype}-bootstrap-fail", row, "H5", run_id)
                    if cleanup and station_id:
                        client.delete(f"{alchemy_url}/api/admin/stations/{station_id}", headers=auth)
                    continue

                boot_body = bootstrap.json()
                row["queued_count"] = boot_body.get("queued_count")
                row["ok"] = bool(row.get("queued_count", 0) > 0)
                row["phase"] = "done"
                if not row["ok"]:
                    row["error"] = "Bootstrap returned 200 but queued_count is 0"
                results.append(row)
                log(f"matrix-{ptype}-ok" if row["ok"] else f"matrix-{ptype}-empty", row, "H5", run_id)

                if cleanup and station_id:
                    client.delete(f"{alchemy_url}/api/admin/stations/{station_id}", headers=auth)
                    row["cleaned_up"] = True

            except Exception as exc:
                row["phase"] = "exception"
                row["error"] = str(exc)
                results.append(row)
                log(f"matrix-{ptype}-exception", row, "H5", run_id)

    passed = sum(1 for r in results if r.get("ok"))
    total = len(results)
    print(json.dumps({"passed": passed, "total": total, "results": results}, indent=2))
    log("matrix-summary", {"passed": passed, "total": total, "results": results}, "H5", run_id)
    return 0 if passed == total else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Live Alchemy FM deploy matrix")
    parser.add_argument("--alchemy-url", default=os.environ.get("ALCHEMY_URL", "http://192.168.1.10:9246"))
    parser.add_argument("--audiomuse-url", default=os.environ.get("AUDIOMUSE_URL", "http://192.168.1.10:8387"))
    parser.add_argument("--admin-user", default=os.environ.get("ADMIN_USERNAME", "admin"))
    parser.add_argument(
        "--admin-password",
        default=os.environ.get("ALCHEMY_ADMIN_PASSWORD") or os.environ.get("ADMIN_PASSWORD", ""),
    )
    parser.add_argument("--audiomuse-token", default=os.environ.get("AUDIOMUSE_API_TOKEN", ""))
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()

    if not args.admin_password:
        print("ERROR: Set ADMIN_PASSWORD or ALCHEMY_ADMIN_PASSWORD", file=sys.stderr)
        return 2

    return run_matrix(
        args.alchemy_url.rstrip("/"),
        args.admin_user,
        args.admin_password,
        args.audiomuse_url.rstrip("/"),
        args.audiomuse_token,
        cleanup=not args.no_cleanup,
    )


if __name__ == "__main__":
    raise SystemExit(main())
