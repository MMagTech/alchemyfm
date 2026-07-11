#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    if not line.strip() or line.strip().startswith("#") or "=" not in line:
        continue
    k, _, v = line.partition("=")
    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

USER = os.environ.get("ADMIN_USERNAME", "admin")
PASS = os.environ.get("ADMIN_PASSWORD", "")
TOKEN = os.environ.get("AUDIOMUSE_API_TOKEN", "")


def basic(u: str, p: str) -> dict[str, str]:
    t = base64.b64encode(f"{u}:{p}".encode()).decode()
    return {"Authorization": f"Basic {t}"}


def probe_url(base: str) -> dict:
    base = base.rstrip("/")
    row: dict = {"url": base}
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as c:
            r = c.get(f"{base}/api/admin/deploy-check", headers=basic(USER, PASS))
            row["deploy_check_status"] = r.status_code
            if r.status_code == 200:
                row["deploy_check_ok"] = r.json().get("ok")
            else:
                row["deploy_check_error"] = r.text[:150]
            r2 = c.get(f"{base}/api/admin/stations", headers=basic("admin", PASS))
            row["plugin_default_admin_auth"] = r2.status_code
            r3 = c.get(f"{base}/api/admin/stations", headers=basic(USER, PASS))
            row["correct_user_auth"] = r3.status_code
    except Exception as exc:
        row["exception"] = str(exc)[:200]
    return row


def main() -> int:
    urls = [
        "http://192.168.1.10:9246",
        "https://alchemyfm.mmagtech.com",
    ]
    out = [probe_url(u) for u in urls]
    with httpx.Client(timeout=15) as c:
        no_tok = c.get("http://192.168.1.10:8387/api/mood_centroids")
        with_tok = c.get(
            "http://192.168.1.10:8387/api/mood_centroids",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
    print(
        json.dumps(
            {
                "url_probes": out,
                "audiomuse_no_token": no_tok.status_code,
                "audiomuse_with_token": with_tok.status_code,
                "plugin_must_use_username": USER,
                "plugin_wrong_default": "admin",
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
