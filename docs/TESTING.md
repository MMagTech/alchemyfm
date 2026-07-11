# Testing

Automated tests run in GitHub Actions on every pull request. No live AudioMuse or Alchemy FM instance is required — external APIs are mocked and databases use test fixtures.

## Quick start

### Plugin tests (Channel Designer)

Requires PostgreSQL (plugin SQL is Postgres-specific).

```bash
# Start test database
docker compose -f docker-compose.test.yml up -d

export TEST_DATABASE_URL=postgresql://postgres:test@localhost:5433/postgres
pip install -r audiomuse-plugins/alchemy_fm_bridge/tests/requirements.txt
pytest audiomuse-plugins/alchemy_fm_bridge/tests -v
```

### Backend tests (Alchemy FM API)

Uses SQLite in-memory — no Docker required.

```bash
pip install -r backend/requirements.txt -r backend/requirements-dev.txt
pytest backend/tests -v
```

### Run everything

```bash
export TEST_DATABASE_URL=postgresql://postgres:test@localhost:5433/postgres
pip install -r backend/requirements.txt -r backend/requirements-dev.txt
pip install -r audiomuse-plugins/alchemy_fm_bridge/tests/requirements.txt
pytest -v
```

## What is tested

| Suite | Database | External APIs | Coverage |
|-------|------------|---------------|----------|
| `audiomuse-plugins/.../tests/test_core.py` | None | None | Form parsing, helpers, flash anchors |
| `.../test_preview.py` | Postgres | Mocked AudioMuse | All programming types, preview route, regressions |
| `.../test_persistence.py` | Postgres | None | Save/load, pool, auditions, errors |
| `backend/tests/test_audiomuse_client.py` | None | Mocked HTTP | Refill source parsing |
| `backend/tests/test_stations_api.py` | SQLite | None | Health + public stations API |
| `backend/tests/test_admin_stations_api.py` | SQLite | Mocked bootstrap | Admin CRUD, bootstrap, refresh, rebuild, artwork |
| `backend/tests/test_internal_api.py` | SQLite | None | Liquidsoap track-started callback + IP guard |
| `backend/tests/test_listen_proxy.py` | SQLite | None | HEAD listen probe + listen.m3u |
| `backend/tests/test_admin_navidrome_api.py` | SQLite | Mocked Navidrome | Operator heart POST (heart on/off); retired GET prefetch stays gone |
| `backend/tests/test_retired_routes.py` | SQLite | None | Removed AudioMuse admin proxy returns 404 |
| `.../test_deploy.py` | None | Mocked Alchemy FM HTTP | Plugin deploy payload + `AlchemyFmClient` admin API contract |
| `.../test_deploy_route.py` | Postgres | Mocked AudioMuse + Alchemy | **Step 6 form POST `action=push`** — full deploy handler path |
| `backend/tests/test_plugin_deploy_contract.py` | SQLite | Mocked bootstrap | **Plugin payload → backend create + bootstrap** for all programming types |

## Adding tests for a new feature

1. Add fixture JSON under `audiomuse-plugins/alchemy_fm_bridge/tests/fixtures/` if the feature calls a new AudioMuse endpoint.
2. Add a test in the appropriate file proving the happy path produces results (tracks in preview, rows in DB, expected HTTP status).
3. Add an error-path test when user input or API failure is possible.
4. Run `pytest` locally before opening a PR — CI must pass.

## Integration contracts (dead-code safety net)

These tests guard paths that are **not used by the web UI** but are required for production:

| Test file | Protects |
|-----------|----------|
| `test_admin_stations_api.py` | Channel Designer deploy: create/update/delete, bootstrap, refresh, rebuild M3U, artwork |
| `test_internal_api.py` | Liquidsoap `track-started` callback + `/internal` IP guard |
| `test_listen_proxy.py` | Safari/iOS `HEAD /listen` probe (no upstream Icecast connect) |
| `test_deploy.py` | Plugin `AlchemyFmClient` → admin API URL contract |
| `test_deploy_route.py` | Plugin Step 6 deploy button (`action=push`) through form handler |
| `test_plugin_deploy_contract.py` | Real `channel_profile_to_alchemy_payload` accepted by backend + bootstrap |

Removing any of those routes or changing plugin deploy URLs should fail CI. Tier-1 dead code (unused helpers, legacy CSS) is still not covered — that is intentional.

Backend tests pin admin credentials in `backend/tests/conftest.py` so a developer `.env` does not affect CI or local pytest.

## CI

Workflows:

| Workflow | Trigger | What it does |
|----------|---------|--------------|
| [`.github/workflows/test.yml`](../.github/workflows/test.yml) | PR + push to `master` | Plugin + backend pytest |
| same — **release-plugin** job | push to `master` after tests pass | Rebuilds `alchemy_fm_bridge.zip`, bumps `plugin.json` + `PLUGIN_VERSION` if source changed, commits back to `master` |
| [`.github/workflows/docker-publish.yml`](../.github/workflows/docker-publish.yml) | After **Test** succeeds on `master`, tags `v*`, or manual | Publishes backend/liquidsoap/icecast Docker images to GHCR (skips when only plugin files changed) |

**Order on push to `master`:** Test → (if green) release-plugin + Publish Docker images.

Docker images are **not** rebuilt for plugin-only commits. The plugin catalog zip **is** updated automatically when `__init__.py` changes.

Local plugin release (same script as CI):

```bash
python scripts/release_plugin.py "Your changelog sentence."
```

Plugin + backend jobs:

- **plugin-tests** job: Postgres service container + plugin suite
- **backend-tests** job: SQLite in-memory + backend suite

Plugin-only changes no longer ship without automated validation.
