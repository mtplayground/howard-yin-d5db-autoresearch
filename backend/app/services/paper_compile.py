from __future__ import annotations

import base64
import hashlib
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Artifact, ArtifactKind, Paper, PaperStatus, SandboxJobStatus
from app.models.sandbox import SandboxSubmitRequest
from app.services.sandbox import SandboxError, SandboxOrchestrator
from app.services.storage import ObjectStorageClient, ObjectNotFoundError, StoredObjectRef, StorageError, get_storage_client


class PaperCompileError(RuntimeError):
    pass


class SandboxSubmitter(Protocol):
    async def submit(self, payload: SandboxSubmitRequest) -> Any:
        ...


@dataclass(frozen=True)
class CompilePayload:
    files: dict[str, str]
    command: list[str]
    capture_globs: list[str]


async def compile_paper_to_pdf(
    db: Session,
    paper_id: uuid.UUID,
    *,
    storage: ObjectStorageClient | None = None,
    sandbox: SandboxSubmitter | None = None,
    timeout_seconds: int = 120,
    cpu_time_seconds: int = 60,
) -> Paper:
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise PaperCompileError(f"Paper {paper_id} was not found")
    storage_client = storage or get_storage_client()
    sandbox_submitter = sandbox or SandboxOrchestrator(db)
    payload = _compile_payload(storage_client, paper)

    paper.status = PaperStatus.GENERATING.value
    _merge_review_notes(
        paper,
        {
            "status": "running",
            "started_at": datetime.now(UTC).isoformat(),
            "command": payload.command,
        },
    )
    db.commit()
    db.refresh(paper)

    try:
        job = await sandbox_submitter.submit(
            SandboxSubmitRequest(
                command=payload.command,
                files=payload.files,
                timeout_seconds=timeout_seconds,
                cpu_time_seconds=cpu_time_seconds,
                run_id=paper.run_id,
                experiment_id=paper.experiment_id,
                capture_globs=payload.capture_globs,
                execute_immediately=True,
            )
        )
    except (SandboxError, ValueError) as exc:
        _mark_compile_failed(db, paper, str(exc))
        raise PaperCompileError(str(exc)) from exc

    if job.status != SandboxJobStatus.SUCCEEDED.value:
        message = job.error_message or f"LaTeX compile sandbox finished with status {job.status}"
        _mark_compile_failed(db, paper, message, job=job)
        raise PaperCompileError(message)

    pdf_bytes = _captured_pdf(job)
    ref = _upload_pdf(storage_client, paper, pdf_bytes)
    artifact = _pdf_artifact(paper, ref)
    existing = db.scalar(select(Artifact).where(Artifact.storage_key == artifact.storage_key))
    if existing is None:
        db.add(artifact)
    else:
        existing.kind = artifact.kind
        existing.filename = artifact.filename
        existing.content_type = artifact.content_type
        existing.byte_size = artifact.byte_size
        existing.checksum_sha256 = artifact.checksum_sha256
        existing.extra = artifact.extra

    paper.status = PaperStatus.COMPILED.value
    paper.pdf_storage_key = ref.key
    paper.compiled_at = datetime.now(UTC)
    _merge_review_notes(
        paper,
        {
            "status": "succeeded",
            "sandbox_job_id": str(job.id),
            "stdout": job.stdout or "",
            "stderr": job.stderr or "",
            "exit_code": job.exit_code,
            "pdf_storage_key": ref.key,
            "completed_at": paper.compiled_at.isoformat(),
        },
    )
    db.commit()
    db.refresh(paper)
    return paper


def _compile_payload(storage: ObjectStorageClient, paper: Paper) -> CompilePayload:
    if not paper.latex_storage_key:
        raise PaperCompileError("paper has no LaTeX source storage key")
    files = {
        "main.tex": _download_text(storage, paper.latex_storage_key, "paper LaTeX source"),
        "_compile_latex.py": _compile_script(),
    }
    for artifact in paper.artifacts:
        if artifact.kind != ArtifactKind.FIGURE.value:
            continue
        input_path = artifact.extra.get("input_path") if isinstance(artifact.extra, dict) else None
        if not isinstance(input_path, str) or not artifact.storage_key:
            continue
        files[_safe_relative_path(input_path)] = _download_text(storage, artifact.storage_key, f"figure {artifact.filename or input_path}")
    return CompilePayload(
        files=files,
        command=[sys.executable, "_compile_latex.py"],
        capture_globs=["main.pdf", "main.log"],
    )


def _download_text(storage: ObjectStorageClient, key: str, label: str) -> str:
    try:
        data = storage.download_bytes(key)
    except ObjectNotFoundError as exc:
        raise PaperCompileError(f"{label} was not found in object storage") from exc
    except StorageError as exc:
        raise PaperCompileError(str(exc)) from exc
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PaperCompileError(f"{label} was not UTF-8 text") from exc


def _compile_script() -> str:
    return r'''from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def run(command):
    print("$ " + " ".join(command), flush=True)
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.stdout:
        print(completed.stdout, end="", flush=True)
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr, flush=True)
    return completed.returncode


def main():
    if shutil.which("latexmk"):
        return run(["latexmk", "-pdf", "-interaction=nonstopmode", "-halt-on-error", "main.tex"])
    if shutil.which("pdflatex"):
        first = run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "main.tex"])
        if first != 0:
            return first
        return run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "main.tex"])
    print("No LaTeX compiler found in sandbox PATH", file=sys.stderr, flush=True)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _captured_pdf(job: Any) -> bytes:
    for payload in (job.extra or {}).get("captured_files", []):
        if not isinstance(payload, dict) or payload.get("path") != "main.pdf":
            continue
        encoded = payload.get("base64")
        if not isinstance(encoded, str):
            break
        try:
            pdf_bytes = base64.b64decode(encoded)
        except ValueError as exc:
            raise PaperCompileError("captured PDF payload was not valid base64") from exc
        if not pdf_bytes.startswith(b"%PDF"):
            raise PaperCompileError("captured PDF payload did not look like a PDF")
        return pdf_bytes
    raise PaperCompileError("LaTeX compile did not produce main.pdf")


def _upload_pdf(storage: ObjectStorageClient, paper: Paper, data: bytes) -> StoredObjectRef:
    checksum = hashlib.sha256(data).hexdigest()
    key = f"papers/runs/{paper.run_id or 'unbound'}/{paper.id}/main.pdf"
    try:
        return storage.upload_bytes(
            _safe_relative_path(key),
            data,
            content_type="application/pdf",
            checksum_sha256=checksum,
            metadata={
                "paper-id": str(paper.id),
                "artifact-kind": ArtifactKind.PDF.value,
            },
        )
    except StorageError as exc:
        raise PaperCompileError(str(exc)) from exc


def _pdf_artifact(paper: Paper, ref: StoredObjectRef) -> Artifact:
    return Artifact(
        run_id=paper.run_id,
        idea_id=paper.idea_id,
        experiment_id=paper.experiment_id,
        paper_id=paper.id,
        kind=ArtifactKind.PDF.value,
        storage_key=ref.key,
        filename="main.pdf",
        content_type=ref.content_type,
        byte_size=ref.byte_size,
        checksum_sha256=ref.checksum_sha256,
        extra={"source": "latex_compile", "storage_uri": ref.uri},
    )


def _mark_compile_failed(db: Session, paper: Paper, message: str, *, job: Any | None = None) -> None:
    paper.status = PaperStatus.FAILED.value
    payload: dict[str, Any] = {
        "status": "failed",
        "error": message,
        "completed_at": datetime.now(UTC).isoformat(),
    }
    if job is not None:
        payload.update(
            {
                "sandbox_job_id": str(job.id),
                "stdout": job.stdout or "",
                "stderr": job.stderr or "",
                "exit_code": job.exit_code,
            }
        )
    _merge_review_notes(paper, payload)
    db.commit()
    db.refresh(paper)


def _merge_review_notes(paper: Paper, compile_payload: dict[str, Any]) -> None:
    review_notes = dict(paper.review_notes or {})
    review_notes["compile"] = compile_payload
    paper.review_notes = review_notes


def _safe_relative_path(raw_path: str) -> str:
    path = Path(raw_path)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise PaperCompileError(f"unsafe compile path: {raw_path}")
    if any(part in {"", "."} for part in path.parts):
        raise PaperCompileError(f"unsafe compile path: {raw_path}")
    return path.as_posix()
