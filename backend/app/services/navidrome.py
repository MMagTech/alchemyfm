import asyncio
import logging
import re
import time
from dataclasses import dataclass
from html import unescape
from urllib.parse import quote_plus

import httpx

from app.config import settings
from app.schemas import NowPlaying, TrackInfo, TrackRef

logger = logging.getLogger(__name__)

ARTIST_BIO_CACHE_TTL_SEC = 86400
ARTIST_BIO_EMPTY_CACHE_TTL_SEC = 3600


@dataclass(frozen=True)
class NavidromePlaylist:
    id: str
    name: str


@dataclass(frozen=True)
class ArtistBio:
    biography: str
    info_url: str | None = None
    music_brainz_id: str | None = None


def _sanitize_biography(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\s*Read more on Last\.fm\.?\s*$", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_artist_name(s: str) -> str:
    s = s.strip().lower()
    for ch in ("\u2018", "\u2019", "\u2032", "`", "\u201c", "\u201d"):
        s = s.replace(ch, "'")
    return re.sub(r"\s+", " ", s).strip()


def _artist_name_variants(artist: str) -> set[str]:
    norm = _normalize_artist_name(artist)
    variants = {norm}
    if ", " in artist:
        last, first = artist.split(", ", 1)
        variants.add(_normalize_artist_name(f"{first} {last}"))
    if norm.endswith(" the"):
        base = norm[: -len(" the")].strip()
        if base:
            variants.add(f"the {base}")
    if norm.startswith("the "):
        base = norm[4:].strip()
        if base:
            variants.add(f"{base} the")
    return variants


def artists_match(a: str, b: str) -> bool:
    if not a.strip() or not b.strip():
        return True
    return bool(_artist_name_variants(a) & _artist_name_variants(b))


def _lastfm_wiki_url(artist_name: str) -> str:
    return f"https://www.last.fm/music/{quote_plus(artist_name.strip())}/+wiki"


def bio_read_more_url(raw_url: str | None, artist_name: str) -> str | None:
    """Full biography page — Navidrome's lastFmUrl is often the band's official site, not Last.fm."""
    name = artist_name.strip()
    if not name:
        return None
    if raw_url and "last.fm" in raw_url.lower():
        base = raw_url.rstrip("/").split("?")[0]
        if base.endswith("/+wiki"):
            return base
        if "/music/" in base.lower():
            return f"{base}/+wiki"
    return _lastfm_wiki_url(name)


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
        self._bio_cache: dict[str, tuple[ArtistBio, float]] = {}
        self._star_cache: dict[str, tuple[bool, float]] = {}
        self._http: httpx.AsyncClient | None = None
        # Songs appended to each playlist this process lifetime (avoids slow getPlaylist).
        self._playlist_appended: dict[str, set[str]] = {}

    def _http_client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=60.0)
        return self._http

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
        client = self._http_client()
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

    async def get_song_raw(self, item_id: str) -> dict:
        data = await self._request("getSong", {"id": item_id})
        return data.get("song") or {}

    def is_hearted(self, song: dict) -> bool:
        return bool(song.get("starred"))

    def _set_star_cache(self, item_id: str, hearted: bool) -> None:
        self._star_cache[str(item_id)] = (hearted, time.time())

    def _invalidate_star_cache(self, item_id: str) -> None:
        self._star_cache.pop(str(item_id), None)

    async def is_song_hearted(self, item_id: str) -> bool:
        key = str(item_id)
        cached = self._star_cache.get(key)
        if cached and (time.time() - cached[1]) < 120:
            return cached[0]
        song = await self.get_song_raw(item_id)
        hearted = self.is_hearted(song)
        self._set_star_cache(key, hearted)
        return hearted

    async def star_song(self, item_id: str) -> None:
        await self._request("star", {"id": item_id})
        self._set_star_cache(item_id, True)

    async def unstar_song(self, item_id: str) -> None:
        await self._request("unstar", {"id": item_id})
        self._set_star_cache(item_id, False)

    async def list_playlists(self) -> list[NavidromePlaylist]:
        data = await self._request("getPlaylists")
        playlists = data.get("playlists") or {}
        raw = playlists.get("playlist") or []
        if isinstance(raw, dict):
            raw = [raw]
        result: list[NavidromePlaylist] = []
        for pl in raw:
            pl_id = str(pl.get("id") or "").strip()
            if not pl_id:
                continue
            result.append(NavidromePlaylist(id=pl_id, name=str(pl.get("name") or "Untitled")))
        return sorted(result, key=lambda p: p.name.lower())

    async def get_playlist(self, playlist_id: str) -> dict:
        data = await self._request("getPlaylist", {"id": playlist_id})
        return data.get("playlist") or {}

    async def song_in_playlist(self, playlist_id: str, item_id: str) -> bool:
        playlist = await self.get_playlist(playlist_id)
        entries = playlist.get("entry") or []
        if isinstance(entries, dict):
            entries = [entries]
        return any(str(entry.get("id")) == str(item_id) for entry in entries)

    async def append_song_to_playlist(self, playlist_id: str, item_id: str) -> None:
        await self._request(
            "updatePlaylist",
            {"playlistId": playlist_id, "songIdToAdd": item_id},
        )

    async def add_song_to_playlist(self, playlist_id: str, item_id: str) -> bool:
        """Append song if not already in playlist. Returns True if newly added."""
        if await self.song_in_playlist(playlist_id, item_id):
            return False
        await self.append_song_to_playlist(playlist_id, item_id)
        return True

    async def heart_song_with_playlist(
        self, item_id: str, playlist_id: str | None
    ) -> bool:
        """Star song and queue playlist append when configured. Returns playlist_added."""
        await self.star_song(item_id)
        if not playlist_id:
            return False

        appended = self._playlist_appended.setdefault(playlist_id, set())
        if item_id in appended:
            return False

        appended.add(item_id)
        asyncio.create_task(self._append_playlist_background(playlist_id, item_id))
        return True

    async def _append_playlist_background(self, playlist_id: str, item_id: str) -> None:
        try:
            await self.append_song_to_playlist(playlist_id, item_id)
        except Exception:
            logger.exception(
                "Background playlist append failed for %s in %s",
                item_id,
                playlist_id,
            )
            self._playlist_appended.get(playlist_id, set()).discard(item_id)

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

    async def _song_artist_id(self, item_id: str) -> str | None:
        data = await self._request("getSong", {"id": item_id})
        song = data.get("song") or {}
        artist_id = str(song.get("artistId") or "").strip()
        return artist_id or None

    async def get_artist_bio(self, item_id: str) -> ArtistBio:
        """Artist biography from Navidrome getArtistInfo2 (cached per artist ID)."""
        if not item_id:
            return ArtistBio(biography="")
        try:
            artist_id = await self._song_artist_id(item_id)
        except Exception:
            logger.warning("Failed to resolve artist for song %s", item_id)
            artist_id = None
        cache_key = artist_id or item_id
        cached = self._bio_cache.get(cache_key)
        if cached and cached[1] > time.monotonic():
            return cached[0]
        lookup_id = artist_id or item_id
        try:
            data = await self._request("getArtistInfo2", {"id": lookup_id, "count": 0})
            info = data.get("artistInfo2") or {}
            biography = _sanitize_biography(str(info.get("biography") or ""))
            info_url = str(info.get("lastFmUrl") or "").strip() or None
            mbid = str(info.get("musicBrainzId") or "").strip() or None
            result = ArtistBio(biography=biography, info_url=info_url, music_brainz_id=mbid)
        except Exception:
            logger.warning("Failed to fetch artist info for %s", lookup_id)
            result = ArtistBio(biography="")
        ttl = ARTIST_BIO_CACHE_TTL_SEC if result.biography else ARTIST_BIO_EMPTY_CACHE_TTL_SEC
        self._bio_cache[cache_key] = (result, time.monotonic() + ttl)
        return result

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


async def attach_artist_bio(np: NowPlaying | None) -> NowPlaying | None:
    cleared = {"artist_bio": None, "artist_bio_url": None}
    if not np:
        return np
    if not np.item_id:
        return np.model_copy(update=cleared)
    try:
        song = await navidrome_client.get_song(np.item_id)
    except Exception:
        return np.model_copy(update=cleared)
    if not artists_match(song.artist, np.artist):
        logger.info(
            "Skipping artist bio: item %s is %r, now playing shows %r",
            np.item_id,
            song.artist,
            np.artist,
        )
        return np.model_copy(update=cleared)
    bio = await navidrome_client.get_artist_bio(np.item_id)
    if not bio.biography:
        return np.model_copy(update=cleared)
    read_more = bio_read_more_url(bio.info_url, song.artist)
    return np.model_copy(
        update={"artist_bio": bio.biography, "artist_bio_url": read_more}
    )


async def attach_operator_heart(
    np: NowPlaying | None,
    *,
    is_admin: bool,
) -> NowPlaying | None:
    if not np or not np.item_id or not is_admin:
        return np
    try:
        hearted = await navidrome_client.is_song_hearted(np.item_id)
    except Exception:
        logger.warning("Failed to read heart state for %s", np.item_id)
        return np
    return np.model_copy(update={"hearted": hearted})
