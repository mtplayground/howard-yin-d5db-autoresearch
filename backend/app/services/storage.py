from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import boto3
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import Settings, get_settings


class StorageError(RuntimeError):
    pass


class StorageConfigurationError(StorageError):
    pass


class StorageOperationError(StorageError):
    pass


class ObjectNotFoundError(StorageOperationError):
    pass


@dataclass(frozen=True)
class StoredObjectRef:
    bucket: str
    key: str
    uri: str
    content_type: str | None = None
    byte_size: int | None = None
    checksum_sha256: str | None = None


@dataclass(frozen=True)
class StorageConfig:
    bucket: str
    prefix: str
    region: str
    endpoint_url: str | None
    access_key_id: str | None
    secret_access_key: str | None

    @classmethod
    def from_settings(cls, settings: Settings) -> StorageConfig:
        if not settings.object_storage_bucket:
            raise StorageConfigurationError("OBJECT_STORAGE_BUCKET is required")

        access_key_id = (
            settings.object_storage_access_key_id.get_secret_value()
            if settings.object_storage_access_key_id
            else None
        )
        secret_access_key = (
            settings.object_storage_secret_access_key.get_secret_value()
            if settings.object_storage_secret_access_key
            else None
        )
        if bool(access_key_id) != bool(secret_access_key):
            raise StorageConfigurationError(
                "OBJECT_STORAGE_ACCESS_KEY_ID and OBJECT_STORAGE_SECRET_ACCESS_KEY must be configured together"
            )

        return cls(
            bucket=settings.object_storage_bucket,
            prefix=settings.object_storage_prefix,
            region=settings.object_storage_region,
            endpoint_url=settings.object_storage_endpoint,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
        )


class ObjectStorageClient:
    def __init__(self, config: StorageConfig, s3_client: BaseClient | None = None) -> None:
        self._config = config
        self._client = s3_client or self._build_client(config)

    @staticmethod
    def _build_client(config: StorageConfig) -> BaseClient:
        client_options: dict[str, Any] = {
            "service_name": "s3",
            "region_name": config.region,
            "config": Config(signature_version="s3v4"),
        }
        if config.endpoint_url:
            client_options["endpoint_url"] = config.endpoint_url
        if config.access_key_id and config.secret_access_key:
            client_options["aws_access_key_id"] = config.access_key_id
            client_options["aws_secret_access_key"] = config.secret_access_key
        return boto3.client(**client_options)

    @property
    def bucket(self) -> str:
        return self._config.bucket

    def storage_key(self, key: str) -> str:
        clean_key = self._clean_key(key)
        prefix = self._clean_prefix(self._config.prefix)
        if not prefix or clean_key == prefix or clean_key.startswith(f"{prefix}/"):
            return clean_key
        return f"{prefix}/{clean_key}"

    def object_uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{self.storage_key(key)}"

    def upload_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
        checksum_sha256: str | None = None,
        cache_control: str | None = None,
    ) -> StoredObjectRef:
        storage_key = self.storage_key(key)
        request: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": storage_key,
            "Body": data,
            "ContentLength": len(data),
        }
        if content_type:
            request["ContentType"] = content_type
        object_metadata = dict(metadata or {})
        if checksum_sha256:
            object_metadata["checksum-sha256"] = checksum_sha256
        if object_metadata:
            request["Metadata"] = object_metadata
        if cache_control:
            request["CacheControl"] = cache_control

        try:
            self._client.put_object(**request)
        except (BotoCoreError, ClientError) as exc:
            raise StorageOperationError(f"failed to upload object {storage_key}") from exc

        return StoredObjectRef(
            bucket=self.bucket,
            key=storage_key,
            uri=f"s3://{self.bucket}/{storage_key}",
            content_type=content_type,
            byte_size=len(data),
            checksum_sha256=checksum_sha256,
        )

    def download_bytes(self, key: str) -> bytes:
        storage_key = self.storage_key(key)
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=storage_key)
            body = response["Body"]
            return body.read()
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                raise ObjectNotFoundError(f"object not found: {storage_key}") from exc
            raise StorageOperationError(f"failed to download object {storage_key}") from exc
        except BotoCoreError as exc:
            raise StorageOperationError(f"failed to download object {storage_key}") from exc

    def exists(self, key: str) -> bool:
        storage_key = self.storage_key(key)
        try:
            self._client.head_object(Bucket=self.bucket, Key=storage_key)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise StorageOperationError(f"failed to check object {storage_key}") from exc
        except BotoCoreError as exc:
            raise StorageOperationError(f"failed to check object {storage_key}") from exc

    def presigned_get_url(self, key: str, *, expires_in: int = 900) -> str:
        storage_key = self.storage_key(key)
        try:
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": storage_key},
                ExpiresIn=expires_in,
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageOperationError(f"failed to create object reference {storage_key}") from exc

    @staticmethod
    def _clean_prefix(prefix: str) -> str:
        return "/".join(part for part in prefix.strip("/").split("/") if part)

    @classmethod
    def _clean_key(cls, key: str) -> str:
        clean_key = "/".join(part for part in key.strip("/").split("/") if part)
        if not clean_key:
            raise ValueError("object key must not be empty")
        if any(part == ".." for part in clean_key.split("/")):
            raise ValueError("object key must not contain '..'")
        return clean_key


def get_storage_client(settings: Settings | None = None) -> ObjectStorageClient:
    resolved_settings = settings or get_settings()
    return ObjectStorageClient(StorageConfig.from_settings(resolved_settings))
