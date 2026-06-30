"""Build listener-facing stream URLs from the incoming HTTP request when possible."""

from __future__ import annotations

from fastapi import Request

from app.config import settings
from app.database import Station


def _normalize_mount(mount: str) -> str:
    return mount if mount.startswith("/") else f"/{mount}"


def _first_header(request: Request, name: str) -> str:
    return (request.headers.get(name) or "").split(",")[0].strip()


def _with_port(scheme: str, host: str, port: int) -> str:
    default = 443 if scheme == "https" else 80
    if port == default:
        return host
    if ":" in host and not host.startswith("["):
        host = host.rsplit(":", 1)[0]
    return f"{host}:{port}"


def stream_url_from_request(request: Request, mount: str) -> str | None:
    """
    Derive a stream URL from how the client reached the web UI.

    - Behind a reverse proxy (Traefik, Caddy, …): same host + TLS as the page,
      mount path on that host (proxy must forward Icecast mounts).
    - Direct LAN / WireGuard / Docker port publish: same IP/hostname, Icecast port.
    """
    mount = _normalize_mount(mount)

    if settings.trust_proxy_headers:
        host = _first_header(request, "x-forwarded-host")
        if host:
            proto = _first_header(request, "x-forwarded-proto") or "https"
            scheme = proto if proto in ("http", "https") else "https"
            return f"{scheme}://{host}{mount}"

    hostname = request.url.hostname
    if not hostname:
        return None

    scheme = request.url.scheme or "http"
    ice_port = settings.icecast_public_port
    web_port = request.url.port
    if web_port is None:
        web_port = 443 if scheme == "https" else 80

    if web_port == ice_port:
        return f"{scheme}://{hostname}{mount}"

    host = _with_port(scheme, hostname, ice_port)
    return f"{scheme}://{host}{mount}"


def public_stream_url(station: Station, request: Request | None = None) -> str:
    mount = _normalize_mount(station.icecast_mount)
    if request is not None:
        derived = stream_url_from_request(request, mount)
        if derived:
            return derived
    return settings.stream_public_url(mount)
