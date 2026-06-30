import logging
from typing import Any

import httpx

from app.knowledge.snippets import Snippet

logger = logging.getLogger(__name__)

ENDPOINT = "https://api.duckduckgo.com/"
USER_AGENT = "AlchemyFM/1.0 (https://github.com/audiomuse-radio)"


def _parse_topics(topics: list, heading: str, limit: int) -> list[Snippet]:
    snippets: list[Snippet] = []
    for topic in topics:
        if not topic:
            continue
        if isinstance(topic.get("Topics"), list):
            for sub in topic["Topics"]:
                text = str(sub.get("Text") or "").strip()
                if not text:
                    continue
                url = str(sub.get("FirstURL") or "").strip()
                if not url.startswith("http"):
                    continue
                snippets.append(
                    {
                        "url": url,
                        "title": str(sub.get("Name") or heading or "DuckDuckGo")[:200],
                        "snippet": text[:500],
                    }
                )
                if len(snippets) >= limit:
                    return snippets
        else:
            text = str(topic.get("Text") or "").strip()
            if not text:
                continue
            url = str(topic.get("FirstURL") or "").strip()
            if not url.startswith("http"):
                continue
            snippets.append(
                {
                    "url": url,
                    "title": str(topic.get("Name") or heading or "DuckDuckGo")[:200],
                    "snippet": text[:500],
                }
            )
            if len(snippets) >= limit:
                return snippets
    return snippets


async def fetch_snippets(track: dict[str, Any], *, limit: int = 5) -> list[Snippet]:
    artist = str(track.get("artist") or "").strip()
    title = str(track.get("title") or "").strip()
    if not artist and not title:
        return []

    query = f'{artist} "{title}" song facts'.strip()
    params = {
        "q": query,
        "format": "json",
        "no_html": "1",
        "skip_disambig": "1",
        "no_redirect": "1",
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                ENDPOINT,
                params=params,
                headers={"User-Agent": USER_AGENT},
            )
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        logger.warning("DuckDuckGo request failed: %s", exc)
        return []

    snippets: list[Snippet] = []
    abstract = str(data.get("AbstractText") or data.get("Abstract") or "").strip()
    abstract_url = str(data.get("AbstractURL") or "").strip()
    heading = str(data.get("Heading") or artist or title).strip()
    if abstract and abstract_url.startswith("http"):
        snippets.append(
            {
                "url": abstract_url,
                "title": f"{heading} — DuckDuckGo",
                "snippet": abstract[:500],
            }
        )

    topics = data.get("RelatedTopics")
    if isinstance(topics, list):
        snippets.extend(_parse_topics(topics, heading, max(0, limit - len(snippets))))

    return snippets[:limit]
