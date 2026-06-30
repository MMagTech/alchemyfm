# Alchemy FM

Lightweight radio automation: AudioMuse supplies programming (Song Alchemy anchors and similarity), this app orchestrates station queues, Liquidsoap plays continuously, and Icecast broadcasts live streams. Navidrome provides stream URLs and cover art only.

**This is not a music player.** Listeners pick a station and tune in to a shared live broadcast.

## Architecture

```
AudioMuse ──▶ Radio Backend ──▶ queue.m3u (per station)
                    │                    │
                    │                    ▼
                    │              Liquidsoap ──▶ Icecast ──▶ Listeners
                    └── Web UI (status only)
Navidrome ──▶ stream URLs for each track
```

## Prerequisites

- Docker & Docker Compose
- [AudioMuse-AI](https://github.com/NeptuneHub/AudioMuse-AI) running (default `http://localhost:8000`)
- [Navidrome](https://www.navidrome.org/) running (default `http://localhost:4533`)
- Library analyzed in AudioMuse (for Song Alchemy anchors and similarity)

## Quick start

1. **Copy environment file**

   ```bash
   cp .env.example .env
   ```

   Edit `.env` with your AudioMuse URL/token and Navidrome credentials.

2. **Start the stack**

   ```bash
   docker compose up -d --build
   ```

3. **Create a station**

   Open [http://localhost:8080/admin.html](http://localhost:8080/admin.html) — you will be prompted for admin credentials after setting `ADMIN_PASSWORD` in `.env`.

   Example:
   - **Name:** Yacht Rock Radio
   - **Mount:** `/yachtrock`
   - **Source type:** Song Alchemy anchor
   - **Source ref:** anchor id from AudioMuse (pick from the dropdown in admin)

4. **Tune in** — the new station appears on Icecast within a few seconds; other stations are unaffected.

   - Station list: [http://localhost:8080](http://localhost:8080)
   - Live stream: [http://localhost:8000/yachtrock](http://localhost:8000/yachtrock)

## Source types

| Type | `source_ref` | Fetched from |
|------|--------------|--------------|
| `alchemy_anchor` | Anchor id | AudioMuse `POST /api/alchemy` |
| `similar_seed` | Track item_id | AudioMuse `GET /api/similar_tracks` |

Navidrome is **not** a programming source — it resolves stream URLs and album art for tracks already in the queue.

## Continuation mode (`continuation_mode`)

When a station exhausts fresh tracks from its primary source:

| Mode | Behavior |
|------|----------|
| `source_only` (default) | Reuse tracks from the imported pool. No drift. |
| `similar_to_seed` | Pull similar tracks from a fixed seed in the source pool. |
| `similar_to_last` | Pull similar tracks from whatever just played (can wander over time). |

Existing stations default to `source_only` after backend restart.

## How it works

1. Admin creates a station; **bootstrap imports tracks into a persistent station pool**.
2. The backend writes a per-station Liquidsoap script and updates a manifest.
3. The **Liquidsoap supervisor** starts one process per enabled station (polls every ~3s).
4. Each process reads `queue.m3u`; Icecast broadcasts; now playing syncs from the live stream title.
5. When the queue runs low, **tiered refill**: new programming batch (best effort) → pool reuse → anchor → similar seed → similar last (if allowed).

## API

| Endpoint | Description |
|----------|-------------|
| `GET /api/stations` | Public station list (enabled only) |
| `GET /api/stations/{slug}` | Now playing, up next, recently played |
| `GET /api/admin/stations` | All stations (admin) |
| `POST /api/admin/stations` | Create station |
| `POST /api/admin/stations/{id}/refresh-queue` | Manual queue refresh |

## Development

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8080
```

Set env vars from `.env.example`. SQLite DB defaults to `./data/radio.db` when `DATA_DIR=./data`.

## Security (public deployment)

**Right now, anyone who can reach your backend can use admin unless you set a password.**

Before sharing stations on the internet:

1. **Set `ADMIN_PASSWORD`** in `.env` (and rebuild/restart backend). Admin UI (`/admin.html`) and all `/api/admin/*` routes require HTTP Basic auth (`ADMIN_USERNAME` / `ADMIN_PASSWORD`).
2. **Do not publish port 8080 directly** — put the web UI behind a reverse proxy with HTTPS (Caddy, nginx, Traefik).
3. **Block internal webhooks at the proxy** — never expose `/internal/*` to the internet. Liquidsoap calls these from the Docker network only.
4. **Change default Icecast passwords** in `.env` and `icecast/icecast.xml` (default `hackme`).
5. **Use a strong `LIQUIDSOAP_CALLBACK_SECRET`** — it protects track-started webhooks.
6. **Keep AudioMuse and Navidrome on your LAN** — only the radio backend needs to reach them; listeners never should.

### What is public vs protected

| Path | Access |
|------|--------|
| `/`, `/station.html`, `/api/stations`, `/api/cover/*` | Public (listeners) |
| `/admin.html`, `/api/admin/*` | HTTP Basic auth (`ADMIN_PASSWORD` required) |
| `/internal/*` | Docker/LAN only + shared secret on webhooks |
| `/api/health` | Public (minimal status) |

If `ADMIN_PASSWORD` is empty, admin is **disabled** (503) so you cannot accidentally run an open admin panel.

### Reverse proxy example (Caddy)

Expose only the listener site; block admin from the public hostname if you use a separate admin subdomain, or rely on app-level Basic auth:

```caddy
radio.example.com {
    reverse_proxy backend:8080

    # Defense in depth — do not forward Liquidsoap webhooks publicly
    @internal path /internal/*
    respond @internal 404
}
```

For admin on a private hostname:

```caddy
radio-admin.example.com {
    reverse_proxy backend:8080
}
```

Set `TRUST_PROXY_HEADERS=true` on the backend when the proxy sets `X-Forwarded-For` (needed for internal-route IP filtering to see real client IPs).

### Public stream URLs (copy link / external players)

**Stream URLs follow how you opened the site** — no separate “public hostname” to configure for normal use.

| How you open the web UI | Stream URL you get |
|-------------------------|-------------------|
| `http://192.168.1.10:8080` (LAN) | `http://192.168.1.10:8000/hip_hop` |
| `http://10.0.0.5:8080` (WireGuard) | `http://10.0.0.5:8000/hip_hop` |
| `https://radio.example.com` (Traefik) | `https://radio.example.com/hip_hop` |

Rules:

1. **LAN / VPN / direct Docker ports** — same IP or hostname as the page, Icecast on `ICECAST_PUBLIC_PORT` (default `8000`).
2. **Reverse proxy** — set `TRUST_PROXY_HEADERS=true` and forward **both** the web UI and Icecast mount paths on the **same hostname**. URLs use that host + `https` automatically.
3. **`ICECAST_PUBLIC_HOST` / `ICECAST_PUBLIC_SCHEME`** — fallback only (e.g. admin API without a browser). Listeners using the website never need these.

Traefik must route mount paths (e.g. `/hip_hop`, `/melodic`) to Icecast as well as the web UI to the backend.

### Checklist

- [ ] `ADMIN_PASSWORD` set to a long random value
- [ ] `LIQUIDSOAP_CALLBACK_SECRET` and `ICECAST_SOURCE_PASSWORD` changed from defaults
- [ ] `TRUST_PROXY_HEADERS=true` when behind Traefik/Caddy/nginx
- [ ] Ports 8080/8000 not exposed on your router (proxy handles 443)
- [ ] AudioMuse / Navidrome credentials not committed to git

## Notes

- New stations go live automatically; no manual Liquidsoap restart needed.
- Editing a station's mount or Icecast settings restarts **only that station** (~1–2s gap on that mount).
- Icecast source password in `.env` must match `icecast/icecast.xml` (default `hackme`).
- `LIQUIDSOAP_CALLBACK_SECRET` must match between backend and Liquidsoap containers.
- On Windows Docker, `host.docker.internal` reaches AudioMuse/Navidrome on the host.

### Known issue: growing `queue.m3u` (address later)

**Routine queue refills append to `queue.m3u` but never remove played tracks.** Only a backend restart or admin **Rebuild M3U** rewrites the file to match the current queue. Over time Liquidsoap replays old entries, which can desync now-playing metadata and album art from the database.

Full write-up, symptoms, and proposed fixes: **[docs/TECH_DEBT.md](docs/TECH_DEBT.md)** (P1 item).

**Short-term workaround:** restart the backend container periodically, or rebuild M3U from admin after long uptimes.

## License

MIT
