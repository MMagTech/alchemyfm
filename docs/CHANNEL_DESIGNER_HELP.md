# Channel Designer Help

Operator guide for the **Alchemy FM Channel Designer** AudioMuse plugin (v3+).

Design stations in AudioMuse using CLAP, lyrics, mood clusters, and more — then deploy live programming to [Alchemy FM](https://github.com/MMagTech/alchemyfm).

---

## Before You Start

1. **Plugin settings** — Alchemy FM URL, admin username/password (`ADMIN_USERNAME` / `ADMIN_PASSWORD` from your Alchemy FM `.env`).
2. **AudioMuse API URL** (optional but recommended) — LAN address the **worker** can reach for living-channel cron and `on_song_analyzed` (e.g. `http://192.168.1.10:8387`). Do not use `localhost` if the worker runs in a separate container.
3. **v3 upgrade** — Delete pre-v3 Alchemy FM stations and redeploy fresh. v3 uses live CLAP/lyrics/mood refills instead of frozen Song Alchemy anchors.

---

## Workflow

1. Pick a **programming type** and criteria.
2. Optional: set **filters**, **bootstrap opener**, or **living channel**.
3. **Preview Programming** — review tracks with BPM, energy, and mood.
4. **Deploy to Alchemy FM** — creates or updates the station and optionally bootstraps the queue.
5. Listen on the station mount; use Alchemy FM **Admin** for encoding, appearance, and global broadcast settings.

---

## Programming Types

| Type | Use when |
|------|----------|
| **Sonic Vibe (CLAP)** | Describe the sound in plain language (e.g. late-night rock, energetic guitar). |
| **Lyrics Theme** | Search by meaning or theme (e.g. songs about the open road). |
| **Mood Cluster** | Pick a mood and sub-cluster from your library analysis. |
| **Song Alchemy Anchor** | Reuse an existing anchor playlist. |
| **Similar to Seed Track** | Build around one library track's sonic neighbors. |

---

## Filters

Optional rules narrow **preview**, **living auto-add**, and **cron refresh**:

- Tempo and energy bounds
- Year min/max
- Genre include/exclude — **exact match** on analyzed `top_genre` (see Genre column in Preview)
- Mood tags include — autocomplete from AudioMuse mood labels; inline warning for unknown tags
- Exclude artists — search library to add; exact match on artist name

After **Preview Programming**, the Filters panel shows a **Filter check** summary: which terms matched tracks in the pool and the top genres present. Genre and mood filters use analyzed score metadata, not free-text tags.

---

## Bootstrap Opener

Optional **Navidrome playlist** cold-start:

- **Search** Navidrome playlists by name and **pick** a result (sets Playlist ID automatically), or paste an id manually.
- Click **Verify** to confirm AudioMuse can resolve the playlist and load opener tracks.
- On **Preview**, a bootstrap warning appears if verification fails; **Deploy** blocks until the playlist verifies.
- Opener tracks play first at deploy/bootstrap; refills use your programming query.

Playlist search uses AudioMuse `GET /api/search_playlists`; verification uses `GET /api/playlist`.

---

## Living Channel

When enabled on a saved profile:

- **Auto-add** — newly analyzed songs that pass filters join the channel pool (`on_song_analyzed` on the worker).
- **Auto-refresh** — cron re-runs programming, merges the pool, and optionally updates the Alchemy FM station + queue.

Enable the cron task under **AudioMuse → Administration → Scheduled Tasks → Alchemy FM** (`plugin.alchemy_fm_bridge.refresh_living`, default 03:00 daily).

---

## Discover Channels

Lists **clustering playlists** from AudioMuse `GET /api/playlists` (a map of playlist name → tracks). Includes results from the default weekly clustering job and manual runs.

- **Run Clustering** — optional manual `POST /api/clustering/start` if you want a fresh run now.
- **Use in Designer** — prefills a CLAP query from the cluster name/mood.

Wait for clustering to finish (check **Active Tasks**) before expecting playlists to appear.

---

## Chat Designer (One-Shot)

Natural-language playlist ideas via `POST /chat/api/chatPlaylist`. Requires AudioMuse chat/AI configured.

- Slow (LLM) — use for initial ideas, then tweak and deploy.
- **Not** wired to living cron.

---

## Edit Toolbar (Deployed Stations)

| Action | What it does |
|--------|----------------|
| **Refresh Queue** | Refill the play queue from programming + pool. |
| **Rebuild Pool** | Full bootstrap — clears queue/pool and re-imports. |
| **Rebuild M3U** | Rewrite `queue.m3u` from the database. |
| **Put On Air / Take Off Air** | Toggle `enabled` without redeploying. |
| **Upload / Remove Art** | Station artwork on Alchemy FM. |

**Source error** badges show the last programming/refill failure from Alchemy FM.

---

## When Pool Runs Low

**Continuation mode** on deploy:

| Mode | Behavior |
|------|----------|
| **Similar to Last Played** | Drift from the last track (recommended for variety). |
| **Similar to Programming Seed** | Stay near the fixed seed. |
| **Stay in Source Pool** | Reuse imported pool; allows repeats. |

---

## Cloudflare / Public URLs

If Alchemy FM is behind Cloudflare, allow server-to-server access to `/api/admin/*` from your AudioMuse host, or use a direct/LAN URL in plugin settings.

---

## More

- Plugin catalog: [audiomuse-plugins/README.md](../audiomuse-plugins/README.md)
- Roadmap / backlog: [CHANNEL_DESIGNER_BACKLOG.md](CHANNEL_DESIGNER_BACKLOG.md)
- Alchemy FM admin: your instance `/admin.html`
