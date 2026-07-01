# Technical debt & known issues

Items here are intentional backlog — not forgotten bugs.

---

## ✅ P1: `queue.m3u` grows append-only during normal operation — **fixed**

**Status:** Resolved — `rebuild_m3u_from_db` runs on refill, bootstrap, and track start  
**Area:** `backend/app/services/queue.py`

### What changed

| Code path | Behavior |
|-----------|----------|
| `extend_queue` | Rewrites M3U from DB after adding tracks |
| `bootstrap_station` | Rewrites M3U from DB after initial queue |
| `mark_track_started` | Rewrites M3U after marking `played` / `playing` |
| Backend startup / admin **Rebuild M3U** | Unchanged safety net |

`append_to_m3u` was removed. M3U should stay bounded to `queued` + `playing` rows (~`queue_target` + 1).

### After deploy

Run admin **Rebuild M3U** once per station (or restart backend) to trim any already-bloated files from before the fix.

### Related code

- `rebuild_m3u_from_db`, `extend_queue`, `mark_track_started` — `backend/app/services/queue.py`
- `playlist(..., reload_mode="watch")` — `backend/app/services/liquidsoap.py`

---

*Add new items below this line.*
