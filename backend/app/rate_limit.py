"""Shared rate limiter for admin login and public stream endpoints.

Single-instance deployment (no multiple backend replicas behind a load
balancer), so the in-memory backend is sufficient -- no Redis needed.
"""

from fastapi import Request
from slowapi import Limiter

from app.auth import client_ip


def _rate_limit_key(request: Request) -> str:
    return client_ip(request) or "unknown"


limiter = Limiter(key_func=_rate_limit_key)
