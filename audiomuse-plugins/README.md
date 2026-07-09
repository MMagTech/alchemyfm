# AudioMuse plugins for Alchemy FM

Third-party [AudioMuse-AI](https://github.com/NeptuneHub/AudioMuse-AI) plugins maintained alongside the [Alchemy FM](https://github.com/MMagTech/alchemyfm) radio stack.

## Alchemy FM Channel Designer (`alchemy_fm_bridge`)

**v2** turns AudioMuse into a programming studio for live radio. Design a channel with AudioMuse intelligence, preview tracks with analysis data, then deploy to Alchemy FM.

### What it does (that Alchemy FM admin alone cannot)

| Capability | AudioMuse API |
|------------|---------------|
| Sonic vibe search | `POST /api/clap/search` |
| Lyrics theme search | `POST /api/lyrics/search/text` |
| Mood cluster browse | `GET /api/mood_centroids` + `GET /api/similar_tracks` |
| Song Alchemy anchors | `GET /api/anchors`, `POST /api/alchemy` |
| Preview enrichment | `get_score_data_by_ids()` — tempo, energy, mood |
| Tempo/energy filters | Narrow preview and living pool by BPM and energy |
| Living channels | `on_song_analyzed` auto-pool + nightly cron refresh |
| Audition history | Last 20 preview runs per channel |
| 24/7 deploy bridge | Blends preview → saves anchor → pushes `alchemy_anchor` station |

CLAP, lyrics, and mood channels are **auditioned in AudioMuse**, then compiled into a Song Alchemy anchor so Alchemy FM can refill queues around the clock.

### Plugin not showing v2 in AudioMuse?

GitHub is live immediately; AudioMuse caches the catalog (~1 hour, or until you refresh).

1. **Plugins → Repositories** — confirm this URL is listed:
   ```
   https://raw.githubusercontent.com/MMagTech/alchemyfm/master/audiomuse-plugins/manifest.json
   ```
2. **Remove** that repository → **Add** it again → **Catalog → Refresh catalog**
3. If already installed at v1.x: check **Installed** tab (not Catalog) for **Update to v2.x**
4. Click **Apply now (restart)** after install/update
5. Requires AudioMuse core **2.5.0+** (`min_core_version` in plugin.json)

If the catalog still shows v1, wait 5 minutes (GitHub CDN cache) and refresh again.


1. **Plugins → Repositories → Add**
   ```
   https://raw.githubusercontent.com/MMagTech/alchemyfm/master/audiomuse-plugins/manifest.json
   ```
2. **Plugins → Catalog → Refresh catalog**
3. Install **Alchemy FM Channel Designer** → **Apply now (restart)**

### Workflow

1. **Settings** — Alchemy FM URL + admin credentials (`https://alchemyfm.mmagtech.com`, etc.)
2. **Alchemy FM** menu — Channel Designer
3. Pick programming type (CLAP, lyrics, mood, anchor, or seed)
4. Optional: set tempo/energy filters and enable **Living channel**
5. **Preview programming** — review tracks with BPM / energy / mood
6. **Deploy to Alchemy FM** — creates/updates station + optional bootstrap
7. Enable **Administration → Scheduled Tasks → plugin.alchemy_fm_bridge.refresh_living** for nightly pool refresh

### Local development

```bash
cd audiomuse-plugins/alchemy_fm_bridge
zip -j ../alchemy_fm_bridge.zip __init__.py
cd ..
python -m http.server 8000
```

Add `sourceUrl` to your test `plugin.json` version entry pointing at `http://<lan-ip>:8000/alchemy_fm_bridge.zip` (omit `checksum` for local installs).

### Living channels (v2.3+)

When **Living channel** is enabled on a saved profile:

- **`on_song_analyzed`** (worker): newly analyzed songs that pass tempo/energy filters are added to the channel pool.
- **`refresh_living`** (cron, default 03:00 daily, disabled until enabled): re-runs programming, updates the pool, records audition history, and optionally updates the Alchemy FM station + queue.

Set **AudioMuse API URL** in plugin settings if the worker cannot reach the web UI host (use your LAN IP, e.g. `http://192.168.1.10:8387`).

### Roadmap (v2.4+)

- Navidrome bootstrap playlists via `create_or_replace_playlist`
- Direct Alchemy FM `clap_query` / `lyrics_query` source types (no anchor bridge)
