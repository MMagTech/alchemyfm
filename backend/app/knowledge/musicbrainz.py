import asyncio
import logging
import time
from typing import Any
from urllib.parse import quote

import httpx

from app.knowledge.snippets import Snippet

logger = logging.getLogger(__name__)

API_BASE = "https://musicbrainz.org/ws/2"
USER_AGENT = "AlchemyFM/1.0 (https://github.com/audiomuse-radio)"
_mb_last_call = 0.0


def _escape_lucene(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _build_search_query(track: dict[str, Any]) -> str:
    parts: list[str] = []
    title = str(track.get("title") or "").strip()
    artist = str(track.get("artist") or "").strip()
    album = str(track.get("album") or "").strip()
    if title:
        parts.append(f'recording:"{_escape_lucene(title)}"')
    if artist:
        parts.append(f'artist:"{_escape_lucene(artist)}"')
    if album:
        parts.append(f'release:"{_escape_lucene(album)}"')
    return " AND ".join(parts)


async def _throttle() -> None:
    global _mb_last_call
    now = time.monotonic()
    wait = 1.05 - (now - _mb_last_call)
    if wait > 0:
        await asyncio.sleep(wait)
    _mb_last_call = time.monotonic()


async def _get(client: httpx.AsyncClient, path: str) -> dict | None:
    await _throttle()
    try:
        response = await client.get(
            f"{API_BASE}{path}",
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        logger.warning("MusicBrainz request failed for %s: %s", path, exc)
        return None


def _format_length(ms: int | None) -> str:
    if not ms or ms <= 0:
        return ""
    total = ms // 1000
    minutes, seconds = divmod(total, 60)
    return f"{minutes}:{seconds:02d}"


def _pick_recording(recordings: list[dict], track: dict[str, Any]) -> dict | None:
    if not recordings:
        return None
    title_needle = str(track.get("title") or "").strip().lower()
    artist_needle = str(track.get("artist") or "").strip().lower()

    def score(row: dict) -> int:
        s = 0
        row_title = str(row.get("title") or "").lower()
        if title_needle and title_needle in row_title:
            s += 2
        if row_title == title_needle:
            s += 2
        for credit in row.get("artist-credit") or []:
            name = str((credit.get("artist") or {}).get("name") or credit.get("name") or "").lower()
            if artist_needle and artist_needle in name:
                s += 2
            if name == artist_needle:
                s += 1
        return s

    return max(recordings, key=score)


def _wikipedia_urls_from_rels(relations: list[dict]) -> list[str]:
    urls: list[str] = []
    for rel in relations or []:
        if str(rel.get("type") or "").lower() != "wikipedia":
            continue
        url = str((rel.get("url") or {}).get("resource") or "").strip()
        if "wikipedia.org" in url:
            urls.append(url)
    return urls


async def fetch_snippets(track: dict[str, Any]) -> tuple[list[Snippet], list[str]]:
    """Return MusicBrainz snippets and any linked Wikipedia URLs."""
    query = _build_search_query(track)
    if not query:
        return [], []

    snippets: list[Snippet] = []
    wikipedia_urls: list[str] = []

    async with httpx.AsyncClient(timeout=20.0) as client:
        search = await _get(
            client,
            f"/recording?query={quote(query, safe='')}&fmt=json&limit=5",
        )
        recordings = (search or {}).get("recordings") or []
        recording = _pick_recording(recordings, track)
        if not recording:
            return [], []

        mbid = str(recording.get("id") or "").strip()
        if not mbid:
            return [], []

        detail = await _get(
            client,
            f"/recording/{mbid}?inc=artist-credits+releases+url-rels&fmt=json",
        )
        if not detail:
            detail = recording

        title = str(detail.get("title") or track.get("title") or "Recording")
        artists = [
            str((c.get("artist") or {}).get("name") or c.get("name") or "").strip()
            for c in detail.get("artist-credit") or []
        ]
        artist = ", ".join(a for a in artists if a) or str(track.get("artist") or "")
        disambiguation = str(detail.get("disambiguation") or "").strip()
        length = _format_length(detail.get("length"))

        release_bits: list[str] = []
        for release in (detail.get("releases") or [])[:3]:
            rel_title = str(release.get("title") or "").strip()
            rel_date = str(release.get("date") or "").strip()
            if rel_title and rel_date:
                release_bits.append(f"{rel_title} ({rel_date})")
            elif rel_title:
                release_bits.append(rel_title)
            elif rel_date:
                release_bits.append(rel_date)

        lines = [f'"{title}" by {artist}.']
        if disambiguation:
            lines.append(f"Disambiguation: {disambiguation}.")
        if length:
            lines.append(f"Length: {length}.")
        if release_bits:
            lines.append(f"Appears on: {'; '.join(release_bits)}.")

        snippets.append(
            {
                "url": f"https://musicbrainz.org/recording/{mbid}",
                "title": f"{title} — MusicBrainz",
                "snippet": " ".join(lines),
            }
        )

        wikipedia_urls.extend(_wikipedia_urls_from_rels(detail.get("relations") or []))

        artist_mbid = ""
        for credit in detail.get("artist-credit") or []:
            artist_mbid = str((credit.get("artist") or {}).get("id") or "").strip()
            if artist_mbid:
                break
        if artist_mbid:
            artist_detail = await _get(
                client,
                f"/artist/{artist_mbid}?inc=url-rels&fmt=json",
            )
            if artist_detail:
                wikipedia_urls.extend(
                    _wikipedia_urls_from_rels(artist_detail.get("relations") or [])
                )
                artist_name = str(artist_detail.get("name") or artist).strip()
                area = str(((artist_detail.get("area") or {}).get("name")) or "").strip()
                life = ""
                begin = (artist_detail.get("life-span") or {}).get("begin")
                end = (artist_detail.get("life-span") or {}).get("end")
                if begin:
                    life = f" Active {begin}" + (f"–{end}" if end else "–present") + "."
                if area or life:
                    snippets.append(
                        {
                            "url": f"https://musicbrainz.org/artist/{artist_mbid}",
                            "title": f"{artist_name} — MusicBrainz",
                            "snippet": f"{artist_name}.{life} Origin: {area}.".strip(),
                        }
                    )

    deduped_wiki = list(dict.fromkeys(wikipedia_urls))
    return snippets, deduped_wiki
