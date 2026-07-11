"""Pre-deploy dependency checks — AudioMuse programming + Navidrome streams."""

from __future__ import annotations

from typing import Any

import httpx

from app.config import settings
from app.services.navidrome import navidrome_client


def _audiomuse_error(exc: Exception, url: str) -> str:
    if isinstance(exc, httpx.ConnectError):
        return (
            f"Cannot connect to AudioMuse at {url}. "
            "In Alchemy FM .env set AUDIOMUSE_URL to the LAN address the backend container "
            "can reach (e.g. http://192.168.x.x:8387). Do not use a public Cloudflare URL."
        )
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 401:
            if not settings.audiomuse_api_token:
                return (
                    f"AudioMuse at {url} requires authentication (HTTP 401). "
                    "Set AUDIOMUSE_API_TOKEN in Alchemy FM .env to the token from "
                    "AudioMuse Settings → API."
                )
            return (
                f"AudioMuse at {url} rejected the API token (HTTP 401). "
                "Verify AUDIOMUSE_API_TOKEN in Alchemy FM .env matches AudioMuse Settings → API."
            )
        return f"AudioMuse at {url} returned HTTP {status}."
    if not settings.audiomuse_api_token:
        return (
            f"AudioMuse at {url} failed: {exc}. "
            "Set AUDIOMUSE_API_TOKEN in Alchemy FM .env if AudioMuse auth is enabled."
        )
    return f"AudioMuse at {url} failed: {exc}"


def _navidrome_error(exc: Exception, url: str) -> str:
    if isinstance(exc, httpx.ConnectError):
        return (
            f"Cannot connect to Navidrome at {url}. "
            "Set NAVIDROME_URL in Alchemy FM .env to a URL the backend container can reach "
            "(e.g. http://192.168.x.x:4533)."
        )
    return f"Navidrome at {url} failed: {exc}"


async def check_deploy_dependencies() -> dict[str, Any]:
    audiomuse_url = settings.audiomuse_url.rstrip("/")
    navidrome_url = settings.navidrome_url.rstrip("/")
    result: dict[str, Any] = {
        "ok": True,
        "audiomuse": {"url": audiomuse_url, "ok": False, "error": ""},
        "navidrome": {"url": navidrome_url, "ok": False, "error": ""},
    }

    headers: dict[str, str] = {}
    if settings.audiomuse_api_token:
        headers["Authorization"] = f"Bearer {settings.audiomuse_api_token}"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"{audiomuse_url}/api/mood_centroids", headers=headers)
            response.raise_for_status()
        result["audiomuse"]["ok"] = True
    except Exception as exc:
        result["ok"] = False
        result["audiomuse"]["error"] = _audiomuse_error(exc, audiomuse_url)

    if not settings.navidrome_user or not settings.navidrome_password:
        result["ok"] = False
        result["navidrome"]["error"] = (
            "Navidrome credentials are not configured. "
            "Set NAVIDROME_USER and NAVIDROME_PASSWORD in Alchemy FM .env."
        )
    else:
        try:
            await navidrome_client._request("ping")
            result["navidrome"]["ok"] = True
        except Exception as exc:
            result["ok"] = False
            result["navidrome"]["error"] = _navidrome_error(exc, navidrome_url)

    return result
