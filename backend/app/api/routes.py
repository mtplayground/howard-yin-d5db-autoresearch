from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.core.config import Settings, get_settings
from app.models.health import AppConfigResponse, HealthResponse

router = APIRouter(prefix="/api")
SettingsDependency = Annotated[Settings, Depends(get_settings)]


@router.get("/health", response_model=HealthResponse)
async def health(settings: SettingsDependency) -> HealthResponse:
    return HealthResponse(
        status="ok",
        environment=settings.app_env,
        database_configured=bool(settings.database_url),
    )


@router.get("/config", response_model=AppConfigResponse)
async def public_config(request: Request, settings: SettingsDependency) -> AppConfigResponse:
    forwarded_host = request.headers.get("x-forwarded-host")
    forwarded_proto = request.headers.get("x-forwarded-proto", "https")
    origin = settings.self_url
    if forwarded_host:
        origin = f"{forwarded_proto}://{forwarded_host}"

    return AppConfigResponse(
        environment=settings.app_env,
        public_origin=origin,
        model_provider=settings.model_provider,
    )
