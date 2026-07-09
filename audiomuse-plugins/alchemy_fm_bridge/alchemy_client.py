"""Thin HTTP client for the Alchemy FM admin API."""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


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
        """Create or update by slug. Returns (station, action)."""
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
    """Call AudioMuse core APIs from inside the plugin (same host)."""
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
