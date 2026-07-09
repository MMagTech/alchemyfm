# Development guide

How to run and work on Alchemy FM locally.

## Prerequisites

- Docker & Docker Compose (full stack), **or** Python 3.12+ (backend only)
- [AudioMuse-AI](https://github.com/NeptuneHub/AudioMuse-AI) and [Navidrome](https://www.navidrome.org/) reachable from your machine
- Copy `.env.example` → `.env` and fill in credentials

## Repository layout

```
alchemyfm/
├── backend/app/          FastAPI application
│   ├── routers/          HTTP routes (public, admin, internal)
│   ├── services/         Queue, stations, Icecast, Navidrome, AudioMuse
│   └── knowledge/        Optional track trivia enrichment (feature-flagged)
├── web/                  Static listener + admin UI
│   └── static/           CSS, JS, themes
├── liquidsoap/           Per-station playback scripts + supervisor
├── icecast/              Icecast config
├── docs/                 Deployment and design notes
├── audiomuse-plugins/    AudioMuse-AI plugins (e.g. Alchemy FM Bridge)
├── docker-compose.yml    Local dev (bind-mounts ./web)
└── scripts/              Ad-hoc maintenance scripts
```

## Full stack (recommended)

```bash
cp .env.example .env
# Edit .env — AudioMuse, Navidrome, ADMIN_PASSWORD, secrets

docker compose up -d --build
```

- Listener UI: http://localhost:8080
- Admin: http://localhost:8080/admin.html
- Icecast: http://localhost:8000/{mount}

`docker-compose.yml` bind-mounts `./web` so HTML/CSS/JS edits apply without rebuilding the backend image.

## Backend only

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

mkdir -p ../data
export DATA_DIR=../data   # or set in .env
uvicorn app.main:app --reload --port 8080
```

Liquidsoap and Icecast will not run in this mode — useful for API/UI work with mocked or partial integration.

## Useful scripts

| Script | Purpose |
|--------|---------|
| `scripts/test_on_air_restart.py` | Manual check for on-air / stream epoch behavior |

There is no automated test suite yet. Validate changes by running the stack and exercising admin + a station listen flow.

## Making changes

### Backend

- Settings: `backend/app/config.py` and `.env.example`
- Database models: `backend/app/database.py` (migrations are lightweight inline alters on startup)
- New API routes: add router under `backend/app/routers/`, register in `main.py`

### Web UI

- Pages: `web/*.html`
- Shared assets: `web/static/`
- Bump `?v=` on script/style links when you change cached static files

### Liquidsoap / Icecast

- Station scripts generated at runtime under `DATA_DIR`; template in `backend/app/services/liquidsoap.py`
- Edit `liquidsoap/radio.liq` or supervisor for playback behavior changes

## Optional features

| Flag | Effect |
|------|--------|
| `KNOWLEDGE_FEATURE=true` | Enables Knowledge admin page and station trivia (see `docs/MUSIC_KNOWLEDGE_PLAN.md`) |
| `LOG_LEVEL` | Backend log verbosity (`INFO` default) |
| `LOG_MAX_BYTES` / `LOG_BACKUP_COUNT` | Rotating log at `DATA_DIR/logs/backend.log` (~20 MB cap by default) |

Requires container restart after `.env` changes.

## Logs

The backend writes to stdout (visible via `docker compose logs backend`) and to a persistent rotating file:

- **Path:** `DATA_DIR/logs/backend.log` (default `/data/logs/backend.log` in Docker)
- **Rotation:** when the file exceeds `LOG_MAX_BYTES`, it rolls to `.1`, `.2`, … up to `LOG_BACKUP_COUNT` backups
- **Local dev:** with `DATA_DIR=../data`, logs land in `../data/logs/backend.log`

Liquidsoap and Icecast logs remain in their container stdout / internal log files unless you configure Docker log rotation separately.

## AudioMuse plugin (Channel Designer)

The [`audiomuse-plugins/`](../audiomuse-plugins/) folder ships an [AudioMuse-AI plugin](https://github.com/NeptuneHub/AudioMuse-AI/blob/main/docs/PLUGIN.md) — **Alchemy FM Channel Designer** (v2). It uses AudioMuse intelligence (CLAP, lyrics, mood centroids, Song Alchemy) to preview programming, then deploys live stations to Alchemy FM via the admin API. No Alchemy FM backend changes are required for v2.

See [audiomuse-plugins/README.md](../audiomuse-plugins/README.md) for install and workflow.

## CI

Pushes to `master` build and publish Docker images to GHCR (`.github/workflows/docker-publish.yml`). There is no lint/test workflow yet — run manual checks before opening a PR.

## Contributing

See [CONTRIBUTING.md](../CONTRIBUTING.md).
