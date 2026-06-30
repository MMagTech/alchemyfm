import logging

from app.knowledge import duckduckgo, musicbrainz, wikipedia
from app.knowledge.searxng import search as searxng_search
from app.knowledge.snippets import (
    get_cached,
    merge_snippets,
    set_cached,
)

logger = logging.getLogger(__name__)

SNIPPET_LIMIT = 12
ENRICH_EARLY_STOP = 8


async def gather_snippets(
    track: dict,
    *,
    searxng_url: str = "",
    skip_cache: bool = False,
) -> tuple[list[dict], list[str]]:
    """Collect web snippets from free APIs (MusicBrainz, Wikipedia, DuckDuckGo).

    Optional SearXNG is used only as a last resort when earlier sources return
    nothing. Results are memoized for 30 minutes per artist/title/album.
    """
    if not skip_cache:
        cached = get_cached(track)
        if cached:
            return cached

    snippets: list[dict] = []
    seen_urls: set[str] = set()
    sources_used: list[str] = []

    try:
        mb_snippets, wiki_hints = await musicbrainz.fetch_snippets(track)
        if mb_snippets:
            merge_snippets(snippets, seen_urls, mb_snippets, limit=SNIPPET_LIMIT)
            sources_used.append("musicbrainz")
    except Exception:
        logger.warning("MusicBrainz gather failed", exc_info=True)
        wiki_hints = []

    if len(snippets) < ENRICH_EARLY_STOP:
        try:
            wiki_snippets = await wikipedia.fetch_snippets(track, hint_urls=wiki_hints)
            if wiki_snippets:
                before = len(snippets)
                merge_snippets(snippets, seen_urls, wiki_snippets, limit=SNIPPET_LIMIT)
                if len(snippets) > before:
                    sources_used.append("wikipedia")
        except Exception:
            logger.warning("Wikipedia gather failed", exc_info=True)

    if len(snippets) < ENRICH_EARLY_STOP:
        try:
            ddg_snippets = await duckduckgo.fetch_snippets(track)
            if ddg_snippets:
                before = len(snippets)
                merge_snippets(snippets, seen_urls, ddg_snippets, limit=SNIPPET_LIMIT)
                if len(snippets) > before:
                    sources_used.append("duckduckgo")
        except Exception:
            logger.warning("DuckDuckGo gather failed", exc_info=True)

    if not snippets and searxng_url:
        query = _searxng_query(track)
        if query:
            try:
                hits = await searxng_search(searxng_url, query, limit=6)
                before = len(snippets)
                merge_snippets(snippets, seen_urls, hits, limit=SNIPPET_LIMIT)
                if len(snippets) > before:
                    sources_used.append("searxng")
            except Exception as exc:
                logger.warning("SearXNG fallback failed: %s", exc)

    if not skip_cache:
        set_cached(track, snippets, sources_used)
    return snippets, sources_used


def _searxng_query(track: dict) -> str:
    artist = str(track.get("artist") or "").strip()
    title = str(track.get("title") or "").strip()
    if not artist or not title:
        return ""
    return f'"{artist}" "{title}" song facts'


async def test_connection(searxng_url: str = "") -> tuple[bool, str]:
    """Probe free search sources with a well-known track."""
    track = {
        "artist": "The Beatles",
        "title": "Yesterday",
        "album": "Help!",
        "year": 1965,
    }
    snippets, sources = await gather_snippets(track, searxng_url=searxng_url, skip_cache=True)
    if snippets:
        label = ", ".join(sources) if sources else "unknown"
        return True, f"OK ({len(snippets)} snippet(s) via {label})"
    if searxng_url:
        return False, "No snippets from MusicBrainz, Wikipedia, DuckDuckGo, or SearXNG"
    return False, "No snippets from MusicBrainz, Wikipedia, or DuckDuckGo"
