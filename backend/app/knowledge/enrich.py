import logging

from datetime import datetime, timezone



from app.knowledge import cache as knowledge_cache

from app.knowledge.database import KnowledgeSettings, TrackKnowledgeStatus

from app.knowledge.llm import summarize as summarize_facts

from app.knowledge.settings import (

    effective_llm_provider,

    effective_model_label,

    effective_searxng_url,

)

from app.knowledge.sources import gather_snippets

from app.services.navidrome import navidrome_client



logger = logging.getLogger(__name__)





async def enrich_item(item_id: str, settings: KnowledgeSettings) -> tuple[TrackKnowledgeStatus, dict, str]:

    """Run search + summarize for one track. Returns (status, payload, error)."""

    try:

        info = await navidrome_client.get_song(item_id)

    except Exception as exc:

        return TrackKnowledgeStatus.failed, {}, f"Navidrome: {exc}"



    artist_info = await navidrome_client.get_artist_bio(item_id)

    track = {

        "item_id": info.item_id,

        "title": info.title,

        "artist": info.artist,

        "album": info.album,

        "year": info.year,

        "artist_mbid": artist_info.music_brainz_id,

    }

    searxng = effective_searxng_url(settings)

    model = effective_model_label(settings)



    extra_snippets = _bio_snippets(track, artist_info)



    try:

        all_snippets, search_sources = await gather_snippets(
            track, searxng_url=searxng, extra_snippets=extra_snippets
        )

    except Exception as exc:

        logger.warning("Snippet gather failed for %s: %s", item_id, exc)

        all_snippets, search_sources = [], []



    if not all_snippets:

        payload = _empty_payload(track, model, search_sources, rejected=0)

        return TrackKnowledgeStatus.no_facts, payload, ""



    try:

        raw_facts = await summarize_facts(

            settings,

            track,

            all_snippets,

            settings.max_facts_per_track,

            settings.max_fact_chars,

        )

    except Exception as exc:

        return TrackKnowledgeStatus.failed, {}, str(exc)



    facts = knowledge_cache.validate_facts(

        raw_facts, settings.min_confidence, settings.max_fact_chars

    )[: settings.max_facts_per_track]



    search_label = "+".join(search_sources) if search_sources else "none"

    payload = {

        "version": 1,

        "track": track,

        "facts": facts,

        "rejected_count": max(0, len(raw_facts) - len(facts)),

        "providers": {"search": search_label, "llm": effective_llm_provider(), "model": model},

        "generated_at": datetime.now(timezone.utc).isoformat(),

    }

    if not facts:

        return TrackKnowledgeStatus.no_facts, payload, ""

    return TrackKnowledgeStatus.ready, payload, ""





def _bio_snippets(track: dict, artist_info) -> list[dict]:
    """Turn the Last.fm/Navidrome artist biography into a grounded snippet.

    Previously fetched then discarded — it is rich prose the summarizer never
    saw. Needs a source URL to survive fact validation, so fall back to the
    MusicBrainz artist page when no Last.fm URL is available.
    """
    biography = str(getattr(artist_info, "biography", "") or "").strip()
    if not biography:
        return []
    url = str(getattr(artist_info, "info_url", "") or "").strip()
    if not url.startswith("http"):
        mbid = str(track.get("artist_mbid") or "").strip()
        url = f"https://musicbrainz.org/artist/{mbid}" if mbid else ""
    if not url.startswith("http"):
        return []
    artist = str(track.get("artist") or "").strip() or "Artist"
    return [
        {
            "url": url,
            "title": f"{artist} — artist biography",
            "snippet": biography[:600],
        }
    ]


def _empty_payload(

    track: dict,

    model: str,

    search_sources: list[str],

    rejected: int,

) -> dict:

    search_label = "+".join(search_sources) if search_sources else "none"

    return {

        "version": 1,

        "track": track,

        "facts": [],

        "rejected_count": rejected,

        "providers": {"search": search_label, "llm": effective_llm_provider(), "model": model},

        "generated_at": datetime.now(timezone.utc).isoformat(),

    }


