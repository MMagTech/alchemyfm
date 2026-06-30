import logging
from typing import Any

import httpx

from app.config import settings
from app.schemas import TrackRef

logger = logging.getLogger(__name__)


class AudioMuseClient:
    def __init__(self) -> None:
        self.base_url = settings.audiomuse_url.rstrip("/")
        self.headers: dict[str, str] = {}
        if settings.audiomuse_api_token:
            self.headers["Authorization"] = f"Bearer {settings.audiomuse_api_token}"

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(
                f"{self.base_url}{path}",
                params=params,
                headers=self.headers,
            )
            response.raise_for_status()
            return response.json()

    async def _post(self, path: str, payload: dict[str, Any]) -> Any:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self.base_url}{path}",
                json=payload,
                headers=self.headers,
            )
            response.raise_for_status()
            return response.json()

    async def fetch_similar_tracks(self, item_id: str, count: int) -> list[TrackRef]:
        """AudioMuse sonic neighbors for a track — used to keep stations going."""
        return await self._from_similar_seed(item_id, count)

    async def fetch_tracks(self, source_type: str, source_ref: str, count: int) -> list[TrackRef]:
        if source_type == "similar_seed":
            return await self._from_similar_seed(source_ref, count)
        if source_type == "alchemy_anchor":
            return await self._from_alchemy_anchor(source_ref, count)
        raise ValueError(f"Unknown source type: {source_type}")

    async def _from_similar_seed(self, item_id: str, count: int) -> list[TrackRef]:
        data = await self._get(
            "/api/similar_tracks",
            params={"item_id": item_id, "n": count},
        )
        if not isinstance(data, list):
            raise ValueError("Unexpected response from /api/similar_tracks")
        return [
            TrackRef(
                item_id=str(t["item_id"]),
                title=t.get("title") or "Unknown",
                artist=t.get("author") or "Unknown",
            )
            for t in data[:count]
        ]

    async def _from_alchemy_anchor(self, anchor_id: str, count: int) -> list[TrackRef]:
        data = await self._post(
            "/api/alchemy",
            {
                "items": [{"id": str(anchor_id), "op": "ADD", "type": "anchor"}],
                "n": count,
            },
        )
        results = data.get("results") or []
        return [
            TrackRef(
                item_id=str(r["item_id"]),
                title=r.get("title") or "Unknown",
                artist=r.get("author") or "Unknown",
            )
            for r in results[:count]
            if r.get("item_id")
        ]

    async def list_anchors(self) -> list[dict[str, str | int]]:
        data = await self._get("/api/anchors")
        anchors = data.get("anchors") or []
        return [{"id": a["id"], "name": a.get("name") or f"Anchor {a['id']}"} for a in anchors]

    async def search_tracks(self, query: str, limit: int = 20) -> list[dict[str, str | None]]:
        data = await self._get(
            "/api/search_tracks",
            params={"search_query": query, "end": limit},
        )
        if not isinstance(data, list):
            raise ValueError("Unexpected response from /api/search_tracks")
        results: list[dict[str, str | None]] = []
        for track in data:
            if not isinstance(track, dict) or not track.get("item_id"):
                continue
            results.append(
                {
                    "item_id": str(track["item_id"]),
                    "title": track.get("title") or "Unknown",
                    "artist": track.get("author") or "Unknown",
                    "album": (track.get("album") or "").strip() or None,
                }
            )
        return results

    async def get_track(self, item_id: str) -> dict[str, str | None] | None:
        data = await self._get("/api/track", params={"item_id": item_id})
        if not isinstance(data, dict) or not data.get("item_id"):
            return None
        return {
            "item_id": str(data["item_id"]),
            "title": data.get("title") or "Unknown",
            "artist": data.get("author") or "Unknown",
            "album": (data.get("album") or "").strip() or None,
        }


audiomuse_client = AudioMuseClient()
