"""Deploy dependency check — AudioMuse + Navidrome reachability from Alchemy FM."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings


@pytest.mark.asyncio
async def test_deploy_check_ok():
    from app.services.deploy_check import check_deploy_dependencies

    with (
        patch.object(settings, "navidrome_user", "admin"),
        patch.object(settings, "navidrome_password", "secret"),
        patch("app.services.deploy_check.httpx.AsyncClient") as mock_client_cls,
        patch(
            "app.services.deploy_check.navidrome_client._request",
            new=AsyncMock(return_value={"status": "ok"}),
        ),
    ):
        mock_client = mock_client_cls.return_value.__aenter__.return_value
        mock_response = mock_client.get.return_value
        mock_response.raise_for_status = lambda: None

        result = await check_deploy_dependencies()

    assert result["ok"] is True
    assert result["audiomuse"]["ok"] is True
    assert result["navidrome"]["ok"] is True


@pytest.mark.asyncio
async def test_deploy_check_audiomuse_connect_error():
    import httpx
    from app.services.deploy_check import check_deploy_dependencies

    with patch("app.services.deploy_check.httpx.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value.__aenter__.return_value
        mock_client.get.side_effect = httpx.ConnectError("Connection refused")

        result = await check_deploy_dependencies()

    assert result["ok"] is False
    assert result["audiomuse"]["ok"] is False
    assert "Cannot connect to AudioMuse" in result["audiomuse"]["error"]


@pytest.mark.asyncio
async def test_deploy_check_audiomuse_unauthorized():
    import httpx
    from app.services.deploy_check import check_deploy_dependencies

    with (
        patch.object(settings, "audiomuse_url", "http://192.168.1.10:8387"),
        patch.object(settings, "navidrome_url", "http://192.168.1.10:4533"),
        patch.object(settings, "audiomuse_api_token", ""),
        patch.object(settings, "navidrome_user", "admin"),
        patch.object(settings, "navidrome_password", "secret"),
        patch("app.services.deploy_check.httpx.AsyncClient") as mock_client_cls,
        patch(
            "app.services.deploy_check.navidrome_client._request",
            new=AsyncMock(return_value={"status": "ok"}),
        ),
    ):
        mock_client = mock_client_cls.return_value.__aenter__.return_value
        mock_response = mock_client.get.return_value
        mock_response.status_code = 401
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Unauthorized",
            request=httpx.Request("GET", "http://test/api/mood_centroids"),
            response=mock_response,
        )

        result = await check_deploy_dependencies()

    assert result["ok"] is False
    assert result["audiomuse"]["ok"] is False
    assert "AUDIOMUSE_API_TOKEN" in result["audiomuse"]["error"]


def test_admin_deploy_check_endpoint(admin_client):
    with (
        patch(
            "app.routers.admin_auth.check_deploy_dependencies",
            new=AsyncMock(
                return_value={
                    "ok": True,
                    "audiomuse": {"url": "http://test", "ok": True, "error": ""},
                    "navidrome": {"url": "http://test", "ok": True, "error": ""},
                }
            ),
        ),
    ):
        resp = admin_client.get("/api/admin/deploy-check")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
