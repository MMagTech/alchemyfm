"""Admin authentication and internal-route guards."""

import hashlib
import hmac
import logging
import secrets
import time
from ipaddress import ip_address, ip_network

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import settings

logger = logging.getLogger(__name__)

security = HTTPBasic(auto_error=False)

SESSION_COOKIE = "admin_session"
SESSION_MAX_AGE = 7 * 24 * 3600

_TRUSTED_NETWORKS = tuple(
    ip_network(cidr)
    for cidr in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "127.0.0.0/8",
        "::1/128",
        "fc00::/7",
    )
)


def admin_auth_enabled() -> bool:
    return bool(settings.admin_password)


def _session_key() -> bytes:
    secret = settings.liquidsoap_callback_secret or settings.admin_password
    return secret.encode("utf-8")


def validate_admin_credentials(username: str, password: str) -> bool:
    user_ok = secrets.compare_digest(
        username.encode("utf-8"),
        settings.admin_username.encode("utf-8"),
    )
    pass_ok = secrets.compare_digest(
        password.encode("utf-8"),
        settings.admin_password.encode("utf-8"),
    )
    return user_ok and pass_ok


def create_session_token(username: str) -> str:
    expiry = int(time.time()) + SESSION_MAX_AGE
    payload = f"{username}:{expiry}"
    sig = hmac.new(_session_key(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def verify_session_token(token: str) -> str | None:
    try:
        username, expiry, sig = token.rsplit(":", 2)
        payload = f"{username}:{expiry}"
        expected = hmac.new(_session_key(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        if int(expiry) < time.time():
            return None
        return username
    except (ValueError, TypeError):
        return None


def admin_user_from_request(request: Request) -> str | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return verify_session_token(token)


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE, path="/")


def require_admin(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(security),
) -> str:
    if not admin_auth_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin is disabled. Set ADMIN_PASSWORD in your environment.",
        )

    session_user = admin_user_from_request(request)
    if session_user:
        return session_user

    if credentials is not None and validate_admin_credentials(
        credentials.username, credentials.password
    ):
        return credentials.username

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
    )


def _client_ip(request: Request) -> str | None:
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if forwarded:
            return forwarded
        real_ip = request.headers.get("x-real-ip", "").strip()
        if real_ip:
            return real_ip
    if request.client:
        return request.client.host
    return None


def require_internal_client(request: Request) -> None:
    """Block /internal from the public internet when enabled (defense in depth)."""
    if not settings.restrict_internal_routes:
        return
    raw = _client_ip(request)
    if not raw:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    try:
        ip = ip_address(raw)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden") from None
    if not any(ip in net for net in _TRUSTED_NETWORKS):
        logger.warning("Rejected /internal request from %s", raw)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
