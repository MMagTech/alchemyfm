import json
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

    def _request_error(self, exc: Exception, method: str, path: str) -> str:
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            if status == 401:
                if not settings.audiomuse_api_token:
                    return (
                        f"AudioMuse rejected the request (HTTP 401) during {method} {path}. "
                        "Set AUDIOMUSE_API_TOKEN in Alchemy FM .env to the token from "
                        "AudioMuse Settings → API."
                    )
                return (
                    f"AudioMuse rejected the API token (HTTP 401) during {method} {path}. "
                    "Verify AUDIOMUSE_API_TOKEN in Alchemy FM .env matches AudioMuse Settings → API."
                )
        return str(exc)

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(
                f"{self.base_url}{path}",
                params=params,
                headers=self.headers,
            )
            try:
                response.raise_for_status()
            except Exception as exc:
                raise ValueError(self._request_error(exc, "GET", path)) from exc
            return response.json()

    async def _post(self, path: str, payload: dict[str, Any]) -> Any:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self.base_url}{path}",
                json=payload,
                headers=self.headers,
            )
            try:
                response.raise_for_status()
            except Exception as exc:
                raise ValueError(self._request_error(exc, "POST", path)) from exc
            return response.json()

    def _programming_block(self, programming_json: str | None) -> dict[str, Any]:
        if not programming_json:
            return {}
        try:
            data = json.loads(programming_json)
        except json.JSONDecodeError:
            return {}
        if isinstance(data, dict) and isinstance(data.get("programming"), dict):
            return data["programming"]
        return data if isinstance(data, dict) else {}

    def _track_rows(self, results: Any, count: int) -> list[TrackRef]:
        if not isinstance(results, list):
            return []
        refs: list[TrackRef] = []
        for row in results[:count]:
            if not isinstance(row, dict):
                continue
            item_id = row.get("item_id") or row.get("id")
            if not item_id:
                continue
            refs.append(
                TrackRef(
                    item_id=str(item_id),
                    title=row.get("title") or "Unknown",
                    artist=row.get("author") or row.get("artist") or "Unknown",
                )
            )
        return refs

    async def fetch_similar_tracks(self, item_id: str, count: int) -> list[TrackRef]:
        """AudioMuse sonic neighbors for a track — used to keep stations going."""
        return await self._from_similar_seed(item_id, count)

    async def fetch_scores(self, item_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Batch tempo/key/scale/energy for track ids via AudioMuse /api/sync.

        One call for up to 500 ids. Returns {item_id: {tempo, key, scale, energy}}.
        Embeddings are skipped to keep the payload small. Used by harmonic
        ordering and (later) daypart biasing.
        """
        ids = [str(i) for i in item_ids if i]
        if not ids:
            return {}
        data = await self._get(
            "/api/sync",
            params={
                "ids": ",".join(ids),
                "include_embeddings": "false",
                "limit": len(ids),
            },
        )
        tracks = data.get("tracks") if isinstance(data, dict) else None
        scores: dict[str, dict[str, Any]] = {}
        if isinstance(tracks, list):
            for row in tracks:
                if not isinstance(row, dict):
                    continue
                # /api/sync returns the track id under `id` (not `item_id`).
                tid = row.get("id") or row.get("item_id")
                if not tid:
                    continue
                scores[str(tid)] = {
                    "tempo": row.get("tempo"),
                    "key": row.get("key"),
                    "scale": row.get("scale"),
                    "energy": row.get("energy"),
                }
        return scores

    async def fetch_tracks(
        self,
        source_type: str,
        source_ref: str,
        count: int,
        programming_json: str | None = None,
    ) -> list[TrackRef]:
        programming = self._programming_block(programming_json)
        if source_type == "similar_seed":
            seed = str(programming.get("seed_id") or source_ref)
            return await self._from_similar_seed(seed, count)
        if source_type == "alchemy_anchor":
            anchor_id = str(programming.get("anchor_id") or source_ref)
            return await self._from_alchemy_anchor(anchor_id, count)
        if source_type == "clap_query":
            query = (programming.get("query") or source_ref or "").strip()
            return await self._from_clap_query(query, count)
        if source_type == "lyrics_query":
            query = (programming.get("query") or source_ref or "").strip()
            return await self._from_lyrics_query(query, count)
        if source_type == "mood_centroid":
            mood = (programming.get("mood") or "").strip().lower()
            centroid_index = programming.get("centroid_index")
            if not mood and ":" in source_ref:
                mood, _, idx = source_ref.partition(":")
                centroid_index = idx
            if not mood or centroid_index is None:
                raise ValueError("mood_centroid requires mood and centroid_index")
            return await self._from_mood_centroid(mood, int(centroid_index), count)
        if source_type == "journey":
            return await self._from_journey(programming, source_ref, count)
        raise ValueError(f"Unknown source type: {source_type}")

    async def _from_journey(
        self, programming: dict[str, Any], source_ref: str, count: int
    ) -> list[TrackRef]:
        """Clock-anchored journey: seed from the waypoint current for the hour."""
        from app.services.journey import current_seed

        journey = programming.get("journey") if isinstance(programming, dict) else None
        journey = journey if isinstance(journey, dict) else {}
        waypoints = [str(w) for w in (journey.get("waypoints") or []) if w]
        seed = current_seed(waypoints, str(journey.get("timezone") or ""))
        if not seed:
            # No baked path (e.g. find_path failed at deploy) — fall back to the
            # start track so the station still plays something on-vibe.
            seed = str(journey.get("start_id") or source_ref or "").strip()
        if not seed:
            raise ValueError("journey has no waypoints or start track")
        return await self._from_similar_seed(seed, count)

    async def _from_similar_seed(self, item_id: str, count: int) -> list[TrackRef]:
        data = await self._get(
            "/api/similar_tracks",
            params={"item_id": item_id, "n": count, "eliminate_duplicates": "true"},
        )
        if not isinstance(data, list):
            raise ValueError("Unexpected response from /api/similar_tracks")
        return self._track_rows(data, count)

    async def _from_alchemy_anchor(self, anchor_id: str, count: int) -> list[TrackRef]:
        data = await self._post(
            "/api/alchemy",
            {
                "items": [{"id": str(anchor_id), "op": "ADD", "type": "anchor"}],
                "n": count,
            },
        )
        results = data.get("results") or []
        return self._track_rows(results, count)

    async def _from_clap_query(self, query: str, count: int) -> list[TrackRef]:
        if len(query) < 3:
            raise ValueError("CLAP query must be at least 3 characters")
        data = await self._post("/api/clap/search", {"query": query, "limit": count})
        results = data.get("results") if isinstance(data, dict) else None
        return self._track_rows(results, count)

    async def _from_lyrics_query(self, query: str, count: int) -> list[TrackRef]:
        if len(query) < 3:
            raise ValueError("Lyrics query must be at least 3 characters")
        data = await self._post("/api/lyrics/search/text", {"query": query, "limit": count})
        results = data.get("results") if isinstance(data, dict) else None
        return self._track_rows(results, count)

    async def _from_mood_centroid(self, mood: str, centroid_index: int, count: int) -> list[TrackRef]:
        data = await self._get(
            "/api/similar_tracks",
            params={
                "mood": mood,
                "centroid_index": str(centroid_index),
                "n": str(count),
                "eliminate_duplicates": "true",
            },
        )
        return self._track_rows(data, count)


audiomuse_client = AudioMuseClient()
