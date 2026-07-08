import logging

from datetime import datetime, timezone



from app.knowledge import cache as knowledge_cache

from app.knowledge.database import KnowledgeSettings, TrackKnowledgeStatus

from app.knowledge.ollama import summarize_facts

from app.knowledge.settings import (

    effective_ollama_model,

    effective_ollama_url,

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

    ollama = effective_ollama_url(settings)

    model = effective_ollama_model(settings)



    try:

        all_snippets, search_sources = await gather_snippets(track, searxng_url=searxng)

    except Exception as exc:

        logger.warning("Snippet gather failed for %s: %s", item_id, exc)

        all_snippets, search_sources = [], []



    if not all_snippets:

        payload = _empty_payload(track, model, search_sources, rejected=0)

        return TrackKnowledgeStatus.no_facts, payload, ""



    try:

        raw_facts = await summarize_facts(

            ollama, model, track, all_snippets, settings.max_facts_per_track

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

        "providers": {"search": search_label, "llm": "ollama", "model": model},

        "generated_at": datetime.now(timezone.utc).isoformat(),

    }

    if not facts:

        return TrackKnowledgeStatus.no_facts, payload, ""

    return TrackKnowledgeStatus.ready, payload, ""





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

        "providers": {"search": search_label, "llm": "ollama", "model": model},

        "generated_at": datetime.now(timezone.utc).isoformat(),

    }


