from __future__ import annotations

import base64
import json
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Experiment, ExperimentStatus, SandboxJobStatus
from app.models.sandbox import SandboxSubmitRequest
from app.services.sandbox import SandboxError, SandboxOrchestrator

RESULT_FILE_NAMES = {"results.json", "metrics.json"}
CHART_EXTENSIONS = {".png", ".jpg", ".jpeg", ".svg", ".pdf"}
DEFAULT_CAPTURE_GLOBS = [
    "results.json",
    "metrics.json",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.svg",
    "*.pdf",
    "figures/*",
    "charts/*",
    "outputs/*",
]
METRIC_LINE_PATTERN = re.compile(r"^METRIC\s+([A-Za-z_][A-Za-z0-9_.-]*)\s*=\s*(-?\d+(?:\.\d+)?)\s*$")


class ExperimentRunnerError(RuntimeError):
    pass


@dataclass(frozen=True)
class CapturedExperimentFile:
    path: str
    byte_size: int
    content_type: str
    base64: str


@dataclass(frozen=True)
class ExperimentRunResult:
    sandbox_job_id: uuid.UUID
    status: str
    numeric_results: dict[str, float | int] = field(default_factory=dict)
    captured_files: list[CapturedExperimentFile] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    error_message: str | None = None


class ExperimentRunner:
    def __init__(self, db: Session, sandbox: SandboxOrchestrator | None = None) -> None:
        self._db = db
        self._sandbox = sandbox or SandboxOrchestrator(db)

    async def run(self, experiment_id: uuid.UUID, *, timeout_seconds: int = 300, cpu_time_seconds: int = 120) -> Experiment:
        experiment = self._load_experiment(experiment_id)
        files = _runner_files(experiment)
        command = [sys.executable, "_runner.py"]

        started_at = datetime.now(UTC)
        experiment.status = ExperimentStatus.RUNNING.value
        experiment.started_at = started_at
        experiment.completed_at = None
        experiment.error_message = None
        self._db.commit()
        self._db.refresh(experiment)

        try:
            job = await self._sandbox.submit(
                SandboxSubmitRequest(
                    command=command,
                    files=files,
                    timeout_seconds=timeout_seconds,
                    cpu_time_seconds=cpu_time_seconds,
                    run_id=experiment.run_id,
                    experiment_id=experiment.id,
                    capture_globs=DEFAULT_CAPTURE_GLOBS,
                )
            )
        except (SandboxError, ValueError) as exc:
            _mark_failed(experiment, str(exc))
            self._db.commit()
            self._db.refresh(experiment)
            raise ExperimentRunnerError(str(exc)) from exc

        result = _result_from_job(job)
        _apply_result(experiment, result)
        self._db.commit()
        self._db.refresh(experiment)
        return experiment

    def _load_experiment(self, experiment_id: uuid.UUID) -> Experiment:
        experiment = self._db.get(Experiment, experiment_id)
        if experiment is None:
            raise ExperimentRunnerError(f"Experiment {experiment_id} was not found")
        if not experiment.code_files:
            raise ExperimentRunnerError("experiment has no generated code files")
        if not experiment.run_command:
            raise ExperimentRunnerError("experiment has no run command")
        return experiment


async def run_experiment_in_sandbox(
    db: Session,
    experiment_id: uuid.UUID,
    *,
    timeout_seconds: int = 300,
    cpu_time_seconds: int = 120,
) -> Experiment:
    return await ExperimentRunner(db).run(
        experiment_id,
        timeout_seconds=timeout_seconds,
        cpu_time_seconds=cpu_time_seconds,
    )


def _runner_files(experiment: Experiment) -> dict[str, str]:
    files = dict(experiment.code_files or {})
    files["requirements.txt"] = "\n".join(experiment.dependencies or [])
    files["_runner.py"] = _runner_script(experiment.run_command or [])
    return files


def _runner_script(run_command: list[str]) -> str:
    command_json = json.dumps(run_command)
    return f"""from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

RUN_COMMAND = {command_json}


def run_step(command, *, env=None):
    print("$ " + " ".join(command), flush=True)
    completed = subprocess.run(command, capture_output=True, text=True, env=env)
    if completed.stdout:
        print(completed.stdout, end="", flush=True)
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr, flush=True)
    return completed.returncode


def main():
    env = os.environ.copy()
    run_command = list(RUN_COMMAND)
    if run_command and run_command[0] in {{"python", "python3"}}:
        run_command[0] = sys.executable
    requirements = Path("requirements.txt")
    deps_dir = Path(".deps")
    if requirements.exists() and requirements.read_text(encoding="utf-8").strip():
        print("Installing experiment dependencies", flush=True)
        deps_dir.mkdir(exist_ok=True)
        install_code = run_step([
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--target",
            str(deps_dir),
            "-r",
            str(requirements),
        ], env=env)
        if install_code != 0:
            return install_code
    if deps_dir.exists():
        env["PYTHONPATH"] = str(deps_dir.resolve()) + os.pathsep + env.get("PYTHONPATH", "")
    return run_step(run_command, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
"""


def _result_from_job(job: Any) -> ExperimentRunResult:
    captured_files = [_captured_file_from_payload(payload) for payload in (job.extra or {}).get("captured_files", [])]
    stdout = job.stdout or ""
    stderr = job.stderr or ""
    return ExperimentRunResult(
        sandbox_job_id=job.id,
        status=job.status,
        numeric_results=_numeric_results(stdout, captured_files),
        captured_files=captured_files,
        stdout=stdout,
        stderr=stderr,
        error_message=job.error_message,
    )


def _captured_file_from_payload(payload: dict[str, Any]) -> CapturedExperimentFile:
    return CapturedExperimentFile(
        path=str(payload.get("path") or ""),
        byte_size=int(payload.get("byte_size") or 0),
        content_type=str(payload.get("content_type") or "application/octet-stream"),
        base64=str(payload.get("base64") or ""),
    )


def _numeric_results(stdout: str, captured_files: list[CapturedExperimentFile]) -> dict[str, float | int]:
    values: dict[str, float | int] = {}
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("RESULT_JSON:"):
            _merge_numeric_values(values, _parse_json(stripped.removeprefix("RESULT_JSON:").strip()))
            continue
        metric_match = METRIC_LINE_PATTERN.match(stripped)
        if metric_match:
            values[metric_match.group(1)] = _number_from_string(metric_match.group(2))

    for captured_file in captured_files:
        if Path(captured_file.path).name in RESULT_FILE_NAMES:
            try:
                decoded = base64.b64decode(captured_file.base64).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                continue
            _merge_numeric_values(values, _parse_json(decoded))
    return values


def _parse_json(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {}


def _merge_numeric_values(destination: dict[str, float | int], payload: Any, *, prefix: str = "") -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized_key = str(key)
            nested_prefix = f"{prefix}.{normalized_key}" if prefix else normalized_key
            _merge_numeric_values(destination, value, prefix=nested_prefix)
        return
    if isinstance(payload, list):
        for index, value in enumerate(payload):
            _merge_numeric_values(destination, value, prefix=f"{prefix}.{index}" if prefix else str(index))
        return
    if isinstance(payload, bool):
        return
    if isinstance(payload, int | float):
        destination[prefix] = payload


def _number_from_string(value: str) -> float | int:
    parsed = float(value)
    if parsed.is_integer():
        return int(parsed)
    return parsed


def _apply_result(experiment: Experiment, result: ExperimentRunResult) -> None:
    completed_at = datetime.now(UTC)
    experiment.status = _experiment_status(result.status)
    experiment.completed_at = completed_at
    experiment.error_message = result.error_message
    metrics = dict(experiment.metrics or {})
    metrics["last_run"] = {
        "sandbox_job_id": str(result.sandbox_job_id),
        "status": result.status,
        "completed_at": completed_at.isoformat(),
        "numeric_results": result.numeric_results,
        "logs": {
            "stdout": result.stdout,
            "stderr": result.stderr,
        },
        "captured_files": [
            {
                "path": captured_file.path,
                "byte_size": captured_file.byte_size,
                "content_type": captured_file.content_type,
                "base64": captured_file.base64,
            }
            for captured_file in result.captured_files
        ],
        "charts": [
            {
                "path": captured_file.path,
                "byte_size": captured_file.byte_size,
                "content_type": captured_file.content_type,
                "base64": captured_file.base64,
            }
            for captured_file in result.captured_files
            if Path(captured_file.path).suffix.lower() in CHART_EXTENSIONS
        ],
    }
    experiment.metrics = metrics
    experiment.result_summary = _result_summary(result)


def _experiment_status(sandbox_status: str) -> str:
    if sandbox_status == SandboxJobStatus.SUCCEEDED.value:
        return ExperimentStatus.SUCCEEDED.value
    if sandbox_status == SandboxJobStatus.CANCELED.value:
        return ExperimentStatus.CANCELED.value
    return ExperimentStatus.FAILED.value


def _result_summary(result: ExperimentRunResult) -> str:
    if result.status != SandboxJobStatus.SUCCEEDED.value:
        return result.error_message or f"sandbox finished with status {result.status}"
    if result.numeric_results:
        metrics = ", ".join(f"{key}={value}" for key, value in sorted(result.numeric_results.items())[:8])
        return f"Experiment completed with metrics: {metrics}"
    return "Experiment completed without numeric metrics"


def _mark_failed(experiment: Experiment, message: str) -> None:
    experiment.status = ExperimentStatus.FAILED.value
    experiment.completed_at = datetime.now(UTC)
    experiment.error_message = message
