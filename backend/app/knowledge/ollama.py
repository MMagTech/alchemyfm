import json
import logging
import re

import httpx

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You extract short music trivia for radio listeners from the provided snippets only.

Priority: facts about the exact track (title + artist) first. MusicBrainz recording snippets are valid for song_fact (featured artists, length, which album it appears on).

Rules:
- Use ONLY information explicitly stated in the snippets. Do not invent or guess.
- If a detail is directly stated, include it. Do not infer across snippets unless the link is explicit.
- Prefer song_fact when snippets support it. Use album_fact or artist_fact only when nothing track-specific is available.
- Focus on music: release, collaborators, production, samples, charts. Skip gossip, rumors, crime, and personal drama unless clearly about this track in the snippets.
- Categories: song_fact, artist_fact, album_fact, producer_fact, sample_fact.
- Each fact needs confidence 0.0-1.0 and source URLs copied exactly from the snippet URLs.
- Confidence: 0.85+ if verbatim in snippet; 0.65+ if clearly supported; omit below 0.65.
- Return up to {max_facts} distinct facts (different angles, no repeats).
- JSON only: {{"facts":[{{"category":"...","text":"...","confidence":0.8,"sources":[{{"url":"...","title":"..."}}]}}]}}
- If nothing reliable, return {{"facts":[]}}."""


def _extract_json(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        return json.loads(match.group(0))
    raise ValueError("No JSON object in model response")


async def summarize_facts(
    base_url: str,
    model: str,
    track: dict,
    snippets: list[dict],
    max_facts: int,
) -> list[dict]:
    if not base_url:
        raise RuntimeError("Ollama URL is not configured")
    if not snippets:
        return []

    snippet_block = "\n\n".join(
        f"Title: {s['title']}\nURL: {s['url']}\nSnippet: {s['snippet']}"
        for s in snippets[:12]
    )
    user_prompt = (
        f"Listeners are hearing \"{track.get('title')}\" by {track.get('artist')} right now.\n"
        f"Album: {track.get('album') or 'unknown'}\n"
        f"Year: {track.get('year') or 'unknown'}\n\n"
        f"Search snippets:\n{snippet_block}"
    )
    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        # Unload weights from GPU immediately after each job — do not hold VRAM.
        "keep_alive": 0,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT.format(max_facts=max_facts)},
            {"role": "user", "content": user_prompt},
        ],
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(f"{base_url.rstrip('/')}/api/chat", json=payload)
        response.raise_for_status()
        body = response.json()
    content = (body.get("message") or {}).get("content") or ""
    parsed = _extract_json(content)
    facts = parsed.get("facts") or []
    return facts if isinstance(facts, list) else []


async def release_gpu(base_url: str, model: str = "") -> None:
    """Ask Ollama to drop loaded model weights from GPU VRAM."""
    if not base_url:
        return
    base = base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            models_to_clear: list[str] = []
            if model:
                models_to_clear.append(model)
            resp = await client.get(f"{base}/api/ps")
            if resp.status_code == 200:
                for row in resp.json().get("models") or []:
                    name = str(row.get("name") or "").strip()
                    if name and name not in models_to_clear:
                        models_to_clear.append(name)
            for name in models_to_clear:
                await client.post(
                    f"{base}/api/chat",
                    json={"model": name, "messages": [], "keep_alive": 0},
                )
        logger.info("Released Ollama GPU for %s model(s)", len(models_to_clear))
    except Exception:
        logger.warning("Failed to release Ollama GPU", exc_info=True)


async def test_connection(base_url: str, model: str) -> tuple[bool, str]:
    if not base_url:
        return False, "URL not configured"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"{base_url.rstrip('/')}/api/tags")
            response.raise_for_status()
            tags = response.json().get("models") or []
        names = [m.get("name", "") for m in tags]
        if model and not any(model in n for n in names):
            return True, f"Connected; model '{model}' not in tag list"
        return True, f"OK ({len(names)} model(s))"
    except Exception as exc:
        logger.warning("Ollama health check failed: %s", exc)
        return False, str(exc)
