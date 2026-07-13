from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.auth import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    admin_auth_enabled,
    clear_session_cookie,
    create_session_token,
    require_admin,
    set_session_cookie,
    validate_admin_credentials,
)
from app.rate_limit import limiter
from app.services.deploy_check import check_deploy_dependencies

router = APIRouter(prefix="/api/admin", tags=["admin"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=500)


class LoginResponse(BaseModel):
    ok: bool = True
    username: str


@router.post("/login", response_model=LoginResponse)
@limiter.limit("5/15minute")
def admin_login(request: Request, payload: LoginRequest, response: Response):
    if not admin_auth_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin is disabled. Set ADMIN_PASSWORD in your environment.",
        )
    if not validate_admin_credentials(payload.username, payload.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    token = create_session_token(payload.username)
    set_session_cookie(response, token)
    return LoginResponse(username=payload.username)


@router.post("/logout")
def admin_logout(response: Response, _: str = Depends(require_admin)):
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/me")
def admin_me(username: str = Depends(require_admin)):
    return {"username": username, "session_max_age_sec": SESSION_MAX_AGE}


@router.get("/deploy-check")
async def admin_deploy_check(_: str = Depends(require_admin)):
    """Verify AudioMuse + Navidrome — required for plugin deploy/bootstrap."""
    return await check_deploy_dependencies()
