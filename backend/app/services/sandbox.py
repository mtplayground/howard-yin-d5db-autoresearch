from __future__ import annotations

import base64
import fnmatch
import mimetypes
import os
import resource
import signal
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping

from sqlalchemy.orm import Session

from app.db.models import SandboxJob, SandboxJobStatus
from app.models.sandbox import SandboxSubmitRequest

MAX_CAPTURED_OUTPUT = 64_000
MAX_FILE_COUNT = 24
MAX_FILE_BYTES = 200_000
MAX_CAPTURED_FILE_COUNT = 32
MAX_CAPTURED_FILE_BYTES = 300_000


class SandboxError(RuntimeError):
    pass


@dataclass(frozen=True)
class SandboxExecutionResult:
    status: str
    stdout: str
    stderr: str
    exit_code: int | None
    error_message: str | None = None


class SandboxOrchestrator:
    def __init__(self, db: Session) -> None:
        self._db = db

    async def submit(self, payload: SandboxSubmitRequest) -> SandboxJob:
        self._validate_files(payload.files)
        job = SandboxJob(
            run_id=payload.run_id,
            experiment_id=payload.experiment_id,
            status=SandboxJobStatus.QUEUED.value,
            command=payload.command,
            stdin=payload.stdin,
            timeout_seconds=payload.timeout_seconds,
            cpu_time_seconds=payload.cpu_time_seconds,
            extra={
                "files": payload.files,
                "environment": payload.environment,
                "capture_globs": payload.capture_globs,
                "file_count": len(payload.files),
                "recycled": False,
            },
        )
        self._db.add(job)
        self._db.commit()
        self._db.refresh(job)
        if payload.execute_immediately:
            return await self.execute(job.id)
        return job

    async def execute(self, job_id: uuid.UUID) -> SandboxJob:
        job = self._db.get(SandboxJob, job_id)
        if job is None:
            raise SandboxError(f"Sandbox job {job_id} was not found")
        if job.status in {SandboxJobStatus.RUNNING.value, SandboxJobStatus.SUCCEEDED.value}:
            return job

        job.status = SandboxJobStatus.RUNNING.value
        job.started_at = datetime.now(UTC)
        job.completed_at = None
        job.error_message = None
        self._db.commit()
        self._db.refresh(job)

        result = self._run_in_temporary_sandbox(job)
        job.status = result.status
        job.stdout = _truncate(result.stdout)
        job.stderr = _truncate(result.stderr)
        job.exit_code = result.exit_code
        job.error_message = result.error_message
        job.completed_at = datetime.now(UTC)
        self._db.commit()
        self._db.refresh(job)
        return job

    def _run_in_temporary_sandbox(self, job: SandboxJob) -> SandboxExecutionResult:
        sandbox_id = f"sandbox-{job.id}"
        extra = dict(job.extra or {})
        try:
            with tempfile.TemporaryDirectory(prefix=f"{sandbox_id}-") as workspace:
                extra["sandbox_id"] = sandbox_id
                extra["workspace_created_at"] = datetime.now(UTC).isoformat()
                job.extra = extra
                self._db.commit()
                _write_files(Path(workspace), extra.get("files") or {})
                result = _run_command(
                    command=job.command,
                    cwd=workspace,
                    stdin=job.stdin,
                    timeout_seconds=job.timeout_seconds,
                    cpu_time_seconds=job.cpu_time_seconds,
                    environment=extra.get("environment") or {},
                )
                if extra.get("capture_globs"):
                    captured_files = _capture_files(Path(workspace), extra.get("capture_globs") or [])
                    refreshed_extra = dict(job.extra or {})
                    refreshed_extra["captured_files"] = captured_files
                    job.extra = refreshed_extra
                    self._db.commit()
                return result
        except SandboxError as exc:
            return SandboxExecutionResult(
                status=SandboxJobStatus.FAILED.value,
                stdout="",
                stderr="",
                exit_code=None,
                error_message=str(exc),
            )
        finally:
            updated_extra = dict(job.extra or {})
            updated_extra["recycled"] = True
            updated_extra["workspace_recycled_at"] = datetime.now(UTC).isoformat()
            job.extra = updated_extra
            self._db.commit()

    def _validate_files(self, files: Mapping[str, str]) -> None:
        if len(files) > MAX_FILE_COUNT:
            raise SandboxError(f"at most {MAX_FILE_COUNT} files can be submitted")
        total_bytes = 0
        for raw_path, content in files.items():
            _safe_relative_path(raw_path)
            total_bytes += len(content.encode("utf-8"))
        if total_bytes > MAX_FILE_BYTES:
            raise SandboxError(f"submitted files exceed {MAX_FILE_BYTES} bytes")


def _run_command(
    *,
    command: list[str],
    cwd: str,
    stdin: str | None,
    timeout_seconds: int,
    cpu_time_seconds: int,
    environment: Mapping[str, str],
) -> SandboxExecutionResult:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": cwd,
        "TMPDIR": cwd,
        **{key: value for key, value in environment.items() if key and "\x00" not in key and "\x00" not in value},
    }
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            preexec_fn=lambda: _sandbox_preexec(cpu_time_seconds),
        )
    except OSError as exc:
        return SandboxExecutionResult(
            status=SandboxJobStatus.FAILED.value,
            stdout="",
            stderr="",
            exit_code=None,
            error_message=str(exc),
        )

    try:
        stdout, stderr = process.communicate(input=stdin, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _terminate_process_group(process.pid)
        stdout, stderr = process.communicate()
        return SandboxExecutionResult(
            status=SandboxJobStatus.TIMED_OUT.value,
            stdout=stdout or "",
            stderr=stderr or "",
            exit_code=process.returncode,
            error_message=f"sandbox timed out after {timeout_seconds} seconds",
        )

    status = SandboxJobStatus.SUCCEEDED.value if process.returncode == 0 else SandboxJobStatus.FAILED.value
    error_message = None if process.returncode == 0 else f"process exited with code {process.returncode}"
    return SandboxExecutionResult(
        status=status,
        stdout=stdout or "",
        stderr=stderr or "",
        exit_code=process.returncode,
        error_message=error_message,
    )


def _sandbox_preexec(cpu_time_seconds: int) -> None:
    os.setsid()
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_time_seconds, cpu_time_seconds + 1))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))


def _terminate_process_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _write_files(workspace: Path, files: Mapping[str, str]) -> None:
    for raw_path, content in files.items():
        relative_path = _safe_relative_path(raw_path)
        destination = workspace / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")


def _capture_files(workspace: Path, patterns: list[str]) -> list[dict[str, object]]:
    captured: list[dict[str, object]] = []
    total_bytes = 0
    for path in sorted(candidate for candidate in workspace.rglob("*") if candidate.is_file()):
        relative_path = path.relative_to(workspace).as_posix()
        if not _matches_any(relative_path, patterns):
            continue
        data = path.read_bytes()
        total_bytes += len(data)
        if len(captured) >= MAX_CAPTURED_FILE_COUNT or total_bytes > MAX_CAPTURED_FILE_BYTES:
            raise SandboxError("captured sandbox files exceed configured limits")
        captured.append(
            {
                "path": relative_path,
                "byte_size": len(data),
                "content_type": mimetypes.guess_type(relative_path)[0] or "application/octet-stream",
                "base64": base64.b64encode(data).decode("ascii"),
            }
        )
    return captured


def _matches_any(relative_path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(relative_path, pattern) for pattern in patterns)


def _safe_relative_path(raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise SandboxError(f"unsafe sandbox file path: {raw_path}")
    return path


def _truncate(value: str) -> str:
    if len(value) <= MAX_CAPTURED_OUTPUT:
        return value
    return value[:MAX_CAPTURED_OUTPUT] + "\n[output truncated]"
