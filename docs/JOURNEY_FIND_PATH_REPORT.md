# Journey / find_path Investigation

**Date:** 2026-07-17  
**Scope:** Read-only (repo + live GET probes). Secrets redacted. No code or state changes.

**Live instance:** `AUDIOMUSE_URL` from `.env` → `http://192.168.1.10:8387`  
**Auth:** `Authorization: Bearer ***REDACTED***`

**Goal:** Find the cheapest way to get an ordered sequence of tracks that walks from A to B through AudioMuse's similarity space (a “journey” station whose vibe drifts from start toward destination).

---

## 1. `GET /api/find_path`

| | |
|---|---|
| **Answer** | Yes — it returns an **ordered** list of tracks from A → B in similarity space |
| **Confidence** | high |

### Contract (live `/apispec_1.json`)

**Summary:** “Find a path of similar songs between two endpoints.”

**Description:** Each endpoint can be a **song id**, a **mood label**, or an **alchemy anchor**. Only **one** endpoint may be mood/anchor at a time (the other must be a song id). Mood/anchor endpoints are resolved to the nearest real song by walking from the other endpoint toward the centroid by `mood_pct`.

| Param | Required? | Type | Notes |
|---|---|---|---|
| `start_song_id` | one of start* | string | Song id for start |
| `end_song_id` | one of end* | string | Song id for end |
| `start_mood` | optional | enum: `happy`, `sad`, `aggressive`, `relaxed`, `danceable` | |
| `end_mood` | optional | same enum | |
| `start_anchor` | optional | integer | Alchemy anchor id |
| `end_anchor` | optional | integer | |
| `mood_pct` | optional | int, default `100` | 0–100; how far toward mood/anchor centroid |
| `max_steps` | optional | int | Max intermediate songs (config default if omitted) |
| `path_fix_size` | optional | bool | Force path to exactly `max_steps` |
| `path_space` | optional | `audio` (default) \| `lyrics` | `audio` = **MusiCNN** index; `lyrics` = SemGrove lyrics+audio (both ends need lyrics+audio analysis) |

**Response 200 shape (OpenAPI):**

```json
{
  "path": [ { /* track objects */ } ],
  "total_distance": 0.0
}
```

Errors: 400 (bad combo / identical ends), 404 (unresolvable / no path), 500.

### Live call

IDs from `GET /api/search_tracks?search_query=beatles&end=5`:

- Start: `cGbIFRBWpbC5frXYQLm15L` — All I've Got To Do (Beatles)
- End: `ClNQTMWvnP27oq93wKlOQY` — All My Loving (Beatles)

`GET /api/find_path?start_song_id=…&end_song_id=…&max_steps=8&path_space=audio` → **200**

```json
{
  "total_distance": 0.9600853407149126,
  "path": [
    {
      "item_id": "cGbIFRBWpbC5frXYQLm15L",
      "title": "All I've Got To Do",
      "author": "Beatles, The",
      "tempo": 117.1875,
      "key": "C#",
      "scale": "minor",
      "energy": 0.13226095,
      "embedding_vector": ["/* 200 floats redacted */"],
      "album": "With The Beatles (Remastered)",
      "mood_vector": "60s:0.573,oldies:0.565,rock:0.563,…"
    },
    {
      "item_id": "wsDluorLGmcE585ZcIlTjy",
      "title": "It's Only Love",
      "author": "Beatles, The"
    },
    {
      "item_id": "xqoR4XbTvTJ1WBEh4wl5Ld",
      "title": "You Just May Be the One (TV Version)",
      "author": "Monkees, The"
    },
    {
      "item_id": "iaMlBaP0NkR1Bdl0oxuBNM",
      "title": "I'm a Believer",
      "author": "Monkees, The"
    },
    {
      "item_id": "XC2ASKPXGTdMvgGgp6l1IA",
      "title": "Daydream Believer",
      "author": "Monkees, The"
    },
    {
      "item_id": "08vOR0RKhEWwEcRpens6iW",
      "title": "Help Me, Rhonda",
      "author": "Beach Boys, The"
    },
    {
      "item_id": "6lzSe4AjWiG3bm04qQ0JMv",
      "title": "Migration (Album Version)",
      "author": "Jimmy Buffett"
    },
    {
      "item_id": "ClNQTMWvnP27oq93wKlOQY",
      "title": "All My Loving",
      "author": "Beatles, The",
      "tempo": 156.25,
      "key": "C#",
      "scale": "minor",
      "energy": 0.16413157
    }
  ]
}
```

**Ordered path?** Yes — `path[0]` is start, `path[-1]` is end, intermediates bridge them. Each step also includes score fields + a 200-d `embedding_vector`.

**More dramatic sample** (Beatles → Metallica “Don't Tread on Me”, `max_steps=10`): `total_distance` ≈ `1.12`, 10 steps:

0. Beatles — All I've Got To Do  
1. Beatles — All My Loving  
2. Beatles — Ticket To Ride  
3. Beach Boys — I Can Hear Music  
4. Warrant — Heaven  
5. Queen — I Want It All  
6. Bon Jovi — Bad Medicine  
7. Stone Temple Pilots — Plush  
8. Extreme — Get The Funk Out  
9. Metallica — Don't Tread on Me  

---

## 2. Can the plugin call `find_nearest_neighbors_by_vector` in-process?

| | |
|---|---|
| **Answer** | **Not via the stable plugin API.** Undocumented internal import may work but is unsupported |
| **Confidence** | high (official surface); med (whether a raw `tasks.*` import succeeds at runtime) |

### Evidence — sanctioned API

Upstream `plugin/api.py` docstring:

> “The **only** module a plugin should import from… Keeps plugins from reaching into app internals.”

`__all__` exports: `get_db`, `get_score_data_by_ids`, `get_tracks_by_ids`, settings/table/enqueue/logger/config, server helpers — **no** `find_nearest_neighbors_by_vector`.

`docs/PLUGIN.md` API reference:

> “Everything below comes from `plugin.api`. The **one exception** is the media-server helper… `from tasks.mediaserver import …`.”

Official vector-adjacent helpers:

| Import | What you get |
|---|---|
| `from plugin.api import get_score_data_by_ids` | tempo/key/energy/mood — **no** vector KNN |
| `from plugin.api import get_tracks_by_ids` | score details **+ analysis embedding** — still **no** KNN |
| `ctx.on_song_analyzed` | `musicnn_embedding` / `clap_embedding` numpy arrays **at analysis time only** |

**Where the function actually lives (core, not plugin API):**  
`from tasks.voyager_manager import find_nearest_neighbors_by_vector` (also used from path/IVF code).

Alchemy FM’s plugin only imports `plugin.api` today (`audiomuse-plugins/alchemy_fm_bridge/__init__.py:17-26`).

**Practical take:** Plugins have full DB/app permissions, so a private import *might* run, but it is **not** an allowed/stable import path. Prefer HTTP `find_path` / `similar_tracks`, or ask upstream to expose vector-KNN on `plugin.api`.

---

## 3. Embedding space: MusiCNN vs CLAP

| | |
|---|---|
| **Answer** | `/external/get_embedding` = **MusiCNN 200-d**. CLAP is **512-d**, readable via sync (and analysis hooks), but **no CLAP nearest-neighbor API** |
| **Confidence** | high |

### Evidence

| Source | Dim | Space |
|---|---|---|
| Live `GET /api/clap/stats` | `"embedding_dimension": 512`, `song_count: 26723`, `loaded: true` | CLAP |
| Live `GET /external/get_embedding?id=…` | **200** floats | MusiCNN (audio index) |
| Live `GET /api/sync?ids=…&include_embeddings=true` | `embedding` → 800 bytes = **200×float32**; `clap_embedding` → 2048 bytes = **512×float32** | both, as base64 |
| OpenAPI `path_space=audio` | documents **MusiCNN** similarity index | |
| `find_path` step `embedding_vector` | dim **200** | same as MusiCNN |

### Get CLAP vector?

- **HTTP:** yes, via `GET /api/sync?ids=…&include_embeddings=true` → `clap_embedding` (base64 float32×512). **Not** via `/external/get_embedding`.
- **In-process:** `on_song_analyzed` payload includes `clap_embedding` at analysis time; **not** listed as a general “get CLAP by id” on `plugin.api`.

### Query nearest neighbors in CLAP space?

- **No** public endpoint. `POST /api/clap/search` is **text → tracks** only.
- No OpenAPI path for “CLAP vector → KNN”.
- MusiCNN KNN exists as song/mood/anchor/`find_path`, not as free float[] input.

---

## 4. `GET /api/radios` and `POST /api/radios/run`

| | |
|---|---|
| **Answer** | Radios = **saved Alchemy-anchor playlist generators**, not live drifting stations |
| **Confidence** | high |

### Contract

- `GET /api/radios` → `{ "radios": [ { id, anchor_id, name, enabled, n_results, temperature } ] }`
- `POST /api/radios` — create (state-changing; not called): requires `anchor_id`, `temperature`, `n_results`
- `PUT` / `DELETE /api/radios/{radio_id}` — update/delete
- `POST /api/radios/run` → `{ playlists_created, radios_enabled, failed[], message }` — **batch-creates media-server playlists** for all enabled radios

### Live `GET /api/radios` sample

```json
{
  "radios": [
    {"id": 2, "anchor_id": 2, "name": "Hip-Hop", "enabled": true, "n_results": 200, "temperature": 0.7},
    {"id": 3, "anchor_id": 3, "name": "Melodic", "enabled": true, "n_results": 100, "temperature": 0.8},
    {"id": 4, "anchor_id": 4, "name": "Upbeat", "enabled": true, "n_results": 200, "temperature": 0.8},
    {"id": 1, "anchor_id": 1, "name": "Yacht Rock", "enabled": true, "n_results": 200, "temperature": 0.7}
  ]
}
```

(`POST /api/radios/run` not executed — state-changing.)

### Could a radio be a continuously-refreshing journey source?

Poor fit. It samples around a **fixed** alchemy anchor with temperature; `run` dumps a playlist once. No A→B path, no time-progress, no ordered journey. Alchemy FM already has richer live refill via clap/lyrics/mood/`similar_tracks`.

---

## SUMMARY

**Cheapest viable path to a drifting journey station:**  
Use **`GET /api/find_path`** with `start_song_id` + `end_song_id` (and `max_steps` / optional `path_space=audio`). It already returns an ordered MusiCNN-space walk from A to B with full track metadata. Wire that in the Channel Designer plugin (or thin Alchemy refill): pick start/end once, call `find_path`, queue the ordered `path` (optionally re-call with a later “current” song as start toward a fixed destination for longer journeys). No custom vector math and no new AudioMuse core API required for song→song journeys.

**Requires upstream AudioMuse changes:** **No** for MusiCNN A→B ordered journeys via `find_path`. **Yes** if you need (a) KNN on arbitrary interpolated vectors, (b) journeys in **CLAP** space, or (c) a stable in-process `find_nearest_neighbors_by_vector` on `plugin.api` — none of those are exposed today.

---

## Related

- Broader feature checklist (harmonic ordering, daypart, embedding journeys): [`FEATURE_FEASIBILITY_REPORT.md`](FEATURE_FEASIBILITY_REPORT.md)
