"""Restart the Icecast Docker container from admin (optional Docker socket access)."""

from __future__ import annotations

import logging

from app.config import settings
from app.services.stream_epoch import bump_stream_epoch

logger = logging.getLogger(__name__)


def icecast_restart_enabled() -> bool:
    return settings.icecast_docker_restart


def restart_icecast_container() -> str:
    if not settings.icecast_docker_restart:
        raise RuntimeError(
            "Icecast restart from admin is disabled. Set ICECAST_DOCKER_RESTART=true "
            "and mount the Docker socket into the backend container."
        )

    try:
        import docker
        from docker.errors import DockerException
    except ImportError as exc:
        raise RuntimeError("Docker SDK is not installed on the backend.") from exc

    try:
        client = docker.from_env()
    except DockerException as exc:
        raise RuntimeError(
            "Backend cannot reach Docker. Mount /var/run/docker.sock into the backend container."
        ) from exc

    service = settings.icecast_compose_service.strip() or "icecast"
    project = settings.docker_compose_project.strip()

    matches = []
    for container in client.containers.list(all=True):
        labels = container.labels or {}
        if labels.get("com.docker.compose.service") != service:
            continue
        if project and labels.get("com.docker.compose.project") != project:
            continue
        matches.append(container)

    if not matches:
        hint = f"service={service}"
        if project:
            hint += f", project={project}"
        raise RuntimeError(f"Icecast container not found ({hint}).")

    if len(matches) > 1:
        logger.warning(
            "Multiple Icecast containers match; restarting %s",
            matches[0].name,
        )

    container = matches[0]
    container.restart(timeout=30)
    bump_stream_epoch()
    logger.info("Restarted Icecast container %s", container.name)
    return container.name
