from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = "development"
    host: str = "0.0.0.0"
    port: int = 8080
    self_url: str = "http://localhost:8080"
    allowed_cors_origin: str | None = None

    database_url: SecretStr = Field(..., description="PostgreSQL connection string")

    access_passphrase: SecretStr | None = None
    access_session_cookie_name: str = "single_account_session"
    access_session_max_age_seconds: int = 60 * 60 * 24 * 7
    mctai_auth_url: str | None = None
    mctai_auth_app_token: SecretStr | None = None
    mctai_auth_jwks_url: str | None = None

    mctai_email_url: str | None = None
    mctai_email_app_token: SecretStr | None = None

    model_provider: str = "openai"
    model_base_url: str | None = None
    model_api_key: SecretStr | None = None
    model_default_model: str = "gpt-4.1-mini"
    model_request_timeout_seconds: float = 60.0

    object_storage_endpoint: str | None = None
    object_storage_region: str = "auto"
    object_storage_bucket: str | None = None
    object_storage_access_key_id: SecretStr | None = None
    object_storage_secret_access_key: SecretStr | None = None
    object_storage_prefix: str = ""

    arxiv_api_url: str = "https://export.arxiv.org/api/query"
    semantic_scholar_api_url: str = "https://api.semanticscholar.org/graph/v1"
    github_api_url: str = "https://api.github.com"
    papers_with_code_api_url: str = "https://paperswithcode.com/api/v1"
    source_connectors_enabled: str = "arxiv,semantic_scholar,github,papers_with_code"
    source_request_timeout_seconds: float = 30.0
    source_min_interval_seconds: float = 1.0
    source_user_agent: str = "autoresearch/0.1"
    semantic_scholar_api_key: SecretStr | None = None
    openalex_email: str | None = None
    github_token: SecretStr | None = None

    discovery_default_query: str = "machine learning"
    discovery_default_limit: int = 10
    discovery_interval_seconds: int = 0


@lru_cache
def get_settings() -> Settings:
    return Settings()
