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

**Listeners** — station picker, tune-in page with now playing / up next / recently played, and a listen button.

**Operators** — admin UI to create stations, set mounts and programming sources, manage broadcast settings, and optional track trivia (Knowledge feature).

```
AudioMuse ──▶ Backend ──▶ queue.m3u (per station) ──▶ Liquidsoap ──▶ Icecast ──▶ Listeners
                  │                                              ▲
                  └── Web UI (status + admin)                    │
Navidrome ──▶ stream URLs & cover art for queued tracks ─────────┘
```

## Quick start (local Docker)

**Prerequisites:** Docker & Docker Compose, [AudioMuse-AI](https://github.com/NeptuneHub/AudioMuse-AI) and [Navidrome](https://www.navidrome.org/) running and reachable from Docker (see [troubleshooting](#troubleshooting) below). Your library should be analyzed in AudioMuse so anchors or similarity seeds exist.

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

3. **Create a station**

   Open [http://localhost:8080/admin.html](http://localhost:8080/admin.html) and sign in with `ADMIN_USERNAME` / `ADMIN_PASSWORD`.

   Example:
   - **Name:** Yacht Rock Radio
   - **Mount:** `/yachtrock`
   - **Source type:** Song Alchemy anchor
   - **Source ref:** pick an anchor from the admin dropdown

4. **Tune in**

   - Station list: [http://localhost:8080](http://localhost:8080)
   - Live stream: [http://localhost:8000/yachtrock](http://localhost:8000/yachtrock)

New stations go live within a few seconds — no manual Liquidsoap restart.

## Other deployments

| Guide | Use when |
|-------|----------|
| [docs/UNRAID.md](docs/UNRAID.md) | Unraid server, Compose Manager, GHCR images |
| [docs/TRAEFIK.md](docs/TRAEFIK.md) | One HTTPS hostname for the site and streams |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Public internet, reverse proxy, security checklist |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Backend-only dev, project layout, contributing |

## How it works

1. Admin creates a station → backend **bootstraps a track pool** from AudioMuse.
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

## Troubleshooting

| Problem | Things to check |
|---------|-----------------|
| Admin shows **503** | Set `ADMIN_PASSWORD` in `.env` and restart the backend container |
| Station has **no tracks** / empty queue | AudioMuse URL/token in `.env`; anchor or seed exists; backend logs for AudioMuse errors |
| Backend **can't reach** AudioMuse/Navidrome on the host | On Docker Desktop use `host.docker.internal`; on Linux add `extra_hosts` or use the host LAN IP in `.env` |
| Stream **won't play** | Icecast on port `8000`; mount matches admin (e.g. `/yachtrock`); station is **On** in admin |
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
| `POST /api/admin/stations` | Create station |
| `POST /api/admin/stations/{id}/refresh-queue` | Manual queue refresh |

## Contributing

Contributions welcome — [CONTRIBUTING.md](CONTRIBUTING.md), [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Notes

- Editing a station mount or Icecast settings restarts **only that station** (~1–2s gap on that mount).
- Icecast source password in `.env` must match `icecast/icecast.xml` (default `hackme`).
- `LIQUIDSOAP_CALLBACK_SECRET` must match between backend and Liquidsoap containers.
- Queue files: the backend rewrites each `queue.m3u` from the database; after upgrading an old install, use admin **Rebuild M3U** once per station. See [docs/TECH_DEBT.md](docs/TECH_DEBT.md).

## License

MIT — see [LICENSE](LICENSE).
