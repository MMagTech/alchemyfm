# Technical debt & known issues

Items here are intentional backlog — not forgotten bugs.

---

## 🔴 P1: `queue.m3u` grows append-only during normal operation

**Status:** Open — needs design + implementation  
**Area:** `backend/app/services/queue.py`, Liquidsoap playlist  
**Workaround:** Restart backend (rebuilds all M3Us on startup) or use admin **Rebuild M3U** per station

### What happens today

Each station has a Liquidsoap playlist at `data/stations/{slug}/queue.m3u`.

| Code path | Behavior | When |
|-----------|----------|------|
| `append_to_m3u()` | **Appends** new tracks to the end of the file | Every routine queue refill (`extend_queue`) |
| `rebuild_m3u_from_db()` | **Rewrites** file with only `queued` + `playing` rows | Backend startup, admin rebuild, station bootstrap |

During normal 24/7 operation, refills only **append**. Played tracks are marked `played` in SQLite but **are not removed** from the M3U. Liquidsoap (`reload_mode="watch"`) keeps the full history in its playlist and will eventually replay old entries.

The database queue and the on-disk M3U **diverge** over time.

### Symptoms (already observed in production)

- Tracks replay that the DB considers long since played
- `mark_track_started` fails to match on-air metadata → `PlayHistory` rows with empty `item_id`
- Missing album art (`cover_url` null) until catalog lookup compensates
- `recently_played` shows duplicate titles with and without `item_id`
- Gradually larger M3U files → slower Liquidsoap reload on each append

### Scale / when it hurts

- **Days–weeks:** behavioral drift (repeats, metadata mismatch) — **primary concern**
- **Months:** M3U may reach thousands of entries per station (still small on disk, but reload cost grows)
- **Mitigation today:** backend restart trims M3U to current queue only

### Proposed fix (to implement later)

Pick one (or combine):

1. **Rebuild on consumption** — after `mark_track_started` marks items played, call `rebuild_m3u_from_db` (or delete consumed lines) so M3U always mirrors pending queue
2. **Replace append with rebuild on refill** — `extend_queue` rebuilds instead of appending (simplest; watch Liquidsoap reload behavior)
3. **Periodic rebuild** — e.g. every N tracks or every queue refresh cycle if file line count exceeds `queue_target * k`
4. **Liquidsoap-native queue** — push next track via request/cue instead of monolithic M3U (larger change)

### Acceptance criteria

- [ ] M3U line count stays bounded (~`queue_target` + small buffer per station)
- [ ] Liquidsoap does not replay tracks already marked `played` unless pool/refill logic intentionally re-queues them
- [ ] `mark_track_started` reliably gets `item_id` without catalog fallback
- [ ] No audible gap/regression on track transitions when M3U is trimmed
- [ ] Document operational behavior in README

### Related code

- `append_to_m3u`, `rebuild_m3u_from_db`, `extend_queue`, `mark_track_started` — `backend/app/services/queue.py`
- `playlist(..., reload_mode="watch")` — `backend/app/services/liquidsoap.py`
- Compensating lookup: `_lookup_item_id_from_catalog` — `queue.py` (treats symptom, not root cause)

---

*Add new items below this line.*
