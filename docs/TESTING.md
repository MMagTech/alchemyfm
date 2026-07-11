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
| `backend/tests/test_stations_api.py` | SQLite | None | Health + stations API |

## Adding tests for a new feature

1. Add fixture JSON under `audiomuse-plugins/alchemy_fm_bridge/tests/fixtures/` if the feature calls a new AudioMuse endpoint.
2. Add a test in the appropriate file proving the happy path produces results (tracks in preview, rows in DB, expected HTTP status).
3. Add an error-path test when user input or API failure is possible.
4. Run `pytest` locally before opening a PR — CI must pass.

## CI

Workflow: [`.github/workflows/test.yml`](../.github/workflows/test.yml)

- **plugin-tests** job: Postgres service container + plugin suite
- **backend-tests** job: SQLite in-memory + backend suite

Plugin-only changes no longer ship without automated validation.
