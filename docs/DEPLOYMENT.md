# Deployment & public exposure

Use this guide when Alchemy FM is reachable beyond your LAN, or when you want Traefik/Unraid-specific setup. For local Docker quick start, see the [README](../README.md).

## Other install paths

| Method | When to use |
|--------|-------------|
| [UNRAID.md](UNRAID.md) | Unraid + Compose Manager, GHCR images |
| [TRAEFIK.md](TRAEFIK.md) | Single hostname for web UI and stream mounts |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Build from source, bind-mount `./web` |
| [unraid/templates-user/](../unraid/templates-user/) | Optional Docker Man XML templates (icecast → backend → liquidsoap) |

## Security before going public

**Anyone who can reach your backend can use admin unless you set a password.**

### Checklist

- [ ] `ADMIN_PASSWORD` set to a long random value
- [ ] `LIQUIDSOAP_CALLBACK_SECRET` and `ICECAST_SOURCE_PASSWORD` changed from defaults
- [ ] `TRUST_PROXY_HEADERS=true` when behind Traefik/Caddy/nginx
- [ ] Ports 8080/8000 not exposed on your router (proxy handles 443)
- [ ] AudioMuse / Navidrome credentials not committed to git

### Required steps

1. **Set `ADMIN_PASSWORD`** in `.env` (and rebuild/restart backend). Admin UI (`/admin.html`) and all `/api/admin/*` routes require HTTP Basic auth (`ADMIN_USERNAME` / `ADMIN_PASSWORD`).
2. **Do not publish port 8080 directly** — put the web UI behind a reverse proxy with HTTPS (Caddy, nginx, Traefik).
3. **Block internal webhooks at the proxy** — never expose `/internal/*` to the internet. Liquidsoap calls these from the Docker network only.
4. **Change default Icecast passwords** in `.env` and `icecast/icecast.xml` (default `hackme`).
5. **Use a strong `LIQUIDSOAP_CALLBACK_SECRET`** — it protects track-started webhooks.
6. **Keep AudioMuse and Navidrome on your LAN** — only the radio backend needs to reach them; listeners never should.

If `ADMIN_PASSWORD` is empty, admin is **disabled** (503) so you cannot accidentally run an open admin panel.

### What is public vs protected

| Path | Access |
|------|--------|
| `/`, `/station.html`, `/api/stations`, `/api/cover/*` | Public (listeners) |
| `/admin.html`, `/api/admin/*` | HTTP Basic auth (`ADMIN_PASSWORD` required) |
| `/internal/*` | Docker/LAN only + shared secret on webhooks |
| `/api/health` | Public (minimal status) |

## Reverse proxy example (Caddy)

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

## Public stream URLs (copy link / external players)

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

## Vulnerability reports

Do not open public issues for security bugs. See [SECURITY.md](../SECURITY.md).
