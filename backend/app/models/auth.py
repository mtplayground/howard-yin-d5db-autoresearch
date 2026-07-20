from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    passphrase: str = Field(min_length=1, max_length=512)


class SessionResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    authenticated: bool
