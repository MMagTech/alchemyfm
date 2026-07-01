# Music Knowledge Enrichment — Design Plan (v1)

**Status:** Implemented (v1 shipped) — this document is the original design reference; behavior may have drifted slightly from the plan. For current setup, see [DEVELOPMENT.md](DEVELOPMENT.md) and admin **Knowledge** when `KNOWLEDGE_FEATURE=true`.

**Default:** OFF via `.env` — **opt-in only**; when OFF, **no UI changes anywhere**  
**Scope v1:** Global admin settings; per-station toggle deferred  

---

## Opt-in invariant (non-negotiable)

### Layer 1 — `.env` deploy gate (UI + code paths)

| `KNOWLEDGE_FEATURE` | Behavior |
|---------------------|----------|
| `false` or unset (default) | App **identical to today**. No Knowledge nav link, no admin Knowledge section, no station “Did you know?” panel, worker not started, `knowledge.db` not touched |
| `true` | Knowledge feature **available** — admin header shows **Knowledge** link; station page may show fact panel when admin runtime toggle is ON and cache exists |

Requires **container restart** to show/hide (standard `.env` change). No half-enabled UI.

```env
# .env.example — default off
KNOWLEDGE_FEATURE=false
KNOWLEDGE_DATABASE_URL=sqlite:////data/knowledge.db
SEARXNG_URL=
OLLAMA_URL=
OLLAMA_MODEL=
```

### Layer 2 — Admin mode (only when `KNOWLEDGE_FEATURE=true`)

Three modes on the **Knowledge** admin page (stored in `knowledge.db` as `mode`):

| Mode | Processing (SearXNG/Ollama) | Station “Did you know?” | Use when |
|------|----------------------------|-------------------------|----------|
| **`off`** | None | Hidden — no `knowledge` in API | Completely off without redeploy; same listener experience as no facts |
| **`cached_only`** | None — read cache only | Shows facts for tracks already in `knowledge.db` | Pause GPU/search; keep trivia on air for enriched tracks |
| **`active`** | Lookahead + background jobs | Shows cached facts (incl. newly enriched) | Normal operation |

```text
KNOWLEDGE_FEATURE=false  →  feature absent (Layer 1 — app as today)

KNOWLEDGE_FEATURE=true   →  Knowledge nav + admin page, pick mode:
                           off | cached_only | active
```

**Default when feature first enabled:** `off` (admin must explicitly choose `cached_only` or `active`).

**Worker:** Only runs in `active`. Scheduler no-ops in `off` and `cached_only`.

**Station API:** Returns `knowledge` only in `cached_only` and `active` when a non-expired cache hit exists; omits field in `off`.

**Enabling for real:** `KNOWLEDGE_FEATURE=true` in `.env` → restart → configure providers → Knowledge admin → set mode to `active` (or `cached_only` if cache already populated).

---

## Goals

Enrich upcoming tracks with **factual, music-focused** tidbits (song, artist, album, producer, samples) using **local-only** search + LLM, cached with **TTL**, with **zero impact** on playback or queue throughput.

## Non-goals (v1)

- Cloud AI, API keys, token billing
- Fullscreen MilkDrop overlay (possible v2)
- Per-station enable switch
- Generating knowledge when a track **starts** playing
- Blocking Liquidsoap, queue refill, or API responses on enrichment

---

## User decisions (locked for v1)

| Topic | Decision |
|-------|----------|
| **Listener UI** | **“Did you know?”** panel on station page — **below now playing, above strip visualizer** |
| **Multiple facts** | **Rotate** through up to 3 cached facts every **~15s** while the same track is on air |
| **Sources** | **One link** for the **currently displayed** fact only; `target="_blank"` / new tab |
| **Deploy gate** | **`KNOWLEDGE_FEATURE=false` in `.env` (default)** — when false, zero UI changes vs today |
| **Admin nav** | When gate ON: **Knowledge** link in header → dedicated admin page |
| **Runtime mode** | Three-way: **`off`** · **`cached_only`** · **`active`** (default `off` until admin chooses) |
| **Infrastructure** | SearXNG + Ollama are **optional**. App must run normally when feature is OFF or when URLs are unset |
| **Navidrome metadata** | **Yes** — use **album + year** from Navidrome when building SearXNG queries (extend `getSong` / track metadata for knowledge worker) |
| **Database** | **Separate SQLite file** — `/data/knowledge.db` (not `radio.db`) from day one |
| **Persistence** | **TTL enabled** — entries expire and may be re-enriched; admin can wipe cache to start over |

---

## High-level architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Queue refill / bootstrap (existing, unchanged)              │
│       │                                                      │
│       └──► schedule lookahead scan (non-blocking, ~0ms)      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Knowledge scheduler (backend, asyncio)                      │
│  • If global OFF → no-op                                     │
│  • If SearXNG/Ollama unreachable → log, skip, retry later    │
│  • Peek next 5–10 queued tracks per enabled station         │
│  • Skip item_ids already cached (ready or explicit no-fact)  │
│  • Enqueue jobs in knowledge DB `knowledge_jobs` table         │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Knowledge worker (same process, low concurrency)            │
│  1. Navidrome metadata (title, artist, album, year)          │
│  2. SearXNG music-focused queries → snippets + URLs          │
│  3. Ollama → structured JSON facts + confidence              │
│  4. Validate / filter categories                             │
│  5. Write `track_knowledge` cache (TTL) in `knowledge.db`      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Station API + station.html                                  │
│  • Attach cached fact(s) to now_playing when available         │
│  • Never call SearXNG/Ollama on listener requests             │
└─────────────────────────────────────────────────────────────┘
```

**Why one backend worker (v1):** Same FastAPI process, but **two SQLite files** — knowledge I/O isolated from `radio.db`. Revisit separate container only if Ollama jobs become very heavy.

**Cross-database link:** `item_id` (Navidrome) only — **no SQL foreign keys** between `radio.db` and `knowledge.db`. Station API: read queue from `radio.db`, lookup facts from `knowledge.db` by `item_id`.

---

## Trigger: when to schedule jobs

| Hook | Behavior |
|------|----------|
| After `extend_queue()` commits | Fire-and-forget `schedule_knowledge_lookahead(station_id)` |
| After `bootstrap_station()` | Same |
| Existing `_queue_refresh_loop` (~30s) | Lightweight scan: any station with enrichment ON and pending lookahead |

**Lookahead window:** next **8** queued tracks (configurable 5–10 via admin/env).

**Lead time rule:** Only enqueue tracks that are still `queued` (not `playing`). Worker may still finish while a track is playing — that is OK; we never *start* a job because a track *just went on air*.

**Dedup:** Cache key = Navidrome `item_id`. Skip enqueue if a **non-expired** cache row exists for that `item_id`.

**Expiry sweep:** Lightweight job (e.g. daily or on worker idle) deletes rows where `expires_at < now()` so bad or stale data does not linger until TTL wall clock on read path only.

---

## Optional providers (not required for app)

| Setting | Example | When `KNOWLEDGE_FEATURE=false` |
|---------|---------|-------------------------------|
| `KNOWLEDGE_FEATURE` | `false` | **Default** — feature absent from UI |
| `KNOWLEDGE_DATABASE_URL` | `sqlite:////data/knowledge.db` | Unused |
| `SEARXNG_URL` | `http://host.docker.internal:8081` | Unused |
| `OLLAMA_URL` | `http://host.docker.internal:11434` | Unused |
| `OLLAMA_MODEL` | `llama3.2:3b` | Unused |

When `KNOWLEDGE_FEATURE=true` but URLs fail health check:

- Admin shows **degraded** status (“Ollama unreachable”)
- Queue/streaming continues unaffected
- Jobs retry with backoff

**Do not** add SearXNG/Ollama to `docker-compose.yml` as required services. Document optional compose snippets in README for users who want them.

---

## Data model (separate SQLite: `/data/knowledge.db`)

**Not stored in `radio.db`.** Same Docker volume (`radio-data`), separate file — easy to backup, delete, or reset without touching stations/queues.

Env: `KNOWLEDGE_DATABASE_URL=sqlite:////data/knowledge.db` (default)

### `knowledge_settings` (singleton row, id=1)

| Column | Notes |
|--------|-------|
| `mode` | `off` \| `cached_only` \| `active` — default **`off`** |
| `cache_ttl_days` | int, default **`180`** — days until cache row expires (admin range 30–365; `0` = never expire, not recommended) |
| `lookahead_count` | int, default `8` |
| `max_concurrent_jobs` | int, default `1` (protect Ollama) |
| `max_facts_per_track` | int, default `3` — how many facts Ollama may generate/store (cap 1–5) |
| `max_fact_chars` | int, default `200` — max characters per fact text (cap 80–320) |
| `display_fact_count` | int, default `3` — how many facts the station UI may rotate through (≤ stored) |
| `rotation_interval_sec` | int, default `15` — on-air rotation cadence (8–60) |
| `min_confidence` | float, default `0.6` — facts below this are not stored or shown |
| `searxng_url` | optional override of env |
| `ollama_url` | optional override of env |
| `ollama_model` | optional override of env |

### `track_knowledge` (TTL cache)

| Column | Notes |
|--------|-------|
| `item_id` | PK, Navidrome track id |
| `status` | `ready` \| `no_facts` \| `failed` |
| `title`, `artist`, `album`, `year` | snapshot at generation time |
| `payload_json` | structured facts (see schema below) |
| `created_at`, `updated_at` | |
| `expires_at` | `created_at + cache_ttl_days` (stored explicitly for sweeps) |
| `failure_reason` | nullable, for admin/debug |

**TTL applies to all statuses** (`ready`, `no_facts`, `failed`) so poor results are not locked in forever. After expiry, lookahead may enqueue a fresh job.

`no_facts` is still a valid outcome — it simply expires like everything else.

---

## Where facts live

| Layer | Location | What |
|-------|----------|------|
| **Cache** | `track_knowledge` in **`/data/knowledge.db`** | Full JSON payload per Navidrome `item_id` |
| **Job queue** | `knowledge_jobs` in **`knowledge.db`** | In-flight / pending work |
| **Settings** | `knowledge_settings` in **`knowledge.db`** | Global toggles, TTL, limits |
| **Radio core** | `/data/radio.db` | Stations, queues, pools — **unchanged, no fact rows** |
| **Docker volume** | `radio-data` → `/data` | Both `.db` files + stations dirs + `queue.m3u` |
| **Not stored** | Browser, Icecast, Liquidsoap | Facts joined at API read time |

Facts are keyed by **`item_id`**, not station. Same recording on multiple stations shares one cache entry.

**On disk size (rough):** ~1–3 KB per track → 10,000 tracks ≈ 10–30 MB in `knowledge.db` alone.

---

## Persistence — TTL & starting over

### TTL (from v1)

| Setting | Default | Behavior |
|---------|---------|----------|
| `cache_ttl_days` | **180** | Each row gets `expires_at = created_at + TTL` |
| After expiry | — | Row ignored for display; lookahead may re-enrich; sweeper deletes expired rows |

**Why TTL + separate DB:** Lets you recover from bad Ollama prompts, wrong SearXNG results, or model swaps without manual DB surgery. Avoids “locked in forever” `no_facts` or low-quality facts.

**Changing TTL in admin:** Affects **new** rows only unless admin runs **Re-enrich expired** or **Purge cache** (optional batch — v1 can be purge-only).

### Starting over (inaccurate facts)

| Method | Effect |
|--------|--------|
| **Admin → Clear knowledge cache** | Deletes all `track_knowledge` + pending `knowledge_jobs`; keeps settings & providers |
| **Admin → Refresh one track** | Deletes that `item_id`; re-queues on next lookahead |
| **Nuclear reset** | Stop backend, delete `/data/knowledge.db`, restart — recreates empty knowledge DB; **radio stations unaffected** |
| **Wait for TTL** | Expired rows fall out automatically; worker refills over time |

Document all three in Admin Help. Nuclear option is the main benefit of a separate file.

### Backup

| File | Contents |
|------|----------|
| `radio.db` | Stations, queues, broadcast settings |
| `knowledge.db` | Facts, jobs, knowledge settings |

Include **both** in `radio-data` tarball backups. Restoring only `radio.db` leaves old facts; deleting only `knowledge.db` resets trivia only.

---

## Configurable fact count & length?

**Yes — with admin caps** (global settings, OFF by default).

Split **generation** vs **display** so you can cache more than you show, or keep them aligned:

| Setting | Controls | Default | Cap |
|---------|----------|---------|-----|
| `max_facts_per_track` | Ollama: how many facts to extract and store | 3 | 1–5 |
| `max_fact_chars` | Ollama + validator: max length per fact sentence | 200 | 80–320 |
| `display_fact_count` | Station UI: how many facts to rotate | 3 | 1–5, ≤ stored |
| `rotation_interval_sec` | Station UI: seconds between rotations | 15 | 8–60 |
| `min_confidence` | Drop weak facts before store/show | 0.6 | 0.5–0.9 |

**Why cap:** Uncapped “10 facts × 500 chars” blows up Ollama runtime, SQLite rows, and the station panel. Caps keep local inference predictable.

**Changing settings after cache exists:** Existing `track_knowledge` rows keep old text until **Refresh** or **Clear cache**. New jobs use current limits. Optional v2: “Re-generate all” batch (heavy).

---

### `knowledge_jobs` (queue)

| Column | Notes |
|--------|-------|
| `id` | PK |
| `item_id` | unique active job per item |
| `station_id` | who triggered lookahead |
| `status` | `pending` \| `running` \| `done` \| `failed` |
| `attempts` | retry counter |
| `scheduled_at`, `started_at`, `finished_at` | |

---

## JSON payload schema (internal + API)

```json
{
  "version": 1,
  "track": {
    "item_id": "abc123",
    "title": "Bring It Back",
    "artist": "Lil' Wayne",
    "album": "Tha Carter",
    "year": 2004
  },
  "facts": [
    {
      "category": "song_fact",
      "text": "The track was released as a single in 2004.",
      "confidence": 0.85,
      "sources": [
        { "url": "https://en.wikipedia.org/...", "title": "Bring It Back (Lil Wayne song)" }
      ]
    }
  ],
  "rejected_count": 0,
  "providers": {
    "search": "searxng",
    "llm": "ollama",
    "model": "llama3.2:3b"
  },
  "generated_at": "2026-06-30T12:00:00Z"
}
```

**Categories (allowlist):** `song_fact`, `artist_fact`, `album_fact`, `producer_fact`, `sample_fact`

**Rejected by policy (never store):** gossip, politics, criminal history, personal life, adult content, scandals, speculation — enforced in prompt + post-validation keywords/heuristics.

**Confidence:** LLM assigns 0–1; API only returns facts with `confidence >= 0.6` to listeners (threshold configurable in admin later; hardcode 0.6 for v1).

**Empty facts:** If after search+summarize nothing passes → `status: no_facts`, `facts: []`. **Never invent.**

---

## SearXNG strategy

Navidrome **`getSong`** (or a knowledge-specific metadata fetch) will supply **album** and **year** when available. Queries prefer `artist + title + album + year` to disambiguate reissues, covers, and duplicate titles.

Music-biased queries (2–3 per track, not 10):

1. `"{artist}" "{title}" "{album}"` (+ year in query when known)
2. `"{artist}" "{title}" song facts`
3. `"{artist}" "{album}" album` (if album known)
4. `"{title}" "{artist}" sample interpolation` (only if first pass hints samples)

**Engines:** Prefer Wikipedia, Discogs, MusicBrainz, official label pages. Configurable denylist (TMZ, Reddit drama, etc.) via SearXNG instance config — document for admin, not coded in v1.

**Input to Ollama:** Only search result **title, snippet, URL** — no raw HTML fetch (keeps scope small, reduces junk).

---

## Ollama strategy

- **Structured output:** JSON mode if model supports it; else strict prompt + `json.loads` with repair fallback
- **System prompt highlights:**
  - Only use provided search snippets
  - If unsure, omit fact
  - No biographical drama, legal trouble, relationships
  - Max 3 facts per track for v1
- **Timeouts:** 60–120s per job; failure → retry up to 3 times, then `failed`

---

## API changes (read-only for listeners)

### Extend `GET /api/stations/{slug}`

```json
"now_playing": {
  "title": "...",
  "artist": "...",
  "item_id": "...",
  "cover_url": "...",
  "knowledge": {
    "status": "ready",
    "facts": [
      {
        "category": "song_fact",
        "text": "...",
        "confidence": 0.85,
        "sources": [{ "url": "...", "title": "..." }]
      }
    ]
  }
}
```

- Client rotates through `facts` (max 3 returned, highest confidence first)
- `knowledge` omitted or `null` when feature OFF, uncached, or `no_facts`

### Admin

| Endpoint | Purpose |
|----------|---------|
| `GET /api/admin/knowledge` | Settings + provider health + queue stats |
| `PUT /api/admin/knowledge` | Enable/disable, lookahead, URLs |
| `POST /api/admin/knowledge/purge` | **Clear all cache** + cancel pending jobs (start over) |
| `POST /api/admin/knowledge/refresh/{item_id}` | Force re-enrich one track |
| `GET /api/admin/knowledge/cache` | Paginated cache browse (debug) |

---

## Station page UI (v1)

New panel **“Did you know?”** in the tune-in layout:

- **Position:** below now playing / track title, **above** the strip visualizer
- **Rotation:** up to **3** cached facts per track; advance every **~15s** while the same track is on air
- **Source:** one link for the **current** rotated fact only; opens in a **new browser tab**
- If no facts cached: panel **hidden** (no loading state for listeners)
- Poll reads API cache only — never triggers SearXNG/Ollama from the browser

**Fullscreen / MilkDrop:** out of scope v1; cache API ready for later overlay.

---

## Admin UI (v1)

**Header nav (admin.html only):** when `KNOWLEDGE_FEATURE=true`, show **Knowledge** link beside Help → dedicated `/admin/knowledge.html` or in-page section.

**Knowledge admin page** (not visible when env gate OFF):

- **Mode** selector: Off · Cached only · Active (segmented control or radio group)
- **Cache TTL (days)** — default 180
- Lookahead count (5–10)
- Fact count / max length / rotation (see configurable section)
- Ollama model name (text field)
- Test connection buttons (SearXNG + Ollama ping)
- **Clear knowledge cache** (start over)
- Status: jobs pending / cache entries / expired count / last error

---

## Safety & quality guardrails

1. **Two-stage filter:** LLM instructions + server-side category allowlist
2. **Source required:** Every fact must have ≥1 URL from search results
3. **Snippet-only grounding:** No “common knowledge” without a cited snippet
4. **Rate limits:** `max_concurrent_jobs=1`, max N new jobs per station per hour
5. **Idempotent cache:** Respect `expires_at`; expired rows eligible for re-enrichment
6. **Admin purge / refresh** for immediate start-over

---

## Integration points (existing code)

| File | Change (when built) |
|------|---------------------|
| `queue.py` | After `extend_queue` / bootstrap → `schedule_knowledge_lookahead()` |
| `main.py` | Start/stop knowledge worker in lifespan |
| `knowledge/database.py` | **Separate** SQLAlchemy engine + models for `knowledge.db` |
| `database.py` | **No** knowledge tables in `radio.db` |
| `stations.py` | Lookup facts from `knowledge.db` by `item_id` when building `now_playing` |
| `station.html` + `style.css` | Knowledge panel |
| `admin.html` | Settings section |

**No changes** to Liquidsoap, Icecast, or `queue.m3u` logic.

---

## Phased delivery

| Phase | Deliverable |
|-------|-------------|
| **1** | `knowledge.db` schema, settings, worker skeleton, OFF-by-default |
| **2** | SearXNG + Ollama clients, cache write path |
| **3** | Lookahead scheduler hooked to queue |
| **4** | API + station page panel |
| **5** | Admin UI + health checks |
| **6** | Prompt tuning, category filters, docs |

---

## Open questions (resolved)

| Question | Decision |
|----------|----------|
| Panel placement | Below now playing, above visualizer |
| Multiple facts | Rotate up to 3, ~15s interval |
| Sources | One link per visible fact, new tab |
| Album + year from Navidrome | **Yes** — use for search queries and cache snapshot |
| Separate database from day one | **Yes** — `knowledge.db` |
| TTL | **Yes** — default 180 days, configurable |
| Start over | Admin purge + optional delete `knowledge.db` |
| Failed jobs for listeners | Hidden; admin shows stats only |

---

## Risks

| Risk | Mitigation |
|------|------------|
| Ollama slow / GPU busy | Low concurrency, lookahead lead time, TTL avoids infinite bad cache |
| Hallucinations | Snippet-only + confidence threshold + `no_facts` + TTL/purge |
| SearXNG junk results | Music-focused queries + instance curation + purge |
| Two SQLite files | Separate files reduce lock contention vs one bloated `radio.db`; no cross-DB FKs |
| Same track, wrong metadata on Icecast | Cache by `item_id`, not title string |
| Migrating knowledge later | **N/A** — already isolated in `knowledge.db` |

---

*Backup before implementation: `audiomuse-radio-backup-2026-06-30`*
