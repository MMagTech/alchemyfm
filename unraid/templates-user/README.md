# Local Unraid templates (Community Applications)

Copy these XML files into Unraid’s user template folder:

```bash
cp my-alchemyfm-*.xml /boot/config/plugins/dockerMan/templates-user/
```

Then **Docker → Add Container** — search for `alchemyfm`.

## Before install

1. Log in to GHCR (private images):

   ```bash
   docker login ghcr.io -u YOUR_GITHUB_USERNAME
   ```

   Use a GitHub PAT with **read:packages**.

2. Create appdata:

   ```bash
   mkdir -p /mnt/user/appdata/alchemyfm/data
   ```

## Install order

1. **alchemyfm-icecast**
2. **alchemyfm-backend** — set `ADMIN_PASSWORD`, AudioMuse/Navidrome URLs, `ICECAST_PUBLIC_HOST`, `LIQUIDSOAP_CALLBACK_SECRET`
3. **alchemyfm-liquidsoap** — same **Data Path** as backend

When asked for network, use **`alchemyfm`** for all three (create if new).

## Easier alternative: Compose Manager

If you use the **Compose Manager** plugin, you do not need these XML files.

| Mode | Compose file |
|------|----------------|
| Direct ports (LAN) | `docker-compose.unraid.yml` — [UNRAID.md](../../docs/UNRAID.md) |
| Behind Traefik | `docker-compose.traefik.yml` — [TRAEFIK.md](../../docs/TRAEFIK.md) |
