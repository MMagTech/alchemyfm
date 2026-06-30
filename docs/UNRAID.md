# Alchemy FM on Unraid

Three containers: **backend** (web UI + API), **liquidsoap** (playback), **icecast** (broadcast). Images are published to GitHub Container Registry on every push to `master`.

## Prerequisites

- Unraid 6.12+ with **Docker** enabled
- **Compose Manager** plugin (recommended) — or run `docker compose` from the Unraid terminal
- [AudioMuse](https://github.com/NeptuneHub/AudioMuse-AI) and [Navidrome](https://www.navidrome.org/) reachable from the host (same server or LAN IP)

## 1. Appdata folder

```bash
mkdir -p /mnt/user/appdata/alchemyfm/data
```

## 2. Environment file

```bash
cp /path/to/alchemyfm/.env.example /mnt/user/appdata/alchemyfm/.env
nano /mnt/user/appdata/alchemyfm/.env
```

Set at minimum:

- `ADMIN_PASSWORD`
- `AUDIOMUSE_URL` / `AUDIOMUSE_API_TOKEN`
- `NAVIDROME_URL` / `NAVIDROME_USER` / `NAVIDROME_PASSWORD`
- `ICECAST_PUBLIC_HOST` — your Unraid LAN IP or public hostname listeners use
- `LIQUIDSOAP_CALLBACK_SECRET` — long random string

If AudioMuse or Navidrome run as **other Docker containers on Unraid**, use the host IP and published port (e.g. `http://192.168.1.10:8387`), not container names, unless they share a custom network with Alchemy FM.

## 3. Log in to GHCR (private repo)

The repo is private, so Unraid must authenticate before pulling images:

1. GitHub → **Settings → Developer settings → Personal access tokens** → fine-grained token with **read:packages**
2. On Unraid terminal:

```bash
docker login ghcr.io -u YOUR_GITHUB_USERNAME
```

Paste the token as the password.

## 4. Deploy with Compose Manager

1. Install **Compose Manager** from Community Applications
2. **Add new stack**
   - Name: `alchemyfm`
   - Compose file: paste URL or copy locally:

     `https://raw.githubusercontent.com/MMagTech/alchemyfm/master/docker-compose.unraid.yml`

   - Working directory / env file: `/mnt/user/appdata/alchemyfm/.env`
3. **Pull** and **Up** the stack

Or from terminal:

```bash
cd /mnt/user/appdata/alchemyfm
curl -fsSL -o docker-compose.unraid.yml \
  https://raw.githubusercontent.com/MMagTech/alchemyfm/master/docker-compose.unraid.yml
docker compose --env-file .env -f docker-compose.unraid.yml pull
docker compose --env-file .env -f docker-compose.unraid.yml up -d
```

## 5. Open the UI

- Station list: `http://YOUR_UNRAID_IP:8080`
- Admin: `http://YOUR_UNRAID_IP:8080/admin.html`
- Streams: `http://YOUR_UNRAID_IP:8000/yourmount`

## Ports

| Port | Service |
|------|---------|
| 8080 | Web UI + API (`BACKEND_PORT`) |
| 8000 | Icecast streams (`ICECAST_PORT`) |

## Updates

Compose Manager → stack → **Pull** → **Up**, or:

```bash
docker compose --env-file /mnt/user/appdata/alchemyfm/.env \
  -f /mnt/user/appdata/alchemyfm/docker-compose.unraid.yml pull
docker compose --env-file /mnt/user/appdata/alchemyfm/.env \
  -f /mnt/user/appdata/alchemyfm/docker-compose.unraid.yml up -d
```

## Icecast restart from admin

`docker-compose.unraid.yml` mounts `/var/run/docker.sock` into the backend so **Restart Icecast** in admin works. `DOCKER_COMPOSE_PROJECT` is set to `alchemyfm` to match the stack name.

## Build locally instead (optional)

If you prefer building on Unraid instead of GHCR:

```bash
git clone https://github.com/MMagTech/alchemyfm.git /mnt/user/appdata/alchemyfm/src
cd /mnt/user/appdata/alchemyfm/src
cp .env.example /mnt/user/appdata/alchemyfm/.env
# edit .env, then:
docker compose --env-file /mnt/user/appdata/alchemyfm/.env up -d --build
```

Use the root `docker-compose.yml` for local builds (bind-mounts `./web` for live edits).

## Community Applications template (optional)

A single-container CA template does not fit this stack. Use **Compose Manager** or add a custom template repo entry pointing at `docker-compose.unraid.yml` (see [MMagTech/unraid-templates](https://github.com/MMagTech/unraid-templates)).
