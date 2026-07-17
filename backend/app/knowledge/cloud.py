"""OpenAI-compatible cloud LLM client for knowledge enrichment.

One client covers OpenAI, Google Gemini (via its OpenAI-compatible endpoint),
OpenRouter, and any other provider that speaks the `/chat/completions` shape —
you select between them with the base URL. Reuses the exact same system + user
prompt as the local Ollama path so fact quality/policy is identical.
"""
import logging

import httpx

from app.knowledge.ollama import SYSTEM_PROMPT, _extract_json, build_user_prompt

logger = logging.getLogger(__name__)


async def summarize_facts(
    base_url: str,
    api_key: str,
    model: str,
    track: dict,
    snippets: list[dict],
    max_facts: int,
) -> list[dict]:
    if not base_url:
        raise RuntimeError("Cloud LLM base URL is not configured")
    if not api_key:
        raise RuntimeError("Cloud LLM API key is not configured")
    if not snippets:
        return []

    payload = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT.format(max_facts=max_facts)},
            {"role": "user", "content": build_user_prompt(track, snippets)},
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{base_url.rstrip('/')}/chat/completions", json=payload, headers=headers
        )
        response.raise_for_status()
        body = response.json()
    choices = body.get("choices") or []
    if not choices:
        return []
    content = ((choices[0].get("message") or {}).get("content")) or ""
    parsed = _extract_json(content)
    facts = parsed.get("facts") or []
    return facts if isinstance(facts, list) else []


async def test_connection(base_url: str, api_key: str, model: str) -> tuple[bool, str]:
    if not base_url:
        return False, "Base URL not configured"
    if not api_key:
        return False, "API key not configured"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                f"{base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            response.raise_for_status()
            data = response.json()
        names = [m.get("id", "") for m in (data.get("data") or [])]
        if model and names and not any(model in n for n in names):
            return True, f"Connected; model '{model}' not in list"
        return True, f"OK ({len(names)} model(s))"
    except Exception as exc:
        logger.warning("Cloud LLM health check failed: %s", exc)
        return False, str(exc)
