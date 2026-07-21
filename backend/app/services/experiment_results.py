from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Artifact, ArtifactKind, Experiment
from app.services.storage import ObjectStorageClient, StoredObjectRef, StorageError, get_storage_client

SAFE_KEY_PATTERN = re.compile(r"[^A-Za-z0-9._/-]+")
FIGURE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".svg", ".pdf"}


class ExperimentResultPersistenceError(RuntimeError):
    pass


class ExperimentResultPersistenceService:
    def __init__(self, db: Session, storage: ObjectStorageClient | None = None) -> None:
        self._db = db
        self._storage = storage or get_storage_client()

    def persist(self, experiment_id: uuid.UUID) -> list[Artifact]:
        experiment = self._load_experiment(experiment_id)
        last_run = _last_run_payload(experiment)
        already_persisted = self._existing_persisted_artifacts(last_run)
        if already_persisted and not _captured_files(last_run):
            return already_persisted
        sandbox_job_id = _required_text(last_run.get("sandbox_job_id"), "last_run.sandbox_job_id")
        base_key = _base_key(experiment, sandbox_job_id)
        artifacts: list[Artifact] = []

        artifacts.append(
            self._upload_artifact(
                experiment,
                key=f"{base_key}/results.json",
                filename="results.json",
                content_type="application/json",
                data=_json_bytes(last_run.get("numeric_results") or {}),
                kind=ArtifactKind.RESULT.value,
                extra={"source": "numeric_results", "sandbox_job_id": sandbox_job_id},
            )
        )

        artifacts.append(
            self._upload_artifact(
                experiment,
                key=f"{base_key}/run.log",
                filename="run.log",
                content_type="text/plain; charset=utf-8",
                data=_log_bytes(last_run),
                kind=ArtifactKind.LOG.value,
                extra={"source": "runner_logs", "sandbox_job_id": sandbox_job_id},
            )
        )

        for captured_file in _captured_files(last_run):
            data = _decode_base64(_required_text(captured_file.get("base64"), "captured_file.base64"))
            filename = _safe_filename(_required_text(captured_file.get("path"), "captured_file.path"))
            content_type = str(captured_file.get("content_type") or "application/octet-stream")
            kind = ArtifactKind.FIGURE.value if Path(filename).suffix.lower() in FIGURE_EXTENSIONS else ArtifactKind.RESULT.value
            artifacts.append(
                self._upload_artifact(
                    experiment,
                    key=f"{base_key}/files/{filename}",
                    filename=Path(filename).name,
                    content_type=content_type,
                    data=data,
                    kind=kind,
                    extra={
                        "source": "captured_file",
                        "sandbox_job_id": sandbox_job_id,
                        "captured_path": captured_file.get("path"),
                    },
                )
            )

        persisted_refs = [_artifact_ref(artifact) for artifact in artifacts]
        metrics = dict(experiment.metrics or {})
        updated_last_run = dict(last_run)
        updated_last_run["persisted_artifacts"] = persisted_refs
        updated_last_run["persisted_at"] = datetime.now(UTC).isoformat()
        updated_last_run["captured_files"] = [_strip_inline_payload(item) for item in _captured_files(last_run)]
        updated_last_run["charts"] = [_strip_inline_payload(item) for item in last_run.get("charts") or [] if isinstance(item, dict)]
        metrics["last_run"] = updated_last_run
        experiment.metrics = metrics
        self._db.commit()
        for artifact in artifacts:
            self._db.refresh(artifact)
        self._db.refresh(experiment)
        return artifacts

    def _load_experiment(self, experiment_id: uuid.UUID) -> Experiment:
        experiment = self._db.get(Experiment, experiment_id)
        if experiment is None:
            raise ExperimentResultPersistenceError(f"Experiment {experiment_id} was not found")
        return experiment

    def _existing_persisted_artifacts(self, last_run: dict[str, Any]) -> list[Artifact]:
        refs = last_run.get("persisted_artifacts")
        if not isinstance(refs, list):
            return []
        artifact_ids = []
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            raw_id = ref.get("id")
            if not isinstance(raw_id, str):
                continue
            try:
                artifact_ids.append(uuid.UUID(raw_id))
            except ValueError:
                continue
        if not artifact_ids:
            return []
        artifacts_by_id = {
            artifact.id: artifact
            for artifact in self._db.query(Artifact).filter(Artifact.id.in_(artifact_ids)).all()
        }
        return [artifacts_by_id[artifact_id] for artifact_id in artifact_ids if artifact_id in artifacts_by_id]

    def _upload_artifact(
        self,
        experiment: Experiment,
        *,
        key: str,
        filename: str,
        content_type: str,
        data: bytes,
        kind: str,
        extra: dict[str, Any],
    ) -> Artifact:
        checksum = hashlib.sha256(data).hexdigest()
        try:
            ref = self._storage.upload_bytes(
                key,
                data,
                content_type=content_type,
                checksum_sha256=checksum,
                metadata={
                    "experiment-id": str(experiment.id),
                    "artifact-kind": kind,
                },
            )
        except StorageError as exc:
            raise ExperimentResultPersistenceError(str(exc)) from exc

        artifact = self._artifact_for_ref(experiment, ref, filename=filename, kind=kind, extra=extra)
        existing = self._db.query(Artifact).filter(Artifact.storage_key == artifact.storage_key).one_or_none()
        if existing is not None:
            existing.kind = artifact.kind
            existing.filename = artifact.filename
            existing.content_type = artifact.content_type
            existing.byte_size = artifact.byte_size
            existing.checksum_sha256 = artifact.checksum_sha256
            existing.extra = artifact.extra
            return existing
        self._db.add(artifact)
        self._db.flush()
        return artifact

    @staticmethod
    def _artifact_for_ref(
        experiment: Experiment,
        ref: StoredObjectRef,
        *,
        filename: str,
        kind: str,
        extra: dict[str, Any],
    ) -> Artifact:
        return Artifact(
            run_id=experiment.run_id,
            idea_id=experiment.idea_id,
            experiment_id=experiment.id,
            kind=kind,
            storage_key=ref.key,
            filename=filename,
            content_type=ref.content_type,
            byte_size=ref.byte_size,
            checksum_sha256=ref.checksum_sha256,
            extra={**extra, "storage_uri": ref.uri},
        )


def persist_experiment_results(
    db: Session,
    experiment_id: uuid.UUID,
    *,
    storage: ObjectStorageClient | None = None,
) -> list[Artifact]:
    return ExperimentResultPersistenceService(db, storage=storage).persist(experiment_id)


def _last_run_payload(experiment: Experiment) -> dict[str, Any]:
    payload = (experiment.metrics or {}).get("last_run")
    if not isinstance(payload, dict):
        raise ExperimentResultPersistenceError("experiment has no last_run metrics to persist")
    return payload


def _captured_files(last_run: dict[str, Any]) -> list[dict[str, Any]]:
    files = last_run.get("captured_files")
    if not isinstance(files, list):
        return []
    return [item for item in files if isinstance(item, dict) and item.get("base64")]


def _base_key(experiment: Experiment, sandbox_job_id: str) -> str:
    run_part = str(experiment.run_id) if experiment.run_id else "unbound"
    return f"experiments/runs/{run_part}/{experiment.id}/{_safe_key_part(sandbox_job_id)}"


def _safe_filename(path: str) -> str:
    clean = "/".join(_safe_key_part(part) for part in Path(path).parts if part not in {"", "."})
    if not clean:
        raise ExperimentResultPersistenceError("captured file path must not be empty")
    return clean


def _safe_key_part(value: str) -> str:
    clean = SAFE_KEY_PATTERN.sub("-", value.strip().strip("/"))
    clean = "/".join(part for part in clean.split("/") if part)
    if not clean or clean == ".." or "/../" in f"/{clean}/":
        raise ExperimentResultPersistenceError(f"unsafe storage key segment: {value}")
    return clean


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")


def _log_bytes(last_run: dict[str, Any]) -> bytes:
    logs = last_run.get("logs") if isinstance(last_run.get("logs"), dict) else {}
    stdout = str(logs.get("stdout") or "")
    stderr = str(logs.get("stderr") or "")
    return f"STDOUT\n{stdout}\n\nSTDERR\n{stderr}\n".encode("utf-8")


def _decode_base64(value: str) -> bytes:
    try:
        return base64.b64decode(value)
    except ValueError as exc:
        raise ExperimentResultPersistenceError("captured file payload was not valid base64") from exc


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ExperimentResultPersistenceError(f"{field_name} must be text")
    return value


def _strip_inline_payload(item: dict[str, Any]) -> dict[str, Any]:
    clean = dict(item)
    clean.pop("base64", None)
    return clean


def _artifact_ref(artifact: Artifact) -> dict[str, Any]:
    return {
        "id": str(artifact.id),
        "kind": artifact.kind,
        "storage_key": artifact.storage_key,
        "filename": artifact.filename,
        "content_type": artifact.content_type,
        "byte_size": artifact.byte_size,
        "checksum_sha256": artifact.checksum_sha256,
    }
