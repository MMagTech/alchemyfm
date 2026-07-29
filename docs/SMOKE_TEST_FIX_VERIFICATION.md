# Fix-Pass Verification Plan — follow-up to SMOKE_TEST_REPORT.md (2026-07-18)

The full matrix already passed in the 2026-07-18 smoke test; this pass verifies
**only the five fixes** against the live instances after deploying the new
backend `:test` image and plugin 4.2.0. Baseline: `docs/SMOKE_TEST_REPORT.md`.

## Deploy prerequisites

- Backend: merge/push `knowledge`, run `gh workflow run "Publish Docker images"
  --ref knowledge` (pushes to `knowledge` do NOT auto-build), pull `:test` on the
  server, restart the backend container.
- Plugin: cut 4.2.0 with `scripts/release_plugin.py` (historically run from
  `master`), install via AudioMuse Plugins UI.
- Note: on first backend boot after this deploy, the backend will restart
  Icecast once (applied source limit is unknown → sync fires). Expect a brief
  all-station blip, then all mounts reconnect.

## Checks (in order)

### 1. Bug 1 — in-order playback (was: Liquidsoap shuffled queues)
- `GET /api/stations/{slug}/listen` on one station; snapshot `up_next` via
  `GET /api/stations/{slug}` (detail endpoint — the list omits `up_next`).
- Let 2–3 tracks elapse. **PASS =** each aired track (now_playing /
  recently_played) is the prior queue head, in order.
- Force `POST /api/admin/stations/{id}/refresh-queue` mid-track. **PASS =** no
  track skip, no bulk `queued_count` drop; backend log free of the new
  "matched N deep in the queue" warning.

### 2. Bug 2 — source-limit lockout (was: silent 403 loop past limit)
- Note current enabled-station count. Create enough throwaway stations to
  exceed the old running limit (with the new headroom formula, limit =
  max(16, enabled+10), so first verify the boot-time restart applied it:
  `icecast-error.log` should show no "maximum source limit reached").
- **PASS =** every new station reaches `on_air: true` on the public list within
  ~60 s, with an automatic Icecast restart if the limit had to grow
  (backend log: "Restarted Icecast to raise the source limit to N").
- Also verify the warning path: if restart were unavailable the create/update
  response carries `icecast_warning` and the plugin deploy page shows
  "Deployed, but not on air yet: …". (Only testable by disabling the Docker
  socket — optional.)

### 3. Bug 3 — draft designs deletable
- The three stranded designs from the smoke test are the live test case:
  `zz-filter-probe`, `zz-test-journey-aa`,
  `high-energy-80s-hair-metal-for-a-friday-night-drive`.
- **PASS =** each appears in "Your Stations" with a "Not Deployed" badge and a
  working Delete button; after deletion they are gone from the backup export.
  (This is real cleanup doubling as the test.)

### 4. Bug 4 — refresh-queue idempotent
- On a station with a full queue, `POST refresh-queue` 5×.
- **PASS =** `queued_count` stays at `queue_target` (small transient overshoot
  from in-flight refills acceptable; monotonic growth is a FAIL).

### 5. Bug 5 — genre filter matches subgenres
- Re-run the smoke test's failing case: preview `alchemy_anchor` anchor 4
  ("Upbeat") with `filter_genre_include=rock`.
- **PASS =** non-empty preview (was 0/30), and the filter feedback line says
  "matches Top Genre and its subgenres" with a missing-genre count when
  applicable.

### 6. Regression guard — slim load pass
- 5+ concurrent listeners across 3 mounts for ~3 minutes with a few forced
  refills. **PASS =** steady byte rate, no disconnects, `source_last_error`
  empty, no tracebacks in backend log. (Bug 1's fix touches the playback layer;
  this confirms stream stability is unchanged.)

## Test-suite status at commit time (2026-07-18, Windows dev box)

- Backend: 160/164 pass. The 4 failures reproduce on the base commit unchanged:
  2× `test_queue_refill_sessions` (needs network mocking that this Python
  3.14 venv breaks), 2× flaky Windows `PermissionError` on icecast.xml writes
  (pre-existing; also fails on base). Linux CI is the arbiter.
- Plugin: 58/59 pass. `test_release_integrity` fails by design until the 4.2.0
  zip is rebuilt by `scripts/release_plugin.py`.
