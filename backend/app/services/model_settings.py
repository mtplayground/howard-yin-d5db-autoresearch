from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import ModelSettings


class ModelSettingsError(RuntimeError):
    pass


class ModelCredentialsError(ModelSettingsError):
    pass


@dataclass(frozen=True)
class EffectiveModelSettings:
    provider: str
    base_url: str | None
    default_model: str
    api_key: str | None

    @property
    def api_key_configured(self) -> bool:
        return bool(self.api_key)


@dataclass(frozen=True)
class ModelSettingsPatch:
    provider: str | None = None
    base_url: str | None = None
    default_model: str | None = None
    api_key: str | None = None
    clear_api_key: bool = False


def load_effective_model_settings(db: Session, settings: Settings) -> EffectiveModelSettings:
    record = db.get(ModelSettings, 1)
    env_api_key = settings.model_api_key.get_secret_value() if settings.model_api_key else None
    api_key = env_api_key

    if record and record.encrypted_api_key:
        api_key = decrypt_api_key(settings, record.encrypted_api_key)

    return EffectiveModelSettings(
        provider=(record.provider if record else settings.model_provider),
        base_url=(record.base_url if record else settings.model_base_url),
        default_model=(record.default_model if record else settings.model_default_model),
        api_key=api_key,
    )


def save_model_settings(db: Session, settings: Settings, patch: ModelSettingsPatch) -> EffectiveModelSettings:
    record = db.get(ModelSettings, 1)
    if not record:
        record = ModelSettings(
            id=1,
            provider=settings.model_provider,
            base_url=settings.model_base_url,
            default_model=settings.model_default_model,
        )
        db.add(record)

    if patch.provider is not None:
        record.provider = _required_text(patch.provider, "provider")
    if patch.base_url is not None:
        record.base_url = patch.base_url.strip() or None
    if patch.default_model is not None:
        record.default_model = _required_text(patch.default_model, "default_model")
    if patch.clear_api_key:
        record.encrypted_api_key = None
    elif patch.api_key is not None:
        record.encrypted_api_key = encrypt_api_key(settings, _required_text(patch.api_key, "api_key"))

    db.commit()
    db.refresh(record)
    return load_effective_model_settings(db, settings)


def encrypt_api_key(settings: Settings, api_key: str) -> str:
    return _fernet(settings).encrypt(api_key.encode("utf-8")).decode("utf-8")


def decrypt_api_key(settings: Settings, encrypted_api_key: str) -> str:
    try:
        return _fernet(settings).decrypt(encrypted_api_key.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ModelCredentialsError("stored model API key cannot be decrypted") from exc


def _fernet(settings: Settings) -> Fernet:
    passphrase = settings.access_passphrase
    if not passphrase:
        raise ModelCredentialsError("ACCESS_PASSPHRASE is required to manage stored model credentials")
    digest = hashlib.sha256(f"model-settings:{passphrase.get_secret_value()}".encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _required_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ModelSettingsError(f"{field_name} must not be empty")
    return normalized
