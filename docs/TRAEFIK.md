# Traefik reverse proxy (docker-compose.traefik.yml)

Put Alchemy FM behind Traefik on **one public hostname** so listeners get stream URLs like `https://radio.example.com/rock` — the same host and TLS as the website.

## What you need

- Traefik already running (Unraid **Traefik** plugin, LinuxServer, or your own stack)
- A Docker **external network** named `proxy` (or set `TRAEFIK_NETWORK` if yours differs) — Traefik and all three Alchemy FM containers share it; nothing else is created
- DNS: `ALCHEMYFM_HOST` → your Traefik public IP
- TLS cert resolver **`cloudflare`** on entrypoint **`websecure`** (same as Subwave / mmagtech Traefik)

## Quick start (Unraid + Compose Manager)

1. Create appdata and `.env` (see [UNRAID.md](UNRAID.md)) and add:

```env
ALCHEMYFM_HOST=radio.example.com
TRUST_PROXY_HEADERS=true
ADMIN_PASSWORD=your-long-password
# ... AudioMuse, Navidrome, secrets ...
```

Labels are baked into `docker-compose.traefik.yml` (proxy network, `websecure`, `cloudflare`, `noindex@file,crowdsec@file`) — same pattern as Subwave.

2. In **Compose Manager**, add stack `alchemyfm` using `docker-compose.traefik.yml` (copy to `/mnt/user/appdata/alchemyfm/` or paste from the repo).

3. **Pull** and **Up**. Do **not** publish host ports `8080`/`8000` on the router — Traefik handles `443`.

4. Open `https://radio.example.com` and verify a station’s **Copy stream link** uses `https://radio.example.com/yourmount`.

## How routing works

| Traffic | Routed to | Rule |
|---------|-----------|------|
| Web UI, API, static files | `backend:8080` | `Host(ALCHEMYFM_HOST)` (priority 10) |
| Live streams | `icecast:8000` | Same host + path matching `^/[mount](.xspf)?$` (priority 100) |

Stream paths win over the catch-all web router when they look like a **single path segment** (letters, numbers, `_`, `-`), which matches default station mounts from admin (e.g. `/rock`, `/hip_hop`).

`liquidsoap` stays on the same **`proxy`** network as Traefik (no extra Docker network is created).

## Match your Traefik install

Only **`ALCHEMYFM_HOST`** is required in `.env`. To change labels (middleware, cert resolver, etc.), edit `docker-compose.traefik.yml` directly — same as your other stacks.

| Label | Value |
|-------|--------|
| Network | `proxy` |
| Entrypoint | `websecure` |
| Cert resolver | `cloudflare` |
| Middlewares | `noindex@file,crowdsec@file` |
| Web port | `8080` (backend) |
| Stream port | `8000` (icecast) |

## Security checklist

- [ ] `ADMIN_PASSWORD` set
- [ ] `TRUST_PROXY_HEADERS=true`
- [ ] Ports **8080** and **8000** not forwarded on your router
- [ ] `/internal/*` never exposed (Liquidsoap calls `http://backend:8080` on the `proxy` network only)
- [ ] Change `ICECAST_SOURCE_PASSWORD` and `LIQUIDSOAP_CALLBACK_SECRET` from defaults

Optional defense in depth: add a Traefik router rule or middleware to return 404 for `PathPrefix(`/internal`)` on the public hostname.

## Custom mount paths

If a station mount contains a **dot** or **extra slashes** (e.g. `/live.mp3`, `/radio/rock`), the default PathRegexp will not route it to Icecast. Options:

1. Prefer simple mounts (`rock`, `hip_hop`, `melodic`) — recommended.
2. Add extra Traefik labels on `alchemyfm-icecast` for that mount, e.g.  
   `traefik.http.routers.alchemyfm-streams.rule=Host(\`radio.example.com\`) && (PathPrefix(\`/live.mp3\`) || PathRegexp(\`^/[a-zA-Z0-9_-]+(\\.xspf)?$\`))`

## Terminal deploy

```bash
cd /mnt/user/appdata/alchemyfm
docker compose --env-file .env -f docker-compose.traefik.yml pull
docker compose --env-file .env -f docker-compose.traefik.yml up -d
```

## LAN-only testing (no Traefik)

Use `docker-compose.unraid.yml` instead — publishes `8080` and `8000` on the host.

## Docker Man XML templates

The per-container XML templates in [unraid/templates-user/](../unraid/templates-user/) are for direct port publishing. For Traefik, **Compose Manager + `docker-compose.traefik.yml`** is the supported path (labels are too long and host-specific for a generic CA XML).
