#!/usr/bin/env python3
"""Probe live instance the same way the Channel Designer plugin does."""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), val)


_load_dotenv()

ALCHEMY = os.environ.get("ALCHEMY_URL", "http://192.168.1.10:9246").rstrip("/")
AM = os.environ.get("AUDIOMUSE_URL", "http://192.168.1.10:8387").rstrip("/")
USER = os.environ.get("ADMIN_USERNAME", "admin")
PASS = os.environ.get("ALCHEMY_ADMIN_PASSWORD") or os.environ.get("ADMIN_PASSWORD", "")
TOKEN = os.environ.get("AUDIOMUSE_API_TOKEN", "")


def basic(user: str, password: str) -> dict[str, str]:
    tok = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    return {"Authorization": f"Basic {tok}", "Accept": "application/json"}


def main() -> int:
    if not PASS:
        print("ERROR: set ADMIN_PASSWORD", file=sys.stderr)
        return 2

    report: dict[str, object] = {"alchemy": ALCHEMY, "audiomuse": AM, "admin_user": USER}
    with httpx.Client(timeout=60.0) as c:
        # 1) Test Connection path (plugin skips deploy-check)
        r = c.get(f"{ALCHEMY}/api/admin/stations", headers=basic(USER, PASS))
        report["test_connection"] = {"status": r.status_code, "stations": len(r.json()) if r.status_code == 200 else r.text[:200]}

        # 2) deploy-check (plugin deploy used to block on this)
        r = c.get(f"{ALCHEMY}/api/admin/deploy-check", headers=basic(USER, PASS))
        report["deploy_check"] = {"status": r.status_code, "body": r.json() if r.status_code == 200 else r.text[:300]}

        # 3) Wrong username probe (default plugin setting)
        r_wrong = c.get(f"{ALCHEMY}/api/admin/stations", headers=basic("admin", PASS))
        report["auth_as_admin"] = {"status": r_wrong.status_code, "hint": "plugin defaults to admin username"}

        # 4) AudioMuse from probe machine (plugin preview uses this)
        am_headers = {"Accept": "application/json"}
        if TOKEN:
            am_headers["Authorization"] = f"Bearer {TOKEN}"
        r_am = c.get(f"{AM}/api/mood_centroids", headers=am_headers)
        report["audiomuse_mood_centroids"] = {"status": r_am.status_code, "ok": r_am.status_code == 200}

        # 5) Full deploy simulation (create + bootstrap) like plugin push
        slug = f"probe-{uuid.uuid4().hex[:8]}"
        payload = {
            "name": "Live Probe Channel",
            "slug": slug,
            "description": "Automated deploy probe",
            "icecast_mount": f"/{slug}",
            "enabled": False,
            "source_type": "clap_query",
            "source_ref": "classic rock energetic guitar",
            "programming_json": json.dumps(
                {
                    "programming": {"type": "clap_query", "query": "classic rock energetic guitar", "limit": 15},
                    "refresh": {"mode": "similar_to_last"},
                    "station": {"name": "Live Probe Channel", "slug": slug},
                }
            ),
            "queue_target": 15,
            "refresh_threshold": 5,
            "artist_separation_minutes": 60,
            "continuation_mode": "similar_to_last",
            "identity_anchor_id": "",
            "identity_seed_item_id": "",
            "bootstrap_queue": False,
        }
        auth = basic(USER, PASS)
        create = c.post(f"{ALCHEMY}/api/admin/stations", json=payload, headers={**auth, "Content-Type": "application/json"})
        report["deploy_create"] = {"status": create.status_code, "body": create.text[:400]}
        if create.status_code in (200, 201):
            sid = create.json().get("id")
            boot = c.post(f"{ALCHEMY}/api/admin/stations/{sid}/bootstrap", headers=auth)
            report["deploy_bootstrap"] = {"status": boot.status_code, "body": boot.text[:400]}
            if boot.status_code == 200:
                report["deploy_bootstrap"]["queued_count"] = boot.json().get("queued_count")
            c.delete(f"{ALCHEMY}/api/admin/stations/{sid}", headers=auth)
            report["cleanup"] = "deleted probe station"

    print(json.dumps(report, indent=2))
    ok = (
        report.get("test_connection", {}).get("status") == 200
        and report.get("deploy_check", {}).get("status") == 200
        and (report.get("deploy_check", {}).get("body") or {}).get("ok") is True
        and report.get("deploy_bootstrap", {}).get("status") == 200
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
