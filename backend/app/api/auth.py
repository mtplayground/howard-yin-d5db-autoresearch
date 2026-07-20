from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.auth import (
    AuthConfigurationError,
    clear_session_cookie,
    has_valid_session,
    set_session_cookie,
    verify_passphrase,
)
from app.core.config import Settings, get_settings
from app.models.auth import LoginRequest, SessionResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])
SettingsDependency = Annotated[Settings, Depends(get_settings)]


@router.get("/session", response_model=SessionResponse)
async def session(request: Request, settings: SettingsDependency) -> SessionResponse:
    return SessionResponse(authenticated=has_valid_session(request, settings))


@router.post("/login", response_model=SessionResponse)
async def login(payload: LoginRequest, request: Request, settings: SettingsDependency) -> JSONResponse:
    try:
        authenticated = verify_passphrase(settings, payload.passphrase)
    except AuthConfigurationError as exc:
        raise HTTPException(status_code=503, detail="Access protection is not configured") from exc

    if not authenticated:
        raise HTTPException(status_code=401, detail="Invalid passphrase")

    response = JSONResponse(SessionResponse(authenticated=True).model_dump())
    set_session_cookie(response, request, settings)
    return response


@router.post("/logout", response_model=SessionResponse)
async def logout(settings: SettingsDependency) -> JSONResponse:
    response = JSONResponse(SessionResponse(authenticated=False).model_dump())
    clear_session_cookie(response, settings)
    return response
