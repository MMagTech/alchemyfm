# Security Policy

## Supported versions

Security fixes are applied on the `master` branch. Docker images tagged `latest` on GHCR are rebuilt from `master` after fixes land.

There is no formal LTS release line yet. Deploy from current `master` or the latest published images.

## Reporting a vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Report them through GitHub's **private vulnerability reporting**:

**https://github.com/MMagTech/alchemyfm/security/advisories/new**

Include:

- What you found and where (file, endpoint, or deployment scenario)
- Steps to reproduce
- Impact (e.g. unauthenticated admin access, SSRF, credential leak)
- Your environment (Docker Compose, Unraid, Traefik, etc.) if relevant

We will acknowledge reports as soon as we can and work on a fix before public disclosure when appropriate.

## Deployment reminders

Operators are responsible for:

- Setting a strong `ADMIN_PASSWORD` before exposing the site
- Not publishing `/internal/*` to the internet
- Changing default Icecast and Liquidsoap secrets
- Keeping AudioMuse and Navidrome on a trusted network

Full deployment and public-exposure checklist: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
