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
    artist_mbid = str(track.get("artist_mbid") or "").strip()
    if title:
        parts.append(f'recording:"{_escape_lucene(title)}"')
    if artist_mbid:
        parts.append(f"arid:{artist_mbid}")
    elif artist:
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


# Recordings whose title/disambiguation contain these are alternate versions
# (remixes, edits, interludes, screwed/chopped, live) — prefer the plain studio
# recording so we don't caption facts with a 0:47 interlude or a 5.1 remix.
_ALT_VERSION_HINTS = (
    "remix",
    "mix",
    "live",
    "instrumental",
    "acoustic",
    "demo",
    "karaoke",
    "reprise",
    "interlude",
    "edit",
    "version",
    "screwed",
    "chopped",
    "a cappella",
    "acapella",
    "rerecord",
    "re-record",
    "making of",
    "documentary",
    "commentary",
    "snippet",
)


def _pick_recording(recordings: list[dict], track: dict[str, Any]) -> dict | None:
    if not recordings:
        return None
    title_needle = str(track.get("title") or "").strip().lower()
    artist_needle = str(track.get("artist") or "").strip().lower()
    album_needle = str(track.get("album") or "").strip().lower()
    artist_mbid = str(track.get("artist_mbid") or "").strip().lower()

    def score(row: dict) -> int:
        s = 0
        row_title = str(row.get("title") or "").strip().lower()
        disambig = str(row.get("disambiguation") or "").strip().lower()
        if title_needle and row_title == title_needle:
            s += 6
        elif title_needle and title_needle in row_title:
            s += 2
        # Prefer the recording that actually appears on the album now playing,
        # so a famous title doesn't resolve to a documentary/making-of cut.
        if album_needle:
            for release in row.get("releases") or []:
                rel_title = str(release.get("title") or "").strip().lower()
                if not rel_title:
                    continue
                if album_needle not in rel_title and rel_title not in album_needle:
                    continue
                leftover = rel_title.replace(album_needle, "")
                if any(hint in leftover for hint in _ALT_VERSION_HINTS):
                    continue  # e.g. "The Making of A Night at the Opera"
                s += 5
                break
        # Penalise alternate versions unless the requested title itself asks for one.
        extra = f"{row_title} {disambig}".replace(title_needle, "", 1)
        if any(hint in extra for hint in _ALT_VERSION_HINTS):
            s -= 4
        for credit in row.get("artist-credit") or []:
            artist = credit.get("artist") or {}
            name = str(artist.get("name") or credit.get("name") or "").lower()
            aid = str(artist.get("id") or "").strip().lower()
            if artist_mbid and aid == artist_mbid:
                s += 10
            if artist_needle and artist_needle in name:
                s += 2
            if name == artist_needle:
                s += 1
        return s

    return max(recordings, key=score)


def _credit_snippets(detail: dict, mbid: str, title: str) -> list[Snippet]:
    """Producer/performer/sample facts from recording-level relationships."""
    relations = detail.get("relations") or []
    producers: list[str] = []
    performers: list[str] = []
    samples: list[str] = []
    for rel in relations:
        rel_type = str(rel.get("type") or "").lower()
        artist_name = str((rel.get("artist") or {}).get("name") or "").strip()
        if artist_name:
            if rel_type in ("producer", "co-producer", "executive producer"):
                producers.append(artist_name)
            elif rel_type in ("vocal", "performer", "instrument", "performing orchestra"):
                attrs = ", ".join(a for a in (rel.get("attributes") or []) if a)
                performers.append(f"{artist_name} ({attrs})" if attrs else artist_name)
        sampled = rel.get("recording") or {}
        if sampled and rel_type in ("samples material", "interpolates", "samples"):
            s_title = str(sampled.get("title") or "").strip()
            s_artist = ", ".join(
                str((c.get("artist") or {}).get("name") or "").strip()
                for c in sampled.get("artist-credit") or []
            ).strip(", ")
            if s_title:
                verb = "Interpolates" if "interpolat" in rel_type else "Samples"
                samples.append(f'{verb} "{s_title}"' + (f" by {s_artist}" if s_artist else ""))

    lines: list[str] = []
    if producers:
        lines.append(f'"{title}" produced by {", ".join(dict.fromkeys(producers))}.')
    if performers:
        lines.append(f"Performers: {'; '.join(dict.fromkeys(performers))}.")
    if samples:
        lines.append(" ".join(dict.fromkeys(samples)) + ".")
    if not lines:
        return []
    return [
        {
            "url": f"https://musicbrainz.org/recording/{mbid}",
            "title": f"{title} — credits (MusicBrainz)",
            "snippet": " ".join(lines),
        }
    ]


async def _work_context(
    client: httpx.AsyncClient, detail: dict
) -> tuple[list[Snippet], list[str]]:
    """Composer/lyricist and any song-level Wikipedia link from the linked work."""
    work_id = ""
    for rel in detail.get("relations") or []:
        work = rel.get("work") or {}
        if work.get("id"):
            work_id = str(work["id"]).strip()
            break
    if not work_id:
        return [], []
    work = await _get(client, f"/work/{work_id}?inc=artist-rels+url-rels&fmt=json")
    if not work:
        return [], []
    writers: dict[str, list[str]] = {"composer": [], "lyricist": [], "writer": []}
    for rel in work.get("relations") or []:
        rel_type = str(rel.get("type") or "").lower()
        name = str((rel.get("artist") or {}).get("name") or "").strip()
        if name and rel_type in writers:
            writers[rel_type].append(name)
    wiki_urls = _wikipedia_urls_from_rels(work.get("relations") or [])
    bits: list[str] = []
    for role, names in writers.items():
        if names:
            bits.append(f"{role.capitalize()}: {', '.join(dict.fromkeys(names))}")
    if not bits:
        return [], wiki_urls
    work_title = str(work.get("title") or "").strip()
    return (
        [
            {
                "url": f"https://musicbrainz.org/work/{work_id}",
                "title": f"{work_title or 'Work'} — writers (MusicBrainz)",
                "snippet": ". ".join(bits) + ".",
            }
        ],
        wiki_urls,
    )


async def _artist_context(
    client: httpx.AsyncClient,
    artist_mbid: str,
    fallback_name: str,
) -> tuple[list[Snippet], list[str]]:
    """Artist-level snippets and Wikipedia hints from a known MusicBrainz artist ID."""
    artist_detail = await _get(
        client,
        f"/artist/{artist_mbid}?inc=url-rels&fmt=json",
    )
    if not artist_detail:
        return [], []

    wikipedia_urls = _wikipedia_urls_from_rels(artist_detail.get("relations") or [])
    snippets: list[Snippet] = []
    artist_name = str(artist_detail.get("name") or fallback_name).strip()
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
    return snippets, wikipedia_urls


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
    artist_mbid = str(track.get("artist_mbid") or "").strip()
    if not query and not artist_mbid:
        return [], []

    snippets: list[Snippet] = []
    wikipedia_urls: list[str] = []

    async with httpx.AsyncClient(timeout=20.0) as client:
        recording = None
        if query:
            search = await _get(
                client,
                f"/recording?query={quote(query, safe='')}&fmt=json&limit=10",
            )
            recordings = (search or {}).get("recordings") or []
            recording = _pick_recording(recordings, track)

        if not recording:
            if artist_mbid:
                artist_snippets, artist_wiki = await _artist_context(
                    client,
                    artist_mbid,
                    str(track.get("artist") or ""),
                )
                snippets.extend(artist_snippets)
                wikipedia_urls.extend(artist_wiki)
            return snippets, list(dict.fromkeys(wikipedia_urls))

        mbid = str(recording.get("id") or "").strip()
        if not mbid:
            return snippets, list(dict.fromkeys(wikipedia_urls))

        detail = await _get(
            client,
            f"/recording/{mbid}"
            "?inc=artist-credits+releases+url-rels+work-rels+recording-rels+artist-rels"
            "&fmt=json",
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

        snippets.extend(_credit_snippets(detail, mbid, title))
        work_snippets, work_wiki = await _work_context(client, detail)
        snippets.extend(work_snippets)
        wikipedia_urls.extend(work_wiki)

        wikipedia_urls.extend(_wikipedia_urls_from_rels(detail.get("relations") or []))

        resolved_artist_mbid = artist_mbid
        if not resolved_artist_mbid:
            for credit in detail.get("artist-credit") or []:
                resolved_artist_mbid = str((credit.get("artist") or {}).get("id") or "").strip()
                if resolved_artist_mbid:
                    break
        if resolved_artist_mbid:
            artist_snippets, artist_wiki = await _artist_context(
                client,
                resolved_artist_mbid,
                artist,
            )
            existing_urls = {s["url"] for s in snippets}
            for row in artist_snippets:
                if row["url"] not in existing_urls:
                    snippets.append(row)
                    existing_urls.add(row["url"])
            wikipedia_urls.extend(artist_wiki)

    deduped_wiki = list(dict.fromkeys(wikipedia_urls))
    return snippets, deduped_wiki
