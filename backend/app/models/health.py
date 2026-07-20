from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    environment: str
    database_configured: bool


class AppConfigResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    environment: str
    public_origin: str
    model_provider: str

