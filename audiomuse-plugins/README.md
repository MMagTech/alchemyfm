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
| Live programming deploy | v3+ pushes CLAP/lyrics/mood directly — refills re-query AudioMuse (no frozen anchor required) |

CLAP, lyrics, and mood channels are **previewed in AudioMuse**, then deployed live to Alchemy FM for 24/7 refills.

### Install

Requires AudioMuse core **2.5.0+** (`min_core_version` in `plugin.json`).

1. **Plugins → Repositories → Add**
   ```
   https://raw.githubusercontent.com/MMagTech/alchemyfm/master/audiomuse-plugins/manifest.json
   ```
2. **Plugins → Catalog → Refresh catalog**
3. Install **Alchemy FM Channel Designer** → **Apply now (restart)**
4. In plugin **Settings**, set Alchemy FM URL + admin credentials (e.g. `https://alchemyfm.example.com` or LAN `http://192.168.1.100:8080`)

### Plugin not showing the latest version?

GitHub is live immediately; AudioMuse caches the catalog (~1 hour, or until you refresh).

1. **Plugins → Repositories** — confirm the manifest URL above is listed
2. **Remove** that repository → **Add** it again → **Catalog → Refresh catalog**
3. If already installed at an older version: check the **Installed** tab (not Catalog) for **Update**
4. Click **Apply now (restart)** after install/update

If the catalog still shows an old version, wait 5 minutes (GitHub CDN cache) and refresh again.

Plugin updates do **not** require pulling new Alchemy FM Docker images — only refresh and apply in AudioMuse.

### Releasing a plugin version (maintainers)

AudioMuse installs the **zip**, not the git source. The catalog version label must match the code inside the zip.

After editing `audiomuse-plugins/alchemy_fm_bridge/__init__.py`:

```bash
python scripts/release_plugin.py "Short changelog for this release."
python scripts/verify_plugin_release.py
git add audiomuse-plugins/alchemy_fm_bridge/__init__.py \
  audiomuse-plugins/alchemy_fm_bridge/plugin.json \
  audiomuse-plugins/alchemy_fm_bridge.zip
git commit -m "Release Channel Designer plugin X.Y.Z."
```

CI runs `verify_plugin_release.py` on every PR/push and **fails** if catalog version, checksum, or zip contents drift. The release job on `master` rebuilds the zip if needed; set **`RELEASE_BOT_TOKEN`** (repo secret with push + bypass on protected `master`) so the bot can push release commits when you only merge source changes.

### Workflow

**Full step-by-step guide (what is universal vs per-station):** [docs/CHANNEL_DESIGNER_HELP.md](../docs/CHANNEL_DESIGNER_HELP.md)

Short version:

1. **Settings** (once) — Alchemy FM URL + admin credentials; optional AudioMuse API URL for living cron
2. **Alchemy FM** menu — Channel Designer
3. **+ New channel** → pick **programming type** (required)
4. Optional per station: **filters**, **bootstrap opener**, **living channel**
5. **Preview programming** — review tracks; fix programming/filters if needed
6. **Channel + deploy** — name, slug, mount, queue rules → **Deploy to Alchemy FM**
7. **(Living only)** Enable **Administration → Scheduled Tasks → Alchemy FM** once globally

Operate deployed stations (On/Off, appearance, broadcast encoding) in [Alchemy FM admin](https://github.com/MMagTech/alchemyfm#quick-start-local-docker) — not in the plugin.

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

Set **AudioMuse API URL** in plugin settings if the worker cannot reach the web UI host (use a LAN address the worker can reach, e.g. `http://192.168.1.100:8387`).

### Roadmap (v2.4+)

- Navidrome bootstrap playlists via `create_or_replace_playlist`
- Direct Alchemy FM `clap_query` / `lyrics_query` source types (no anchor bridge)
