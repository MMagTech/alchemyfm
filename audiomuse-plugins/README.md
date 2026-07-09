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
| 24/7 deploy bridge | Blends preview → saves anchor → pushes `alchemy_anchor` station |

CLAP, lyrics, and mood channels are **auditioned in AudioMuse**, then compiled into a Song Alchemy anchor so Alchemy FM can refill queues around the clock.

### Install from GitHub

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
4. **Preview programming** — review tracks with BPM / energy / mood
5. **Deploy to Alchemy FM** — creates/updates station + optional bootstrap

### Local development

```bash
cd audiomuse-plugins/alchemy_fm_bridge
zip -j ../alchemy_fm_bridge.zip __init__.py
cd ..
python -m http.server 8000
```

Add `sourceUrl` to your test `plugin.json` version entry pointing at `http://<lan-ip>:8000/alchemy_fm_bridge.zip` (omit `checksum` for local installs).

### Roadmap (v2.1+)

- Cron refresh: re-score library against saved channel profiles
- `on_song_analyzed` hook: auto-add new matches to channel pools
- Navidrome bootstrap playlists via `create_or_replace_playlist`
- Direct Alchemy FM `clap_query` / `lyrics_query` source types (no anchor bridge)
