# Alchemy FM

Turn [AudioMuse](https://github.com/NeptuneHub/AudioMuse-AI) programming into **live internet radio stations**. This app manages stations and queues, [Liquidsoap](https://www.liquidsoap.info/) plays continuously, and [Icecast](https://icecast.org/) broadcasts one shared stream per station. [Navidrome](https://www.navidrome.org/) supplies stream URLs and cover art for tracks already in the queue.

**This is not a music player.** Everyone hears the same live broadcast — no skip, no personal queue, no per-user playback.

### Who this is for

- You already run **AudioMuse** (Song Alchemy anchors or similarity) and **Navidrome** for your library
- You want **24/7 radio-style channels** on Icecast, not a Spotify-like UI
- You're comfortable with **Docker Compose** (or Unraid / a reverse proxy for production)

### Who this is not for

- Standalone music hosting without AudioMuse and Navidrome
- On-demand or per-listener playback

### What you get

**Listeners** — station picker with play-from-grid, persistent mini-player while browsing, tune-in page with now playing / up next / recently played, per-listener color themes, and optional artist biography.

**Operators** — admin UI for **on-air control** (On/Off per station), global broadcast and appearance settings, optional track trivia when enabled ([Knowledge feature](#optional-knowledge-trivia)), and **PWA operator heart**: sign in from the listener UI, then heart on-air tracks (stars in Navidrome and adds to a playlist you pick in Appearance).

**Channel designers** — use the **[Alchemy FM Channel Designer](audiomuse-plugins/README.md)** plugin inside AudioMuse to search by sonic vibe (CLAP), lyrics themes, or mood clusters; preview tracks with tempo/energy/mood; enable living channels; then deploy to Alchemy FM. Admin does not create or edit programming — that lives in the plugin.

```
AudioMuse ──▶ Channel Designer plugin ──deploy──▶ Backend ──▶ queue.m3u ──▶ Liquidsoap ──▶ Icecast ──▶ Listeners
     │                                                    │
     └── CLAP / lyrics / mood / anchors                   └── Web UI (listener + ops admin)
Navidrome ──▶ stream URLs & cover art for queued tracks ─────────┘
```

## Quick start (local Docker)

**Prerequisites:** Docker & Docker Compose, [AudioMuse-AI](https://github.com/NeptuneHub/AudioMuse-AI) (core **2.5.0+**) and [Navidrome](https://www.navidrome.org/) running and reachable from Docker (see [troubleshooting](#troubleshooting)). Your library should be analyzed in AudioMuse so anchors, similarity, or CLAP search work.

1. **Clone and configure**

   ```bash
   git clone https://github.com/MMagTech/alchemyfm.git
   cd alchemyfm
   cp .env.example .env
   ```

   Edit `.env` — at minimum set `AUDIOMUSE_URL`, `AUDIOMUSE_API_TOKEN`, `NAVIDROME_URL`, `NAVIDROME_USER`, `NAVIDROME_PASSWORD`, and `ADMIN_PASSWORD`.

2. **Start the stack**

   ```bash
   docker compose up -d --build
   ```

3. **Install the Channel Designer plugin** (in AudioMuse)

   - **Plugins → Repositories → Add**
     ```
     https://raw.githubusercontent.com/MMagTech/alchemyfm/master/audiomuse-plugins/manifest.json
     ```
   - **Plugins → Catalog → Refresh catalog**
   - Install **Alchemy FM Channel Designer** → **Apply now (restart)**
   - In plugin **Settings**, set your Alchemy FM URL and admin credentials (e.g. `http://localhost:8080`)

   Full workflow: [audiomuse-plugins/README.md](audiomuse-plugins/README.md)

4. **Design and deploy a channel** (in AudioMuse)

   Open **Plugins → Alchemy FM** (Channel Designer). Pick a programming type (CLAP, lyrics, mood, anchor, or seed), preview tracks, then **Deploy to Alchemy FM**. Example mount: `/yachtrock`.

5. **Operate** (optional)

   Open [http://localhost:8080/admin.html](http://localhost:8080/admin.html) and sign in with `ADMIN_USERNAME` / `ADMIN_PASSWORD`. Use **On** / **Off** for broadcast control, **Appearance** for themes and heart playlist, **Broadcast** for encoding. Edit programming back in the Channel Designer plugin.

6. **Tune in**

   - Station list: [http://localhost:8080](http://localhost:8080)
   - Live stream: [http://localhost:8000/yachtrock](http://localhost:8000/yachtrock)

New stations go live within a few seconds — no manual Liquidsoap restart.

## Channel Designer plugin

The plugin ships in [`audiomuse-plugins/`](audiomuse-plugins/) and installs **inside AudioMuse**, not in the Alchemy FM Docker stack.

| You do in AudioMuse | Alchemy FM receives |
|---------------------|---------------------|
| CLAP / lyrics / mood search, preview, filters | Deployed station + queue bootstrap |
| Living channel + scheduled refresh | Updated pool and optional push to Alchemy FM |
| Song Alchemy anchor from preview | `alchemy_anchor` programming for 24/7 refill |

Plugin-only updates on GitHub do **not** require pulling new Alchemy FM Docker images — refresh the plugin catalog in AudioMuse and apply the update. Docker images rebuild only when backend, web, Liquidsoap, or Icecast code changes.

See **[audiomuse-plugins/README.md](audiomuse-plugins/README.md)** for install troubleshooting, living channels, and local plugin development.

## Other deployments

| Guide | Use when |
|-------|----------|
| [docs/UNRAID.md](docs/UNRAID.md) | Unraid server, Compose Manager, GHCR images |
| [docs/TRAEFIK.md](docs/TRAEFIK.md) | One HTTPS hostname for the site and streams |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Public internet, reverse proxy, security checklist |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Backend-only dev, project layout, contributing |

## How it works

1. Channel Designer (or admin API) creates a station → backend **bootstraps a track pool** from AudioMuse.
2. Backend writes a per-station Liquidsoap script; the **supervisor** starts one process per enabled station.
3. Liquidsoap reads `queue.m3u`; Icecast broadcasts; now playing syncs from the stream.
4. When the queue runs low, **tiered refill**: new batch → pool reuse → anchor → similar seed/last (per station settings).

### Source types

| Type | `source_ref` | Fetched from |
|------|--------------|--------------|
| `alchemy_anchor` | Anchor id | AudioMuse `POST /api/alchemy` |
| `similar_seed` | Track item_id | AudioMuse `GET /api/similar_tracks` |

Navidrome is **not** a programming source — it resolves audio and artwork for tracks already queued.

### Continuation mode (`continuation_mode`)

When a station exhausts fresh tracks from its primary source:

| Mode | Behavior |
|------|----------|
| `source_only` (default) | Reuse tracks from the imported pool. No drift. |
| `similar_to_seed` | Pull similar tracks from a fixed seed in the pool. |
| `similar_to_last` | Pull similar tracks from whatever just played (can wander). |

### Optional: Knowledge trivia

Track trivia on station pages is **off by default**. Set `KNOWLEDGE_FEATURE=true` in `.env`, restart the backend, then open **Admin → Knowledge** to configure providers and mode. When disabled, the Knowledge nav link is hidden and `/admin/knowledge.html` returns 404 — that is expected.

See [docs/MUSIC_KNOWLEDGE_PLAN.md](docs/MUSIC_KNOWLEDGE_PLAN.md) and [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Troubleshooting

| Problem | Things to check |
|---------|-----------------|
| Admin shows **503** | Set `ADMIN_PASSWORD` in `.env` and restart the backend container |
| **Knowledge** link missing or `/admin/knowledge.html` **404** | `KNOWLEDGE_FEATURE=true` in `.env` (not just compose default) and restart backend; confirm `/api/health` reports `"knowledge_feature": true` |
| Station has **no tracks** / empty queue | AudioMuse URL/token in `.env`; anchor or seed exists; redeploy or refresh from Channel Designer; backend logs for AudioMuse errors |
| **Can't create a station** in admin | Expected — use the Channel Designer plugin in AudioMuse to create and edit channels |
| Plugin catalog shows **old version** | **Plugins → Refresh catalog**; check **Installed** tab for updates; wait a few minutes for GitHub CDN |
| Need **backend logs** after a restart | Persistent log at `DATA_DIR/logs/backend.log` (default `/data/logs/backend.log` in Docker; rotates at ~20 MB total). Also `docker compose logs backend` for stdout |
| Backend **can't reach** AudioMuse/Navidrome on the host | On Docker Desktop use `host.docker.internal`; on Linux add `extra_hosts` or use the host LAN IP in `.env` |
| Stream **won't play** | Icecast on port `8000`; mount matches deploy (e.g. `/yachtrock`); station is **On** in admin |
| **Wrong stream URL** behind a proxy | See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — set `TRUST_PROXY_HEADERS=true` and route mount paths to Icecast |

## Security (public internet)

**Set `ADMIN_PASSWORD` before exposing the site.** Without it, admin is disabled; with an empty password on a reachable host, you risk an open admin panel once a password is added without locking down access.

Full checklist, reverse proxy examples, and stream URL rules: **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**.

Report vulnerabilities privately: **[SECURITY.md](SECURITY.md)** (not public issues).

## API (summary)

| Endpoint | Description |
|----------|-------------|
| `GET /api/stations` | Public station list (enabled only) |
| `GET /api/stations/{slug}` | Now playing, up next, recently played |
| `GET /api/admin/stations` | All stations (admin) |
| `POST /api/admin/stations` | Create station (used by Channel Designer deploy; not exposed in admin UI) |
| `POST /api/admin/stations/{id}/refresh-queue` | Manual queue refresh |
| `GET /api/admin/navidrome/playlists` | Navidrome playlists (admin; Appearance picker) |
| `POST /api/admin/navidrome/songs/{item_id}/heart` | Star/unstar on-air track; heart-on also appends to default playlist |

## Contributing

Contributions welcome — [CONTRIBUTING.md](CONTRIBUTING.md), [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Notes

- Editing a station mount or Icecast settings restarts **only that station** (~1–2s gap on that mount).
- Icecast source password in `.env` must match `icecast/icecast.xml` (default `hackme`).
- `LIQUIDSOAP_CALLBACK_SECRET` must match between backend and Liquidsoap containers.
- Queue files: the backend rewrites each `queue.m3u` from the database; after upgrading an old install, use admin **Rebuild M3U** once per station. See [docs/TECH_DEBT.md](docs/TECH_DEBT.md).
- Backend logs persist under `DATA_DIR/logs/` with automatic rotation (`LOG_MAX_BYTES` × `LOG_BACKUP_COUNT`, default ~20 MB cap). Optional overrides in `.env`.

## License

MIT — see [LICENSE](LICENSE).
