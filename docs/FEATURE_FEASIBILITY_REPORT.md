# Alchemy FM / AudioMuse Feasibility Report

**Date:** 2026-07-17  
**Scope:** Read-only (repo + live GET probes). Secrets redacted. No code or state changes.

**Live instance:** `AUDIOMUSE_URL` from `.env` → `http://192.168.1.10:8387`  
**Auth:** `Authorization: Bearer ***REDACTED***` (token present; length redacted)

**Features evaluated:**

1. **Harmonic / tempo ordering** — order next batch so adjacent tracks are tempo- and key-compatible (Camelot-style).
2. **Daypart energy curve** — bias pool toward lower/higher energy by hour of day.
3. **Embedding “journeys”** — drift vibe over time via waypoint vectors interpolated through embedding space.

---

## A. AudioMuse API capabilities

### A1. OpenAPI / Swagger?

| | |
|---|---|
| **Answer** | Yes |
| **Confidence** | high |

**Evidence:**

- Live: `GET /apidocs/` → 200 (Swagger UI)
- Live: `GET /apispec_1.json` → 200, OpenAPI 3.0.0, title `AudioMuse-AI API`, **108 paths**
- Non-API HTML login pages (not the API spec): `/docs`, `/openapi.json` → login HTML

**All endpoints from live `/apispec_1.json`:**

| Methods | Path |
|---|---|
| GET | `/` |
| GET | `/alchemy` |
| GET | `/analysis` |
| GET | `/api/active_tasks` |
| POST | `/api/alchemy` |
| POST | `/api/analysis/start` |
| GET, POST | `/api/anchors` |
| DELETE, PUT | `/api/anchors/{anchor_id}` |
| GET | `/api/artist_projections` |
| GET | `/api/artist_tracks` |
| POST | `/api/backup/create` |
| POST | `/api/backup/restore` |
| POST | `/api/build_artist_projection` |
| POST | `/api/cancel/{task_id}` |
| POST | `/api/cancel_all/{task_type_prefix}` |
| POST | `/api/clap/cache/refresh` |
| POST | `/api/clap/search` |
| GET | `/api/clap/stats` |
| GET | `/api/clap/top_queries` |
| POST | `/api/clap/warmup` |
| GET | `/api/clap/warmup/status` |
| POST | `/api/cleaning/start` |
| POST | `/api/clustering/start` |
| GET | `/api/config` |
| GET | `/api/config/defaults` |
| POST | `/api/create_playlist` |
| GET, POST | `/api/cron` |
| GET | `/api/cron/plugin_tasks` |
| GET | `/api/dashboard/summary` |
| GET | `/api/find_path` |
| GET | `/api/health` |
| GET | `/api/last_task` |
| GET | `/api/lyrics/axes` |
| POST | `/api/lyrics/cache/refresh` |
| POST | `/api/lyrics/search/axes` |
| POST | `/api/lyrics/search/text` |
| GET | `/api/lyrics/stats` |
| POST | `/api/lyrics/warmup` |
| GET | `/api/lyrics/warmup/status` |
| GET | `/api/map` |
| GET | `/api/map_cache_status` |
| GET | `/api/max_distance` |
| POST | `/api/migration/*` (dry-run, execute, libraries, match-album, etc.) |
| GET | `/api/mood_centroids` |
| GET | `/api/playlists` |
| GET, POST | `/api/radios` |
| POST | `/api/radios/run` |
| DELETE, PUT | `/api/radios/{radio_id}` |
| POST | `/api/rebuild_map_cache` |
| GET | `/api/search_artists` |
| GET | `/api/search_playlists` |
| GET | `/api/search_tracks` |
| POST | `/api/sem_grove/cache/refresh` |
| POST | `/api/sem_grove/search` |
| GET | `/api/sem_grove/stats` |
| GET, POST | `/api/setup` (+ plex/providers subpaths) |
| GET | `/api/similar_artists` |
| GET | `/api/similar_tracks` |
| GET, POST | `/api/sonic_fingerprint/generate` |
| GET | `/api/status/{task_id}` |
| GET | `/api/sync` |
| GET | `/api/track` |
| GET, POST | `/api/users` |
| DELETE | `/api/users/{user_id}` |
| PUT | `/api/users/{user_id}/password` |
| GET | `/api/waveform` |
| GET | `/artist_similarity` |
| POST | `/auth` |
| GET | `/backup` |
| GET | `/chat/` |
| POST | `/chat/api/chatPlaylist` |
| GET | `/chat/api/config_defaults` |
| POST | `/chat/api/create_playlist` |
| GET | `/clap_search` |
| GET | `/cleaning` |
| GET | `/cron` |
| GET | `/external/get_embedding` |
| GET | `/external/get_score` |
| GET | `/external/search` |
| GET | `/login` |
| POST | `/logout` |
| GET | `/lyrics_search` |
| GET | `/map` |
| GET | `/path` |
| GET | `/provider-migration` |
| GET | `/setup` |
| GET | `/similarity` |
| GET | `/sonic_fingerprint` |
| GET | `/users` |
| GET | `/waveform` |

**Repo already calls (backend + plugin):**  
`/api/similar_tracks`, `/api/alchemy`, `/api/clap/search`, `/api/lyrics/search/text`, `/api/mood_centroids`, `/api/anchors`, `/api/search_tracks`, `/api/search_playlists`, `/api/playlists`, `/api/playlist`, `/api/clustering/start`, `/api/last_task`, `/api/config`, `/chat/api/chatPlaylist`  
(see `backend/app/services/audiomuse.py`, `audiomuse-plugins/alchemy_fm_bridge/__init__.py`)

---

### A2. KNN-by-arbitrary-vector endpoint?

| | |
|---|---|
| **Answer** | No (public HTTP) |
| **Confidence** | high |

**Evidence:**

- OpenAPI: no path accepts a raw query embedding and returns nearest tracks.
- `GET /api/similar_tracks` params: `item_id` \| `title`+`artist` \| mood+centroid_index \| (internal) alchemy anchor — **not** a free vector body.
- `POST /api/sem_grove/search` requires `item_id` seed only.
- `POST /api/clap/search` is **text** query → tracks, not a vector.
- Only vector **write** surface found: `POST /api/anchors` body `{name, centroid: float[]}` (state-changing; not probed).
- Internal core code (upstream) uses `find_nearest_neighbors_by_vector` for mood/anchor modes, but that is **not** exposed as a public “pass any float[]” API.

**Implication for Feature 3:** continuous waypoint interpolation **cannot** be driven from Alchemy backend via a public KNN-vector call today. Closest public relatives: `GET /api/find_path` (discrete endpoints) or create anchors then query (stateful).

---

### A3. Retrieve a track’s raw embedding via API?

| | |
|---|---|
| **Answer** | Yes |
| **Confidence** | high (dim); med (whether it is CLAP vs MusiCNN) |

**Evidence (live GET):**

- `GET /external/get_embedding?id=cGbIFRBWpbC5frXYQLm15L` → 200
- Keys: `item_id`, `embedding`
- **Dimensionality: 200**
- Sample (first 5 only): `[0.4825, -0.6937, 2.6087, -1.2087, 0.1216, …]`
- OpenAPI for `GET /api/find_path` documents `path_space=audio` as **MusiCNN** similarity index (not CLAP). CLAP has separate text-search endpoints; no public “get CLAP vector” endpoint found in the spec.

**Sample response shape:**

```json
{
  "item_id": "cGbIFRBWpbC5frXYQLm15L",
  "embedding": [/* 200 floats */]
}
```

---

### A4. Score / analysis fields for real tracks?

| | |
|---|---|
| **Answer** | Yes — via `GET /external/get_score` (HTTP) and plugin `get_score_data_by_ids` (in-process) |
| **Confidence** | high |

**Track IDs used:**  
`cGbIFRBWpbC5frXYQLm15L`, `ClNQTMWvnP27oq93wKlOQY`, `S7hst6uKbOEQByYMtAz50C`

**Actual field names (live):**  
`item_id`, `title`, `author`, `album`, `album_artist`, `tempo`, `key`, `scale`, `energy`, `mood_vector`, `other_features`, `year`, `rating`, `file_path`, `search_u`

**One real sample:**

```json
{
  "item_id": "cGbIFRBWpbC5frXYQLm15L",
  "title": "All I've Got To Do",
  "author": "Beatles, The",
  "album": "With The Beatles (Remastered)",
  "album_artist": "Beatles, The",
  "tempo": 117.1875,
  "key": "C#",
  "scale": "minor",
  "energy": 0.13226095,
  "mood_vector": "60s:0.573,oldies:0.565,rock:0.563,classic rock:0.539,pop:0.538",
  "other_features": "danceable:0.56,aggressive:0.59,happy:0.63,party:0.62,relaxed:0.51,sad:0.58",
  "year": 1963,
  "rating": null,
  "file_path": "Beatles, The/With The Beatles (Remastered)/01-02 - All I've Got To Do.mp3",
  "search_u": "all i've got to do beatles, the with the beatles (remastered)"
}
```

| Field | Present? | Format / observed range |
|---|---|---|
| tempo / BPM | Yes | float BPM (samples: ~104–156) |
| musical key | Yes | `key` string e.g. `"C#"` + `scale` `"major"`/`"minor"` — **not Camelot**; convertible |
| energy | Yes | float; samples **0.13–0.17** (plugin UI labels 0–1; upstream docs historically ~0–0.15) |
| mood vector | Yes | comma `tag:score` string; also `other_features` mood dims 0–1-ish |
| embedding | Not in score row | Separate: `/external/get_embedding` → 200-float list |

Plugin enrichment uses the same score fields in-process: `_apply_score_metadata` at `audiomuse-plugins/alchemy_fm_bridge/__init__.py:893-905` (`tempo`, `energy`, `mood_vector` / `moods` — **does not currently read `key`/`scale`**).

---

## B. Harmonic ordering (Feature 1)

### B1. Per-track tempo/BPM available?

| | |
|---|---|
| **Answer** | Yes |
| **Confidence** | high |

**Evidence:**

- Live: `/external/get_score` → `tempo` (e.g. `117.1875`)
- Plugin: `get_score_data_by_ids` → `tempo` (`__init__.py:894`, filters `__init__.py:1006-1017`)
- Backend player/pool today: **no** — `StationPoolItem` has only id/title/artist (`database.py:100-110`); refill does not fetch scores

---

### B2. Per-track musical key?

| | |
|---|---|
| **Answer** | Yes (raw key + mode); not Camelot-native |
| **Confidence** | high |

**Evidence:**

- Live score: `"key": "C#"`, `"scale": "minor"`
- Convertible to Camelot: **yes**, given pitch class + major/minor
- Plugin does **not** currently surface/use key for ordering (only tempo/energy/mood/genre)

---

## C. Daypart energy (Feature 2)

### C1. Per-track energy score?

| | |
|---|---|
| **Answer** | Yes |
| **Confidence** | high (availability); med (canonical numeric range) |

**Evidence:**

- Live: `energy` float on score (samples 0.132 / 0.164 / 0.170)
- Plugin UI: “Energy Min/Max **(0–1)**” (`__init__.py:4732-4737`) — **mismatched** vs observed live magnitudes
- Treat as relative ranking within the library unless you calibrate the true global max on this instance

---

## D. Plumbing (plugin-as-brain / thin player)

### D1. Does `refill.py` re-query AudioMuse?

| | |
|---|---|
| **Answer** | Yes — confirmed |
| **Confidence** | high |

**Evidence (`backend/app/services/refill.py`):**

- Tier 0 programming batch → `fetch_programming_batch` → `audiomuse_client.fetch_tracks` (`:220-227`, `:55-61`)
- Tier 1 imported pool (local DB) (`:233-246`)
- Tier 2 alchemy anchor → `audiomuse_client.fetch_tracks(alchemy_anchor, …)` (`:254-266`)
- Tier 3 similar-seed → `audiomuse_client.fetch_similar_tracks` (`:268-280`)
- Tier 4 similar-last → `audiomuse_client.fetch_similar_tracks(last_id, …)` (`:282-296`)

---

### D2. Does `StationPoolItem` store sonic metadata?

| | |
|---|---|
| **Answer** | No — only identity + title/artist |
| **Confidence** | high |

**Evidence (`backend/app/database.py:100-110`):**

```python
class StationPoolItem(Base):
    id: Mapped[int]
    station_id: Mapped[int]
    item_id: Mapped[str]
    title: Mapped[str]
    artist: Mapped[str]
    imported_at: Mapped[datetime]
```

---

### D3. Plugin HTTP endpoints? Backend→plugin today?

| | |
|---|---|
| **Answer** | Plugin exposes HTTP UI/API helpers; **no** backend→plugin runtime programming calls today |
| **Confidence** | high |

**Plugin routes** (`alchemy_fm_bridge/__init__.py`, mounted under `/plugins/alchemy_fm_bridge`):

| Method | Path (under plugin prefix) |
|---|---|
| GET | `/api/search-artists` |
| GET | `/api/search-playlists` |
| GET | `/api/search-tracks` |
| GET | `/api/search-anchors` |
| GET | `/api/search-genres` |
| GET | `/api/search-moods` |
| GET | `/api/verify-bootstrap` |
| POST | `/api/chat-preview` |
| GET/POST | `/` (Channel Designer UI) |
| GET/POST | `/settings` |

Live probe: `GET /plugins/alchemy_fm_bridge/api/search-moods?q=rock` + Bearer → **200**

```json
{"moods":["alternative rock","classic rock","hard rock","indie rock","progressive rock","rock"]}
```

**Direction today:** plugin → Alchemy FM admin API (deploy/bootstrap/refresh). Backend → AudioMuse **core** APIs only (`audiomuse_client`). No “next tracks” plugin endpoint exists yet.

---

### D4. Can Alchemy backend reach plugin endpoints at runtime?

| | |
|---|---|
| **Answer** | Yes on the LAN path used today; same blockers as any AudioMuse reachability |
| **Confidence** | high (LAN); med (public/WAF topologies) |

**Evidence:**

- Backend already reaches AudioMuse at `AUDIOMUSE_URL` (`http://192.168.1.10:8387`) with Bearer token
- Plugin routes answered on that same host/port with the same token
- Docs warn Cloudflare/WAF can block **AudioMuse→Alchemy** admin paths (`docs/TRAEFIK.md:102-118`, plugin settings copy) — reverse direction (Alchemy→AudioMuse) is what refill already does; use LAN URL, not orange-cloud public hostname
- No existing “next tracks” contract; you’d add one on the plugin

---

### D5. Station timezone / daypart fields?

| | |
|---|---|
| **Answer** | No |
| **Confidence** | high |

**Evidence:** `Station` model (`database.py:52-97`) has programming/source/queue/continuation/identity fields only — **no** `timezone`, daypart curve, schedule, or energy-bias columns.

**Would need adding** (or encode in `programming_json`) e.g. `timezone` + daypart energy targets/curve config if Feature 2 is station-owned and hands-off.

---

## SUMMARY

### READY TO BUILD TODAY (no new AudioMuse core work needed)

- **Feature 1 (harmonic/tempo ordering)** — in the **plugin brain**: tempo + key + scale already in score (`/external/get_score` or `get_score_data_by_ids`). Implement Camelot mapping + ordering when assembling the next batch; thin player just plays what the plugin returns.
- **Feature 2 (daypart energy)** — same score `energy` field is available to the plugin. Add timezone/daypart config (Station column or `programming_json`) and bias/filter the pool by energy vs local hour. Calibrate energy scale on this library (observed ~0.1x, UI says 0–1).
- **Plumbing toward thin player** — backend already talks to AudioMuse over LAN; plugin HTTP is reachable the same way. You can add a plugin-owned “next tracks” endpoint without waiting on NeptuneHub, as long as the brain uses score data + existing recommendation APIs.

### BLOCKED / NEEDS AUDIOMUSE CHANGES

- **Feature 3 (embedding journeys via interpolated waypoint vectors)** as specified:
  - **A2 = No** public KNN-by-arbitrary-vector API → cannot query “nearest to this float[]” from outside without hacks.
  - **A3 = Yes** for a **200-D audio embedding** (MusiCNN-class / `embedding` table), **not** confirmed as CLAP space; CLAP is text→search today.
  - Partial public substitute: `GET /api/find_path` (song/mood/anchor endpoints, discrete path) — useful but not continuous custom waypoints.
  - Stateful workaround: write interpolated centroids as alchemy anchors then query — ugly and mutates AudioMuse state.
  - Best path for your architecture: **plugin-owned endpoint** that wraps AudioMuse’s internal `find_nearest_neighbors_by_vector` (or ask upstream for a public vector-KNN). That is still AudioMuse-side work, even if it lives in your plugin rather than core.

### BIGGEST UNKNOWN

Whether Feature 3’s intended space is **MusiCNN 200-D** (what `/external/get_embedding` returns) or true **CLAP** vectors (not exposed for get/KNN). Also the **true energy dynamic range** on this library (UI vs live samples disagree), which affects daypart curve design.
