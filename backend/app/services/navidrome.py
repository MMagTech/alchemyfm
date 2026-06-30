import logging
from urllib.parse import quote

import httpx

from app.config import settings
from app.schemas import TrackInfo, TrackRef

logger = logging.getLogger(__name__)


def _parse_year(song: dict) -> int | None:
    year = song.get("year")
    if year:
        try:
            return int(year)
        except (TypeError, ValueError):
            pass
    release = str(song.get("releaseDate") or "")
    if len(release) >= 4 and release[:4].isdigit():
        return int(release[:4])
    return None


class NavidromeClient:
    def __init__(self) -> None:
        self.base_url = settings.navidrome_url.rstrip("/")
        self.user = settings.navidrome_user
        self.password = settings.navidrome_password

    def _auth_params(self) -> dict[str, str]:
        if not self.user or not self.password:
            raise RuntimeError("Navidrome credentials are not configured")
        encoded = self.password.encode("utf-8").hex()
        return {
            "u": self.user,
            "p": f"enc:{encoded}",
            "v": "1.16.1",
            "c": "AudioMuseRadio",
            "f": "json",
        }

    async def _request(self, endpoint: str, extra: dict | None = None) -> dict:
        params = self._auth_params()
        if extra:
            params.update(extra)
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(f"{self.base_url}/rest/{endpoint}.view", params=params)
            response.raise_for_status()
            payload = response.json()
        subsonic = payload.get("subsonic-response") or {}
        if subsonic.get("status") != "ok":
            raise RuntimeError(f"Navidrome error: {subsonic.get('error') or subsonic}")
        return subsonic

    def stream_url(self, item_id: str) -> str:
        if not self.user or not self.password:
            raise RuntimeError("Navidrome credentials are not configured")
        encoded = self.password.encode("utf-8").hex()
        # Keep p=enc:... unencoded — Liquidsoap rejects %3A in the hex password.
        return (
            f"{self.base_url}/rest/stream.view"
            f"?u={self.user}&p=enc:{encoded}&v=1.16.1&c=AudioMuseRadio&f=mp3&id={item_id}"
        )

    def cover_art_url(self, item_id: str, size: int = 300) -> str | None:
        """Browser-facing URL — proxied through this app so Navidrome stays private."""
        if not item_id:
            return None
        return f"/api/cover/{item_id}?size={size}"

    def navidrome_cover_url(self, item_id: str, size: int = 300) -> str | None:
        if not item_id or not self.user or not self.password:
            return None
        encoded = self.password.encode("utf-8").hex()
        return (
            f"{self.base_url}/rest/getCoverArt.view"
            f"?u={self.user}&p=enc:{encoded}&v=1.16.1&c=AudioMuseRadio&id={item_id}&size={size}"
        )

    async def fetch_cover_art(self, item_id: str, size: int = 300) -> tuple[bytes, str] | None:
        url = self.navidrome_cover_url(item_id, size)
        if not url:
            return None
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url)
                response.raise_for_status()
                media_type = response.headers.get("content-type") or "image/jpeg"
                return response.content, media_type
        except Exception:
            logger.warning("Failed to fetch cover art for %s", item_id)
            return None

    async def get_song(self, item_id: str) -> TrackInfo:
        data = await self._request("getSong", {"id": item_id})
        song = data.get("song") or {}
        return TrackInfo(
            item_id=str(song.get("id") or item_id),
            title=song.get("title") or "Unknown",
            artist=song.get("artist") or song.get("albumArtist") or "Unknown",
            duration_sec=int(song.get("duration") or 0),
            album=str(song.get("album") or ""),
            year=_parse_year(song),
        )

    async def enrich_tracks(self, refs: list[TrackRef]) -> list[TrackInfo]:
        enriched: list[TrackInfo] = []
        for ref in refs:
            try:
                info = await self.get_song(ref.item_id)
            except Exception:
                logger.warning("Failed to resolve song %s, using AudioMuse metadata", ref.item_id)
                info = TrackInfo(
                    item_id=ref.item_id,
                    title=ref.title,
                    artist=ref.artist,
                    duration_sec=0,
                )
            enriched.append(info)
        return enriched


navidrome_client = NavidromeClient()
