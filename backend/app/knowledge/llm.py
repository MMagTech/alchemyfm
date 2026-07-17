"""Provider dispatch for the enrichment language model.

Single choke point between the enricher and whichever backend is configured.
Local Ollama is the default; set KNOWLEDGE_LLM_PROVIDER=openai to route through
the OpenAI-compatible cloud client instead. Everything upstream (snippets,
caching, validation) is provider-agnostic.
"""
from app.knowledge import cloud, ollama
from app.knowledge.database import KnowledgeSettings
from app.knowledge.settings import (
    effective_llm_api_key,
    effective_llm_base_url,
    effective_llm_model,
    effective_llm_provider,
    effective_ollama_model,
    effective_ollama_url,
)


async def summarize(
    row: KnowledgeSettings | None,
    track: dict,
    snippets: list[dict],
    max_facts: int,
) -> list[dict]:
    if effective_llm_provider(row) == "openai":
        return await cloud.summarize_facts(
            effective_llm_base_url(row),
            effective_llm_api_key(),
            effective_llm_model(row),
            track,
            snippets,
            max_facts,
        )
    return await ollama.summarize_facts(
        effective_ollama_url(row),
        effective_ollama_model(row),
        track,
        snippets,
        max_facts,
    )


async def test_connection() -> tuple[bool, str]:
    """Probe the configured backend. Takes no DB row on purpose: provider
    config is env-only, so the admin endpoint can close its knowledge-DB
    session before awaiting this network call (see read_knowledge_settings).
    """
    if effective_llm_provider() == "openai":
        return await cloud.test_connection(
            effective_llm_base_url(),
            effective_llm_api_key(),
            effective_llm_model(),
        )
    return await ollama.test_connection(
        effective_ollama_url(),
        effective_ollama_model(),
    )
