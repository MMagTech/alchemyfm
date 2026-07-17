"""Shared snippet shape and in-memory cache for knowledge search sources."""

from __future__ import annotations

import time
from typing import Any

Snippet = dict[str, str]

CACHE_TTL_SEC = 30 * 60
_cache: dict[str, tuple[float, list[Snippet], list[str]]] = {}


def track_cache_key(track: dict[str, Any]) -> str:
    artist = str(track.get("artist") or "").strip().lower()
    title = str(track.get("title") or "").strip().lower()
    album = str(track.get("album") or "").strip().lower()
    return f"{artist}|{title}|{album}"


def get_cached(track: dict[str, Any]) -> tuple[list[Snippet], list[str]] | None:
    key = track_cache_key(track)
    hit = _cache.get(key)
    if not hit:
        return None
    at, snippets, sources = hit
    if time.monotonic() - at >= CACHE_TTL_SEC:
        _cache.pop(key, None)
        return None
    return [dict(s) for s in snippets], list(sources)


def set_cached(track: dict[str, Any], snippets: list[Snippet], sources: list[str]) -> None:
    _cache[track_cache_key(track)] = (time.monotonic(), [dict(s) for s in snippets], list(sources))


SNIPPET_CHARS = 600


def clip(text: str, limit: int = SNIPPET_CHARS) -> str:
    """Shorten text without handing the model a half-written word.

    A bare text[:limit] stops dead with no signal, so the summarizer reads the
    partial tail as complete -- a producer list cut at "...and Chink Sa" came
    back as a fact naming "Chink Sa". Backing off to a word boundary and
    marking the cut with an ellipsis tells the model the tail is unreliable.
    """
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"


def merge_snippets(
    target: list[Snippet],
    seen_urls: set[str],
    rows: list[Snippet],
    *,
    limit: int = 12,
) -> None:
    for row in rows:
        if len(target) >= limit:
            return
        url = str(row.get("url") or "").strip()
        if not url.startswith("http") or url in seen_urls:
            continue
        snippet = str(row.get("snippet") or "").strip()
        title = str(row.get("title") or url).strip()
        if not snippet:
            continue
        seen_urls.add(url)
        target.append(
            {
                "url": url,
                "title": title[:200],
                # Single clip point: sources hand over untrimmed text so this is
                # the only place a snippet is shortened, and only one way.
                "snippet": clip(snippet),
            }
        )
