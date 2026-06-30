import logging
from typing import Any
from urllib.parse import quote, unquote, urlparse

import httpx

from app.knowledge.snippets import Snippet

logger = logging.getLogger(__name__)

API = "https://en.wikipedia.org/w/api.php"
REST_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
USER_AGENT = "AlchemyFM/1.0 (https://github.com/audiomuse-radio)"


def _title_from_wikipedia_url(url: str) -> str:
    path = urlparse(url).path
    if "/wiki/" not in path:
        return ""
    return unquote(path.split("/wiki/", 1)[1].replace("_", " "))


async def _summary(client: httpx.AsyncClient, title: str) -> Snippet | None:
    title = title.strip()
    if not title:
        return None
    try:
        response = await client.get(
            f"{REST_SUMMARY}{quote(title.replace(' ', '_'), safe='')}",
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        logger.warning("Wikipedia summary failed for %s: %s", title, exc)
        return None

    extract = str(data.get("extract") or "").strip()
    page_url = str(data.get("content_urls", {}).get("desktop", {}).get("page") or "").strip()
    if not extract or not page_url.startswith("http"):
        return None
    return {
        "url": page_url,
        "title": str(data.get("title") or title) + " — Wikipedia",
        "snippet": extract[:500],
    }


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
    artist = str(track.get("artist") or "").strip()
    title = str(track.get("title") or "").strip()
    if not artist and not title:
        return []

    snippets: list[Snippet] = []
    seen_urls: set[str] = set()

    async with httpx.AsyncClient(timeout=20.0) as client:
        titles_to_try: list[str] = []
        for url in hint_urls or []:
            wiki_title = _title_from_wikipedia_url(url)
            if wiki_title:
                titles_to_try.append(wiki_title)

        search_queries = []
        if title and artist:
            search_queries.append(f'{title} {artist} song')
            search_queries.append(f'{title} ({artist} song)')
        if artist:
            search_queries.append(artist)

        for query in search_queries:
            found = await _search_title(client, query)
            if found:
                titles_to_try.append(found)

        for wiki_title in dict.fromkeys(titles_to_try):
            row = await _summary(client, wiki_title)
            if not row or row["url"] in seen_urls:
                continue
            seen_urls.add(row["url"])
            snippets.append(row)
            if len(snippets) >= 3:
                break

    return snippets
