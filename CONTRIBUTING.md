# Contributing to Alchemy FM

Thanks for your interest in helping. This project is radio automation (AudioMuse → queues → Liquidsoap → Icecast), not a music player — keep that model in mind when proposing changes.

## Before you start

1. Read the [README](README.md) for architecture and deployment basics.
2. Read [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for local setup and project layout.
3. Check [docs/TECH_DEBT.md](docs/TECH_DEBT.md) for known backlog items.
4. Do **not** commit `.env`, database files, or credentials.

## How to contribute

1. **Fork** the repository on GitHub.
2. **Create a branch** from `master` with a short descriptive name (e.g. `fix/admin-mobile-layout`, `docs/traefik-clarify`).
3. **Make focused changes** — one logical change per pull request when possible.
4. **Test locally** — at minimum run the stack or the backend path you touched (see Development doc).
5. **Open a pull request** against `master` and describe what changed and why.

### Pull request checklist

- [ ] Change matches existing code style in the touched files
- [ ] No secrets or personal paths in the diff
- [ ] README or `docs/` updated if behavior, config, or deployment changed
- [ ] Tests added or updated for behavior changes (`pytest` — see [docs/TESTING.md](docs/TESTING.md))
- [ ] Tested manually (describe how in the PR) when automation does not cover the change

## What we welcome

- Bug fixes with a clear reproduction path
- Documentation improvements
- Small UX polish on admin or listener pages
- Deployment guides (Traefik, Unraid, etc.) when accurate and tested

## What needs discussion first

- Large refactors or new dependencies
- Changes to streaming architecture (Liquidsoap, Icecast, queue model)
- New external service requirements (API keys, paid services)
- Breaking changes to admin API or station schema

Open an issue for these before spending time on a large PR.

## Code style (pragmatic)

- **Python:** Match surrounding files in `backend/app/` — no mandatory formatter enforced yet.
- **Web:** Static HTML/CSS/JS under `web/` — follow existing patterns; bump cache-bust query params (`?v=`) when static assets change.
- **Commits:** Clear messages in the imperative mood (e.g. `Fix queue rebuild on track start`).

## Security

Do **not** open public issues for vulnerabilities. See [SECURITY.md](SECURITY.md).

## Questions

Use [GitHub Issues](https://github.com/MMagTech/alchemyfm/issues) for bugs, ideas, and questions. The more context (version, deploy method, logs), the easier it is to help.
