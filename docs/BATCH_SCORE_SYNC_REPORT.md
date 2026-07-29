# Batch Tempo / Key / Scale Fetch for Alchemy Backend

**Date:** 2026-07-17  
**Scope:** Read-only (live GET probes). Secrets redacted. No code or state changes.

**Live instance:** `AUDIOMUSE_URL` from `.env` → `http://192.168.1.10:8387`  
**Auth:** Alchemy FM `.env` Bearer token (`Authorization: Bearer ***REDACTED***`)

**Goal:** Fetch per-track analysis (`tempo`, `key`, `scale`) for a batch of ~20 track ids in as few HTTP calls as possible from `backend/app/services/audiomuse.py`.

**Track IDs** from `GET /api/search_tracks?search_query=beatles&end=5`:  
`cGbIFRBWpbC5frXYQLm15L`, `ClNQTMWvnP27oq93wKlOQY`, `S7hst6uKbOEQByYMtAz50C`

---

## 1. `GET /external/get_score` — multi-id?

| | |
|---|---|
| **Answer** | **Strictly one id per call.** Returns a single object, not a list. |
| **Confidence** | high |

| Probe | Result |
|---|---|
| `?id=<one>` | **200** — one score object |
| `?id=a,b,c` (comma) | **404** — treats whole string as one id: `"Score not found for id: a,b,c"` |
| `?id=a&id=b&id=c` (repeated) | **200** — but only **first** id (Flask `request.args.get('id')`) |
| `?ids=a,b,c` | **400** — `"Missing 'id' parameter"` |

OpenAPI: single required query param `id` (string).

### Live single-id sample (`?id=cGbIFRBWpbC5frXYQLm15L`)

```json
{
  "item_id": "cGbIFRBWpbC5frXYQLm15L",
  "title": "All I've Got To Do",
  "author": "Beatles, The",
  "tempo": 117.1875,
  "key": "C#",
  "scale": "minor",
  "energy": 0.13226095,
  "mood_vector": "…",
  "other_features": "…",
  "album": "…",
  "album_artist": "…",
  "year": 1963,
  "rating": null,
  "file_path": "…",
  "search_u": "…"
}
```

**Field names:** `tempo`, `key`, `scale` (plus `item_id`).

---

## 2. Batch endpoint with tempo/key/scale?

| | |
|---|---|
| **Answer** | **Yes — `GET /api/sync?ids=…`** (OpenAPI: comma-separated GUIDs, ≤500). |
| **Confidence** | high |

### Params (from `/apispec_1.json`)

| Param | Notes |
|---|---|
| `ids` | Comma-separated mediaserver GUIDs (≤500) |
| `include_embeddings` | `true` (default) \| `false` — set **`false`** to skip large embedding payloads |
| `limit` | Default 500 |
| `page` / `fields` | Pagination / index mode (not needed for id lookup) |

### Live multi-id sample

`GET /api/sync?ids=<id1>,<id2>,<id3>&include_embeddings=false&limit=50` → **200** (Bearer OK):

```json
{
  "tracks": [
    {
      "id": "cGbIFRBWpbC5frXYQLm15L",
      "title": "All I've Got To Do",
      "artist": "Beatles, The",
      "tempo": 117.1875,
      "key": "C#",
      "scale": "minor",
      "energy": 0.13226095
    },
    {
      "id": "ClNQTMWvnP27oq93wKlOQY",
      "title": "All My Loving",
      "artist": "Beatles, The",
      "tempo": 156.25,
      "key": "C#",
      "scale": "minor",
      "energy": 0.16413157
    },
    {
      "id": "S7hst6uKbOEQByYMtAz50C",
      "title": "Baby's In Black",
      "artist": "Beatles, The",
      "tempo": 104.166664,
      "key": "C#",
      "scale": "minor",
      "energy": 0.16958478
    }
  ],
  "has_more": null,
  "next_page": null,
  "total_tracks": null,
  "provider_type": null
}
```

**Note:** sync uses track id field **`id`**, not `item_id`. Same analysis names: **`tempo`**, **`key`**, **`scale`**.

No other batch score endpoint in OpenAPI besides `/api/sync` + per-id `/external/get_score`.

---

## 3. Does Alchemy FM Bearer authorize the recommended endpoint?

| | |
|---|---|
| **Answer** | **Yes** — live `GET /api/sync?ids=…&include_embeddings=false` with `.env` Bearer → **200**. |
| **Confidence** | high |

Same token also authorizes `/external/get_score` and `/api/search_tracks`.

---

## SUMMARY

**Cheapest way for the backend to batch-fetch tempo/key/scale for ~20 ids:**  
`GET /api/sync?ids=<id1>,<id2>,…&include_embeddings=false&limit=20`  
Fields: `tracks[].id`, `tracks[].tempo`, `tracks[].key`, `tracks[].scale`.

**Calls needed per refill:** **1** (20 ≪ 500 id cap).

---

## Related

- Feature feasibility checklist: [`FEATURE_FEASIBILITY_REPORT.md`](FEATURE_FEASIBILITY_REPORT.md)
- Journey / find_path: [`JOURNEY_FIND_PATH_REPORT.md`](JOURNEY_FIND_PATH_REPORT.md)
- LLM plugin reuse: [`LLM_PLUGIN_REUSE_REPORT.md`](LLM_PLUGIN_REUSE_REPORT.md)
