import logging

import httpx

logger = logging.getLogger(__name__)


async def search(base_url: str, query: str, limit: int = 8) -> list[dict]:
    if not base_url:
        raise RuntimeError("SearXNG URL is not configured")
    params = {"q": query, "format": "json", "language": "en"}
    headers = {"User-Agent": "AlchemyFM/1.0 (https://github.com/audiomuse-radio)"}
    async with httpx.AsyncClient(timeout=45.0) as client:
        response = await client.get(
            f"{base_url.rstrip('/')}/search", params=params, headers=headers
        )
        response.raise_for_status()
        payload = response.json()
    results = payload.get("results") or []
    snippets: list[dict] = []
    for row in results[:limit]:
        url = str(row.get("url") or "").strip()
        if not url.startswith("http"):
            continue
        snippets.append(
            {
                "url": url,
                "title": str(row.get("title") or url)[:200],
                "snippet": str(row.get("content") or row.get("snippet") or "")[:500],
            }
        )
    return snippets


async def test_connection(base_url: str) -> tuple[bool, str]:
    if not base_url:
        return False, "URL not configured (optional fallback)"
    try:
        results = await search(base_url, "The Beatles Yesterday song facts", limit=3)
        if results:
            return True, f"OK ({len(results)} result(s))"
        return False, "Reachable but returned no results (engines may be suspended)"
    except Exception as exc:
        logger.warning("SearXNG health check failed: %s", exc)
        return False, str(exc)
