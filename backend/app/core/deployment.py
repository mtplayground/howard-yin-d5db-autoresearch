from __future__ import annotations

from dataclasses import dataclass

from pydantic import SecretStr

from app.core.config import Settings


@dataclass(frozen=True)
class DeploymentCheck:
    key: str
    message: str


@dataclass(frozen=True)
class DeploymentValidation:
    errors: list[DeploymentCheck]
    warnings: list[DeploymentCheck]

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_self_hosting_settings(settings: Settings) -> DeploymentValidation:
    errors: list[DeploymentCheck] = []
    warnings: list[DeploymentCheck] = []

    database_url = settings.database_url.get_secret_value().strip()
    if not database_url:
        errors.append(DeploymentCheck("DATABASE_URL", "PostgreSQL connection string is required."))
    elif not (database_url.startswith("postgresql://") or database_url.startswith("postgresql+psycopg://")):
        errors.append(DeploymentCheck("DATABASE_URL", "DATABASE_URL must point to PostgreSQL."))

    if not _secret_value(settings.access_passphrase):
        errors.append(DeploymentCheck("ACCESS_PASSPHRASE", "Single-account console access requires a passphrase."))

    if not settings.object_storage_bucket:
        errors.append(DeploymentCheck("OBJECT_STORAGE_BUCKET", "Object storage bucket is required for papers, logs, charts, and PDFs."))

    access_key_id = _secret_value(settings.object_storage_access_key_id)
    secret_access_key = _secret_value(settings.object_storage_secret_access_key)
    if bool(access_key_id) != bool(secret_access_key):
        errors.append(
            DeploymentCheck(
                "OBJECT_STORAGE_ACCESS_KEY_ID",
                "Object storage access key id and secret access key must be configured together.",
            )
        )

    if not _secret_value(settings.model_api_key):
        warnings.append(
            DeploymentCheck(
                "MODEL_API_KEY",
                "No default model API key is set; configure one here or in the protected model settings screen before running agents.",
            )
        )

    if settings.self_url.startswith("http://") and settings.app_env == "production":
        warnings.append(DeploymentCheck("SELF_URL", "Production SELF_URL should normally use HTTPS so session cookies are secure."))

    if not settings.object_storage_prefix:
        warnings.append(DeploymentCheck("OBJECT_STORAGE_PREFIX", "Set a non-empty prefix when sharing a bucket with other apps."))

    if settings.discovery_interval_seconds < 0:
        errors.append(DeploymentCheck("DISCOVERY_INTERVAL_SECONDS", "Discovery interval must be zero or a positive number of seconds."))

    if settings.discovery_default_limit < 1:
        errors.append(DeploymentCheck("DISCOVERY_DEFAULT_LIMIT", "Discovery default limit must be at least 1."))

    return DeploymentValidation(errors=errors, warnings=warnings)


def _secret_value(value: SecretStr | None) -> str:
    if not value:
        return ""
    return value.get_secret_value().strip()
