import logging
import re
from typing import Any
from urllib.parse import quote, unquote, urlparse

import httpx

from app.knowledge.snippets import Snippet

logger = logging.getLogger(__name__)

API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "AlchemyFM/1.0 (https://github.com/audiomuse-radio)"

# Sections (case-insensitive substring match) that carry real song trivia, in
# rough priority order. The article lead is always taken first; these add the
# specific detail the lead omits.
TRIVIA_SECTIONS = (
    "background",
    "writing",
    "composition",
    "music and lyrics",
    "recording",
    "production",
    "inspiration",
    "sample",
    "music video",
    "promotional video",
    "critical reception",
    "legacy",
    "cultural impact",
    "in popular culture",
    "usage in media",
)

MAX_SECTION_SNIPPETS = 5
# Split on level-2 headers only (== ... ==), keeping subsections as body text.
# Anchored to line starts so consecutive headers each match without swallowing
# the blank lines that separate them.
SECTION_HEADER_RE = re.compile(r"(?m)^==(?!=)\s*(.+?)\s*(?<!=)==$")
SUBHEADER_RE = re.compile(r"={2,6}\s*(.+?)\s*={2,6}")


def _title_from_wikipedia_url(url: str) -> str:
    path = urlparse(url).path
    if "/wiki/" not in path:
        return ""
    return unquote(path.split("/wiki/", 1)[1].replace("_", " "))


async def _fetch_article(client: httpx.AsyncClient, title: str) -> dict | None:
    """Fetch the full plaintext extract for a page. Returns canonical title,
    extract, and whether it is a disambiguation page (which we skip)."""
    title = title.strip()
    if not title:
        return None
    try:
        response = await client.get(
            API,
            params={
                "action": "query",
                "prop": "extracts|pageprops",
                "explaintext": "1",
                "redirects": "1",
                "titles": title,
                "format": "json",
            },
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        response.raise_for_status()
        pages = response.json().get("query", {}).get("pages") or {}
    except Exception as exc:
        logger.warning("Wikipedia extract failed for %s: %s", title, exc)
        return None

    for _, page in pages.items():
        if "missing" in page:
            continue
        extract = str(page.get("extract") or "").strip()
        if not extract:
            continue
        is_disambig = "disambiguation" in (page.get("pageprops") or {}) or (
            "may refer to:" in extract[:200].lower()
        )
        return {
            "title": str(page.get("title") or title),
            "extract": extract,
            "is_disambig": is_disambig,
        }
    return None


def _split_sections(extract: str) -> tuple[str, list[tuple[str, str]]]:
    """Return (lead, [(header, body), ...]) from a plaintext article extract.

    Subsection headers within a section are flattened to inline "Name:" labels
    so the body reads cleanly instead of leaking raw ``=== ... ===`` markers.
    """
    parts = SECTION_HEADER_RE.split(extract)
    lead = parts[0].strip()
    sections: list[tuple[str, str]] = []
    for i in range(1, len(parts) - 1, 2):
        header = parts[i].strip()
        body = SUBHEADER_RE.sub(r"\1: ", parts[i + 1])
        body = re.sub(r"\s+", " ", body).strip()
        if header and body:
            sections.append((header, body))
    return lead, sections


def _page_url(title: str, anchor: str = "") -> str:
    url = f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'), safe='')}"
    if anchor:
        url += "#" + quote(anchor.replace(" ", "_"), safe="")
    return url


def _article_snippets(article: dict, *, section_budget: int) -> list[Snippet]:
    title = article["title"]
    lead, sections = _split_sections(article["extract"])
    out: list[Snippet] = []
    if lead:
        out.append(
            {
                "url": _page_url(title),
                "title": f"{title} — Wikipedia",
                "snippet": lead,  # merge_snippets clips (see snippets.clip)
            }
        )
    picked = 0
    for want in TRIVIA_SECTIONS:
        if picked >= section_budget:
            break
        for header, body in sections:
            if picked >= section_budget:
                break
            if want in header.lower():
                out.append(
                    {
                        "url": _page_url(title, header),
                        "title": f"{title} — {header} (Wikipedia)",
                        "snippet": body,  # merge_snippets clips (see snippets.clip)
                    }
                )
                picked += 1
                break
    return out


async def _search_title(client: httpx.AsyncClient, query: str) -> str:
    try:
        response = await client.get(
            API,
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "format": "json",
                "srlimit": 3,
            },
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        hits = response.json().get("query", {}).get("search") or []
        if not hits:
            return ""
        return str(hits[0].get("title") or "").strip()
    except Exception as exc:
        logger.warning("Wikipedia search failed for %s: %s", query, exc)
        return ""


async def fetch_snippets(
    track: dict[str, Any],
    *,
    hint_urls: list[str] | None = None,
) -> list[Snippet]:
    """Pull the song's Wikipedia article as lead + trivia-dense sections.

    Prefers the exact song page (via MusicBrainz-linked hint URLs, then search),
    then the album page for extra context. The artist page is skipped here — its
    biography is supplied separately and its lead is generic for track trivia.
    """
    artist = str(track.get("artist") or "").strip()
    title = str(track.get("title") or "").strip()
    album = str(track.get("album") or "").strip()
    if not artist and not title:
        return []

    snippets: list[Snippet] = []
    seen_urls: set[str] = set()

    async with httpx.AsyncClient(timeout=20.0) as client:
        # 1) Song page candidates: MusicBrainz hints first, then targeted search.
        song_titles: list[str] = []
        for url in hint_urls or []:
            wiki_title = _title_from_wikipedia_url(url)
            if wiki_title:
                song_titles.append(wiki_title)
        if title and artist:
            for query in (f"{title} {artist} song", f"{title} ({artist} song)"):
                found = await _search_title(client, query)
                if found:
                    song_titles.append(found)

        song_added = False
        for wiki_title in dict.fromkeys(song_titles):
            article = await _fetch_article(client, wiki_title)
            if not article or article["is_disambig"]:
                continue
            for row in _article_snippets(article, section_budget=MAX_SECTION_SNIPPETS):
                if row["url"] in seen_urls:
                    continue
                seen_urls.add(row["url"])
                snippets.append(row)
            song_added = True
            break

        # 2) Album page (lead only) for release/production context.
        if album and artist:
            album_title = await _search_title(client, f"{album} {artist} album")
            if album_title:
                article = await _fetch_article(client, album_title)
                if article and not article["is_disambig"]:
                    budget = 2 if song_added else MAX_SECTION_SNIPPETS
                    for row in _article_snippets(article, section_budget=budget):
                        if row["url"] in seen_urls:
                            continue
                        seen_urls.add(row["url"])
                        snippets.append(row)

    return snippets
