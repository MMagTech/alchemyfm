# Plugin Reuse of AudioMuse’s Configured LLM

**Date:** 2026-07-17  
**Scope:** Read-only (repo + live GET probes). Secrets redacted. No code or state changes.

**Live instance:** `AUDIOMUSE_URL` from `.env` → `http://192.168.1.10:8387`  
**Auth:** `Authorization: Bearer ***REDACTED***`

**Goal:** Determine whether `alchemy_fm_bridge` can reuse AudioMuse’s own configured LLM (OpenAI or Ollama) instead of maintaining separate LLM settings.

---

## 1. Does `plugin.api` expose LLM provider settings?

| | |
|---|---|
| **Answer** | **Yes** — via `from plugin.api import config` (read-only core config). No dedicated chat helper or `get_config()`. |
| **Confidence** | high |

### Evidence

Upstream `plugin/api.py` exports `config` in `__all__` and documents “read access to the core config.”

`docs/PLUGIN.md` API table:

> `| config | Read-only access to the app configuration values. |`

Exact import:

```python
from plugin.api import config

config.AI_MODEL_PROVIDER   # e.g. "OLLAMA"
config.OLLAMA_SERVER_URL
config.OLLAMA_MODEL_NAME
config.OPENAI_SERVER_URL
config.OPENAI_MODEL_NAME
config.GEMINI_MODEL_NAME
config.MISTRAL_MODEL_NAME
# In-process only (not in public GET /api/config):
config.OPENAI_API_KEY
config.GEMINI_API_KEY
config.MISTRAL_API_KEY
```

(Source: upstream `config.py` attribute names; live HTTP uses lowercase snapshots below.)

**Not in `plugin.api`:** any `chat()`, `generate_text()`, or LLM client.

Alchemy FM already reads non-secret config over HTTP in `_audiomuse_mood_labels` via `audiomuse_get("/api/config")` (`audiomuse-plugins/alchemy_fm_bridge/__init__.py:1071`).

---

## 2. General in-process LLM completion helper?

| | |
|---|---|
| **Answer** | **No stable plugin helper.** Core has `tasks.ai.api.generate_text(prompt, ai_config) → str`, but it is **not** part of `plugin.api`. Official plugin path is HTTP chat/playlist endpoints. |
| **Confidence** | high |

### Evidence

- `PLUGIN.md` API reference lists only `plugin.api` (+ `tasks.mediaserver`). No LLM/chat entry.
- Internal (unsupported for plugins): `tasks/ai/api.py` → `generate_text(prompt, ai_config, …) -> str` and `call_with_tools(...)` — routes to Ollama/OpenAI/Gemini/Mistral given an `ai_config` dict (`provider`, `ollama_url`, `ollama_model`, `openai_url`, `openai_key`, …).
- HTTP surface for LLM is playlist-oriented (`POST /chat/api/chatPlaylist`), not “arbitrary prompt → text.”

A plugin *could* privately `from tasks.ai.api import generate_text` and build `ai_config` from `plugin.api.config`, but that is the same unsupported internal-import pattern as vector KNN.

---

## 3. Live config — which provider is configured?

| | |
|---|---|
| **Answer** | **OLLAMA** (model `mistral`, LAN Ollama URL). No API keys in these responses. |
| **Confidence** | high |

### `GET /api/config` (LLM-related fields only)

```json
{
  "ai_model_provider": "OLLAMA",
  "ollama_model_name": "mistral",
  "ollama_server_url": "http://192.168.1.10:11434/api/generate",
  "openai_model_name": "mistral",
  "openai_server_url": "http://localhost:11434/api/generate",
  "gemini_model_name": "gemini-2.5-pro",
  "mistral_model_name": "ministral-3b-latest"
}
```

Secret-like keys in `/api/config`: **none**.

### `GET /chat/api/config_defaults`

```json
{
  "default_ai_provider": "OLLAMA",
  "default_ollama_model_name": "mistral",
  "ollama_server_url": "http://192.168.1.10:11434/api/generate",
  "default_openai_model_name": "mistral",
  "openai_server_url": "http://localhost:11434/api/generate",
  "default_gemini_model_name": "gemini-2.5-pro",
  "default_mistral_model_name": "ministral-3b-latest"
}
```

Provider revealed: **`OLLAMA`**.

---

## 4. `POST /chat/api/chatPlaylist` contract

| | |
|---|---|
| **Answer** | Accepts NL `userInput` (+ optional provider overrides). Returns tracks in `response.query_results` **and** free-text/log in `response.message` (plus SQL/meta). |
| **Confidence** | high (OpenAPI + plugin usage); no live POST (would run LLM / library tools) |

### Request (OpenAPI; required: `userInput`)

| Field | Role |
|---|---|
| `userInput` | Natural-language playlist request |
| `ai_provider` | Optional: `OLLAMA` \| `OPENAI` \| `GEMINI` \| `MISTRAL` \| `NONE` (defaults to server) |
| `ai_model` | Optional model override |
| `ollama_server_url` / `openai_server_url` | Optional URL overrides |
| `openai_api_key` / `gemini_api_key` / `mistral_api_key` | Optional key overrides |

### Response 200

```json
{
  "response": {
    "original_request": "…",
    "ai_provider_used": "OLLAMA",
    "ai_model_selected": "…",
    "message": "Log of AI interaction and processing.",
    "executed_query": "… or null",
    "query_results": [
      { "item_id": "…", "title": "…", "artist": "…" }
    ]
  }
}
```

- **Tracks:** yes — `query_results[]` with `item_id` / `title` / `artist`.
- **Free text:** yes — `message` is an interaction/processing log (not a clean structured JSON config). `executed_query` may also be present.

Alchemy FM already uses this with **server defaults only** (no plugin LLM settings):

```1527:1556:audiomuse-plugins/alchemy_fm_bridge/__init__.py
def _chat_playlist_tracks(prompt: str, *, limit: int = CHAT_PLAYLIST_LIMIT) -> list[dict[str, Any]]:
    ...
    data = audiomuse_post(
        "/chat/api/chatPlaylist",
        {"userInput": prompt},
    )
    ...
            results = response.get("query_results")
```

---

## SUMMARY

**Can the plugin reuse AudioMuse’s LLM for a general prompt → JSON-config call?**  
**Mostly no (not as a first-class API).**

- **Settings reuse: yes** — `from plugin.api import config` (and/or `GET /api/config` / `GET /chat/api/config_defaults`) so you don’t need separate LLM settings in the plugin.
- **General prompt → text/JSON: no stable helper** on `plugin.api`. Only unsupported `tasks.ai.api.generate_text`, or specialized HTTP `chatPlaylist` (playlist/MCP pipeline, not arbitrary JSON config).

**If no, is the fallback (chatPlaylist + deterministic derivation) viable?**  
**Yes.** Omit provider fields so the server’s OLLAMA config is used; take `query_results` (and optionally parse `message`), then map tracks → station programming deterministically — already how Channel Designer chat preview works.

---

## Related

- Feature feasibility checklist: [`FEATURE_FEASIBILITY_REPORT.md`](FEATURE_FEASIBILITY_REPORT.md)
- Journey / find_path investigation: [`JOURNEY_FIND_PATH_REPORT.md`](JOURNEY_FIND_PATH_REPORT.md)
