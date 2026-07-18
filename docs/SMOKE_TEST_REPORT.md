# Alchemy FM Smoke Test Report — 2026-07-18

Multi-station, multi-listener smoke test of the Channel Designer plugin and the
Alchemy FM backend, run against the **live** instances. Observation only — no code
was changed. This report is the as-found baseline for any later fix pass.

## Environment

| Item | Value |
|---|---|
| Plugin | `alchemy_fm_bridge` **4.1.2** (installed + latest in catalog, load_status ok) |
| Backend | `:test` image, git_sha `7b7f02ba9b5766cd2bded7088ad6e578f924964e` (knowledge branch; 3 plugin-only commits behind HEAD), started 2026-07-18 13:48 UTC |
| AudioMuse | `http://192.168.1.10:8387`, LLM = OPENAI / gpt-4o-mini |
| Backend URL | `http://192.168.1.10:9246`, Icecast internal :8000, `knowledge_feature: true` |
| Broadcast settings | mp3 192 kbps, crossfade 4 s, max_listeners 50 |
| Starting state | 21 stations, all on air, `source_last_error` empty everywhere; 26 saved channel designs |
| Backup taken first | `~/alchemyfm-backups/pre-smoketest-backup-2026-07-18.json` (26 channels + pools, 63 KB) |

**What was created:** 10 disposable stations (`zz-test-*`) covering the full
programming/option matrix, deployed through the plugin's real form-POST path
(`afm_action=push`) — the same code path the browser uses, not raw backend calls.

## Coverage table

| Matrix item | Result | Evidence |
|---|---|---|
| clap_query | **PASS** | `zz-test-clap-a`, `zz-test-clap-b` deployed, on air, queue 15, no source error |
| lyrics_query | **PASS** | `zz-test-lyrics`; head tracks visibly on-theme ("Home Life", 2× different "Home") |
| mood_centroid | **PASS** | `zz-test-mood` (aggressive / cluster 0) |
| alchemy_anchor | **PASS** | `zz-test-anchor` (anchor 4 "Upbeat") |
| similar_seed | **PASS** | `zz-test-seed` (seed = Hot Girls in Love — Loverboy) |
| journey song→mood | **PASS** | `zz-test-journey-sm`, end_mood `relaxed`, mood_pct 100 |
| journey song→song | **PASS** | `zz-test-journey-ss`, mood_pct 50 |
| journey song→anchor | **PASS** | `zz-test-journey-sa`, end anchor "Yacht Rock", mood_pct 0 |
| journey anchor→song | **PASS** | `zz-test-journey-as`, start anchor "Aggressive", mood_pct 75 |
| journey anchor→anchor rejected | **PASS (correctly rejected)** | "A journey needs at least one end to be a track." — expected behaviour |
| journey invalid mood rejected | **PASS (bonus)** | Raw POST with `end_mood=chill` rejected: "Invalid mood 'chill'. Valid: aggressive, danceable, happy, relaxed, sad". UI select only offers the valid 5 |
| mood_pct variation | **PASS** | 0 / 50 / 75 / 100 all deployed |
| refresh: similar_to_last | **PASS (deploy layer)** | `continuation_mode` verified on backend for all 5 modes |
| refresh: no_repeats | **PASS (deploy layer)** | clap-b, journey-ss |
| refresh: similar_to_seed | **PASS (deploy layer)** | seed |
| refresh: programming_only | **PASS (deploy layer)** | lyrics |
| refresh: source_only | **PASS (deploy layer)** | anchor |
| refresh-mode *selection behaviour* | **NOT TESTED (unobservable)** | Refills append to the queue tail; API exposes only ~8 head tracks — selected tracks are invisible (known API limit) |
| Smooth Transitions on/off | **PASS deploy / DEFEATED at playback** | Deploys fine; see Bug 1 — Liquidsoap shuffles the queue, so ordering never reaches the listener |
| Daypart on/off, 3 presets | **PASS deploy / DEFEATED at playback** | Same as above |
| Mood arcs (off / calm_to_party / steady_relaxed / upbeat_days) | **PASS deploy / DEFEATED at playback** | All four deployed; same caveat |
| Filters: tempo+energy | **PASS** | clap-a (tempo 90–140, energy 0.3–0.9) deployed with tracks passing |
| Filters: year | **PASS** | lyrics (1975–2000) |
| Filters: genre include/exclude | **PASS with caveat** | Exact-match semantics zeroed out anchor station; clear feedback shown ("Filters removed all 30 track(s)") — see Bug 5 |
| Filters: mood_include + exclude_artists | **PASS (deploy layer)** | seed station |
| Bootstrap opener playlist | **PASS** | clap-b: Navidrome "Yacht Rock" playlist resolved to 10 opener ids in `programming_json.bootstrap.resolved_ids` |
| Living channel on/off | **PASS (deploy layer)** | mood station; 30 living-pool items confirmed via backup ("30 living-pool item(s)") |
| Suggest Programming (chat_preview) | **PASS end-to-end** | AJAX path returned `ok:true`, 60 tracks; draft page renders with preview table |
| Draft Whole Station (design_station) | **PASS end-to-end** | Prompt → drafted name "Sunday Mellow Jazz", journey type, daypart preset + mood arc, smooth transitions, with review flash |
| Backup export | **PASS** | 200, valid JSON, all designs + pools present |
| Restore round-trip | **PASS (lossless)** | Export → restore → re-export: 39/39 channel objects byte-identical; "Restored 39 channel(s) and 30 living-pool item(s)" |
| Station delete (plugin path) | **PASS** | All 10 zz stations deleted via `afm_action=delete`; backend confirmed 0 remaining |
| Station enable/disable (PUT) | **PASS** | Used 16× during test; Liquidsoap picked up every change within seconds |

## Stability under concurrent load

**Conditions:** 22 concurrent listeners for 480 s via `/api/stations/{slug}/listen`
(6 on one mount, 2 each on 8 other mounts, mixed zz + original stations), while a
refill hammer POSTed `refresh-queue` to 12 stations every 30 s (96 total) and a probe
timed admin/public/detail API calls every 15 s. 23 stations on air throughout.

**Result: the backend passed everything the historical bugs would predict it to fail.**

- **Streams:** 22/22 listeners held the full 8 minutes at a rock-steady 23.5 KB/s
  (≈192 kbps MP3), 11.6 MB each. No stalls, drops, truncation, or mount conflicts.
- **Refills under load:** 96/96 returned 200. Latency min 0.14 s / median 0.35 s /
  p90 0.75 s / max 1.95 s. The historical refill-freeze and pool-exhaustion did
  **not** reproduce.
- **API latency under load:** flat vs baseline — admin list ~1.0 s median (baseline
  0.95 s), public list 0.26 s, station detail 0.21 s. No timeouts, no non-200s.
- **Queues:** never starved (none below 5 at any sample), no duplicate tracks in any
  queue head at any snapshot.
- **`source_last_error`:** empty on every station at every check.
- **Logs:** full `backend.log` scan for the entire window — zero tracebacks, zero
  errors, zero warnings (excluding routine httpx INFO noise).

The one load-adjacent anomaly (queues draining far faster than real playback) turned
out to be Bug 1, not a concurrency failure.

## Bugs found

### Bug 1 — Liquidsoap plays queues in SHUFFLED order; ordering features are silently defeated (MAJOR, backend)

The per-station script template builds its source as
`playlist(id="pl-" ^ slug, reload_mode="watch", playlist_path)` with **no `mode`
argument** — Liquidsoap's `playlist()` defaults to `mode="randomize"`. The backend
meticulously orders `queue.m3u` (harmonic ordering, daypart energy/mood arcs, journey
drift spine, artist separation), then Liquidsoap shuffles it.

Second-order effect: every refill rewrites the m3u, the watch-reload reshuffles, the
next track start matches a *random* queue position, and `mark_track_started`'s sweep
(app/services/queue.py, the `passed` loop) bulk-marks every item before the match as
`played`. Queued tracks vanish without ever airing.

- **Repro:** snapshot a station's `up_next` head via `GET /api/stations/{slug}`; let
  1–2 tracks elapse; the tracks that actually air are not from the head. Force a few
  refills and watch `queued_count` collapse toward the target with almost no entries
  added to `recently_played`.
- **Evidence:** `zz-test-clap-a` had ~55 items enqueued cumulatively (15 at deploy +
  8 hammer rounds × 5); play history showed **5** real plays; final queue was 15 —
  ≈35 items marked played without airing. None of the 5 played tracks appeared in the
  pre-load head-8 snapshot.
- **Impact:** Smooth Transitions, Daypart, mood arcs, and journey ordering all pass
  function-level tests and all do nothing on air. Wasted AudioMuse programming calls.
- **Suggested fix:** add `mode="normal"` to the `playlist()` call in
  `backend/app/services/liquidsoap.py` (`make_station` template, ~line 31). Re-verify
  the `mark_track_started` sweep behaviour afterwards — with in-order playback the
  sweep becomes benign, but it deserves a look for the reload-edge case.

### Bug 2 — Stations created past the running Icecast source limit never come on air, silently (MAJOR, backend)

Icecast's `<sources>` limit is written as `enabled_stations + 2` by
`apply_broadcast_settings` (`backend/app/services/broadcast_settings.py:29`) and the
file is rewritten on every station create — but **nothing tells the running Icecast to
reload it**. `restart_icecast_container()` exists but is only wired to the manual
`POST /api/admin/broadcast/restart-icecast` endpoint.

- **Repro:** with N stations enabled since backend start, create N+3 stations. The
  first 2 new ones connect (they consume the `+2` headroom); every one after that
  loops forever: Liquidsoap retries the source connection every ~4 s, Icecast answers
  403, `icecast-error.log` fills with `WARN ... maximum source limit reached 23`.
- **Observed live:** created 10 stations on top of 21 → exactly 2 came on air, 8
  never did. Freeing slots (disabling 8 originals) let all 8 connect within seconds —
  no restart needed once under the limit.
- **Worse:** nothing surfaces this. `source_last_error` stays empty, the plugin
  deploy reports success (id + queued count), and the admin UI just shows the station
  off-air with no reason. The only diagnostic is the Icecast error log.
- **Suggested fix:** in `apply_broadcast_settings`, when the newly computed
  `source_slots` exceeds the limit the running Icecast was started with, trigger the
  restart (or provision generous headroom, e.g. `enabled + 10`, and surface a
  "restart Icecast to apply" warning in the deploy response). Tracking the
  "currently-applied" limit needs storing it somewhere (DB row or parsing the live
  Icecast admin stats).

### Bug 3 — Never-deployed channel designs can never be deleted (MINOR→MAJOR over time, plugin)

The only delete path (`afm_action=delete`, plugin `__init__.py` ~8135) requires a
backend `station_id > 0` before it will call `_delete_local_channel`. Designs that
were never deployed — every **Suggest Programming** click saves a draft named after
the prompt (e.g. `high-energy-80s-hair-metal-for-a-friday-night-drive`), and failed
previews save error rows too — have no station id and no UI that offers deletion.
They accumulate forever in the designs table and the edit picker (26 designs vs 21
stations before this test already; 29 after, including 3 undeletable test leftovers).

- **Repro:** open Chat Designer, click Suggest Programming with any prompt, don't
  deploy. The draft design now exists permanently.
- **Suggested fix:** allow the delete action with `station_id == 0` to just call
  `_delete_local_channel(delete_slug)`, and render a Delete control for design-only
  rows (the "Your Stations" table currently renders only remote stations, so
  design-only rows never even get a row there).

### Bug 4 — Forced refresh-queue appends unconditionally; repeated clicks bloat the queue (MINOR, backend)

`admin_refresh_queue` (`backend/app/routers/admin.py:162`) always extends by
`max(queue_target - refresh_threshold, 1)` regardless of current queue length — there
is no "already at/above target, no-op" guard. Observed queues at 2–2.5× target during
the hammer (35 on a target of 15). Currently masked in practice because Bug 1 drains
queues; once Bug 1 is fixed this becomes visible to any admin who clicks the button a
few times.

- **Suggested fix:** compute `need = max(queue_target - current_queued, 0)` (or no-op
  above target) in `admin_refresh_queue`, mirroring `ensure_queue_fresh`.

### Bug 5 — genre_include is exact-match on a single genre; near-misses silently excluded (MINOR, plugin)

`track_passes_filters` (plugin `__init__.py` ~1368) compares the include list against
the track's single `top_genre` with string equality: `genre_include=rock` excludes
"hard rock", "classic rock", "alternative rock" — and any track with *no* genre
metadata fails every include filter. Live: anchor 4 ("Upbeat", 176-track pool) with
`genre_include=rock` → all 30 programming tracks removed. The UI feedback is good
("Filters removed all 30 track(s) from programming"), so this is a semantics trap,
not a silent failure.

- **Suggested fix:** substring/word match (as `_search_genres` already implies by
  offering granular genres) or match against the track's full genre/mood-tag set.

### Not new / known — excluded per test brief

- gpt-5-mini failing AudioMuse AI calls (upstream, NeptuneHub/AudioMuse-AI#766);
  gpt-4o-mini used and working.
- master/:latest DetachedInstanceError refill bug (deployed :test has the fix; 0
  errors observed).
- Step 5 toggles + mood arc defaulting OFF on untouched stations.
- Admin list `on_air`/`stream_url` unreliable (documented previously): during this
  test the admin API reported `on_air: false` for stations that were verifiably
  streaming. Public `/api/stations` remains the source of truth.

## Ranked list — if a fix pass has time for only three

1. **Bug 1 (Liquidsoap randomize)** — one-word-class fix, restores three shipped
   features (smooth transitions, daypart/mood arcs, journey ordering) that are
   currently 100% inert on air, and stops queue-drain churn.
2. **Bug 2 (Icecast source-limit lockout)** — any user who grows their station count
   past the boot-time limit gets silently broken stations with zero diagnostics.
3. **Bug 3 (undeletable drafts)** — unbounded data accumulation with no user remedy;
   trivially reachable via the AI helper the release just promoted.

## Confirmed vs unobservable — do not conflate

**Confirmed working:** everything in the coverage table marked PASS at the layer
stated; stream stability, refill reliability, and API latency under 22-listener /
96-refill concurrent load; backup/restore losslessness; both AI helpers end-to-end.

**Could NOT be observed (API limits — absence of failure here is NOT a pass):**
- Which tracks each refresh mode / daypart / harmonic pass actually *selects* —
  refills append to the tail; only ~8 head tracks are exposed.
- `source_last_ok_at` (never serialized; reads null for every station).
- Pool growth beyond the 500 cap.
- Whether bootstrap opener tracks actually played first on clap-b (queue had advanced
  past the head by first snapshot).
- Never-starve degradation (no station ever starved, so the degraded path never ran).

**Measurement notes for future sessions:** `up_next` exists only on the *detail*
endpoint `GET /api/stations/{slug}` — the list endpoint omits it entirely. The admin
list DOES include `programming_json`, `up_next`, `recently_played`, `pool_count` per
station — richer than previously documented.

## Final state

- **21 original stations**: all enabled and back on air (verified 21/21). The 8
  temporarily disabled during the test (get-crunk, skate-park-anthems,
  eurobeat-express, voltage-district, basement-confessions, broken-amps,
  static-bloom, neon-confetti) were re-enabled and reconnected within seconds.
- **All 10 `zz-test-*` stations deleted** from backend and plugin (verified 0
  remaining).
- **3 junk designs remain and cannot be removed** (Bug 3): `zz-filter-probe`,
  `zz-test-journey-aa`, `high-energy-80s-hair-metal-for-a-friday-night-drive`.
  Harmless; deletable once Bug 3 is fixed.
- **No restore was needed** — nothing was lost. Pre-test backup retained at
  `~/alchemyfm-backups/pre-smoketest-backup-2026-07-18.json`.
- Icecast was **not** restarted (restart action was unavailable to the test); the
  running source limit is still 23, fine for 21 stations but the next 3+ station
  creates will hit Bug 2.
- No code, branch, or release changes were made.
