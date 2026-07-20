from pydantic import BaseModel, ConfigDict, Field


class ModelSettingsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    base_url: str | None
    default_model: str
    api_key_configured: bool


class ModelSettingsUpdate(BaseModel):
    provider: str | None = Field(default=None, min_length=1, max_length=64)
    base_url: str | None = Field(default=None, max_length=512)
    default_model: str | None = Field(default=None, min_length=1, max_length=160)
    api_key: str | None = Field(default=None, min_length=1, max_length=4096)
    clear_api_key: bool = False
