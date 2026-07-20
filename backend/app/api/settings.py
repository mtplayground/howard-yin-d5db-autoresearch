from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db_session
from app.models.settings import ModelSettingsResponse, ModelSettingsUpdate
from app.services.model_settings import (
    ModelCredentialsError,
    ModelSettingsError,
    ModelSettingsPatch,
    load_effective_model_settings,
    save_model_settings,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])
SettingsDependency = Annotated[Settings, Depends(get_settings)]
DatabaseDependency = Annotated[Session, Depends(get_db_session)]


@router.get("/model", response_model=ModelSettingsResponse)
async def read_model_settings(db: DatabaseDependency, settings: SettingsDependency) -> ModelSettingsResponse:
    effective = load_effective_model_settings(db, settings)
    return ModelSettingsResponse(
        provider=effective.provider,
        base_url=effective.base_url,
        default_model=effective.default_model,
        api_key_configured=effective.api_key_configured,
    )


@router.put("/model", response_model=ModelSettingsResponse)
async def update_model_settings(
    payload: ModelSettingsUpdate,
    db: DatabaseDependency,
    settings: SettingsDependency,
) -> ModelSettingsResponse:
    try:
        effective = save_model_settings(
            db,
            settings,
            ModelSettingsPatch(
                provider=payload.provider,
                base_url=payload.base_url,
                default_model=payload.default_model,
                api_key=payload.api_key,
                clear_api_key=payload.clear_api_key,
            ),
        )
    except ModelCredentialsError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ModelSettingsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ModelSettingsResponse(
        provider=effective.provider,
        base_url=effective.base_url,
        default_model=effective.default_model,
        api_key_configured=effective.api_key_configured,
    )
