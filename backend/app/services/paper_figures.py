from __future__ import annotations

import hashlib
import math
import re
import uuid
from dataclasses import dataclass
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Artifact, ArtifactKind, Experiment, Paper
from app.services.storage import ObjectStorageClient, StoredObjectRef, StorageError

SAFE_KEY_PATTERN = re.compile(r"[^A-Za-z0-9._/-]+")
MAX_METRICS_PER_FIGURE = 8
LATEX_SPECIALS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


class PaperFigureGenerationError(RuntimeError):
    pass


@dataclass(frozen=True)
class GeneratedFigure:
    filename: str
    input_path: str
    caption: str
    label: str
    latex_source: str


def generate_metric_figures(experiment: Experiment) -> list[GeneratedFigure]:
    metrics = _numeric_metrics(experiment.metrics or {})
    if not metrics:
        return []
    selected = metrics[:MAX_METRICS_PER_FIGURE]
    return [
        GeneratedFigure(
            filename="metric-summary.tex",
            input_path="figures/metric-summary.tex",
            caption="Summary of numeric results captured from the automated experiment.",
            label="fig:metric-summary",
            latex_source=_metric_bar_chart(selected),
        )
    ]


def persist_paper_figures(
    db: Session,
    experiment: Experiment,
    paper: Paper,
    storage: ObjectStorageClient,
) -> list[Artifact]:
    artifacts: list[Artifact] = []
    for figure in generate_metric_figures(experiment):
        data = figure.latex_source.encode("utf-8")
        checksum = hashlib.sha256(data).hexdigest()
        key = _safe_storage_key(f"papers/runs/{paper.run_id or 'unbound'}/{paper.id}/{figure.input_path}")
        try:
            ref = storage.upload_bytes(
                key,
                data,
                content_type="application/x-tex; charset=utf-8",
                checksum_sha256=checksum,
                metadata={
                    "paper-id": str(paper.id),
                    "experiment-id": str(experiment.id),
                    "artifact-kind": ArtifactKind.FIGURE.value,
                },
            )
        except StorageError as exc:
            raise PaperFigureGenerationError(str(exc)) from exc
        artifacts.append(_upsert_figure_artifact(db, experiment, paper, figure, ref))
    return artifacts


def embed_figures_in_latex(latex_source: str, figure_artifacts: Sequence[Artifact]) -> str:
    snippets = [_figure_snippet(artifact) for artifact in figure_artifacts if _figure_input_path(artifact)]
    if not snippets:
        return latex_source
    insertion = "\n\n" + "\n\n".join(snippets) + "\n"
    if "\\label{fig:metric-summary}" in latex_source:
        return latex_source
    section_pattern = re.compile(r"(\\section\*?\{Results\})", re.IGNORECASE)
    match = section_pattern.search(latex_source)
    if match:
        insert_at = match.end()
        return latex_source[:insert_at] + insertion + latex_source[insert_at:]
    end_document = latex_source.rfind("\\end{document}")
    if end_document == -1:
        return latex_source + insertion
    return latex_source[:end_document] + insertion + "\n" + latex_source[end_document:]


def figure_artifact_refs(artifacts: Sequence[Artifact]) -> list[dict[str, Any]]:
    return [
        {
            "id": str(artifact.id),
            "storage_key": artifact.storage_key,
            "filename": artifact.filename,
            "input_path": _figure_input_path(artifact),
            "caption": artifact.extra.get("caption"),
            "label": artifact.extra.get("label"),
            "byte_size": artifact.byte_size,
            "checksum_sha256": artifact.checksum_sha256,
        }
        for artifact in artifacts
    ]


def _upsert_figure_artifact(
    db: Session,
    experiment: Experiment,
    paper: Paper,
    figure: GeneratedFigure,
    ref: StoredObjectRef,
) -> Artifact:
    artifact = Artifact(
        run_id=paper.run_id,
        idea_id=paper.idea_id,
        experiment_id=experiment.id,
        paper_id=paper.id,
        kind=ArtifactKind.FIGURE.value,
        storage_key=ref.key,
        filename=figure.filename,
        content_type=ref.content_type,
        byte_size=ref.byte_size,
        checksum_sha256=ref.checksum_sha256,
        extra={
            "source": "paper_figure_generator",
            "storage_uri": ref.uri,
            "input_path": figure.input_path,
            "caption": figure.caption,
            "label": figure.label,
        },
    )
    existing = db.scalar(select(Artifact).where(Artifact.storage_key == artifact.storage_key))
    if existing is not None:
        existing.kind = artifact.kind
        existing.filename = artifact.filename
        existing.content_type = artifact.content_type
        existing.byte_size = artifact.byte_size
        existing.checksum_sha256 = artifact.checksum_sha256
        existing.extra = artifact.extra
        return existing
    db.add(artifact)
    db.flush()
    return artifact


def _numeric_metrics(metrics: dict[str, Any]) -> list[tuple[str, float]]:
    raw = metrics.get("last_run") if isinstance(metrics.get("last_run"), dict) else metrics
    numeric_results = raw.get("numeric_results") if isinstance(raw, dict) and isinstance(raw.get("numeric_results"), dict) else raw
    if not isinstance(numeric_results, dict):
        return []
    flattened = _flatten_numeric("", numeric_results)
    flattened.sort(key=lambda item: item[0])
    return flattened


def _flatten_numeric(prefix: str, value: Any) -> list[tuple[str, float]]:
    if isinstance(value, bool):
        return []
    if isinstance(value, int | float):
        number = float(value)
        if math.isfinite(number):
            return [(prefix or "value", number)]
        return []
    if isinstance(value, dict):
        metrics: list[tuple[str, float]] = []
        for key, nested in value.items():
            if not isinstance(key, str):
                continue
            label = f"{prefix}.{key}" if prefix else key
            metrics.extend(_flatten_numeric(label, nested))
        return metrics
    return []


def _metric_bar_chart(metrics: Sequence[tuple[str, float]]) -> str:
    max_abs = max(abs(value) for _, value in metrics) or 1.0
    row_height = 18
    label_width = 116
    bar_width = 170
    value_x = label_width + bar_width + 12
    height = max(36, 18 + row_height * len(metrics))
    rows = [
        r"\begingroup",
        r"\setlength{\unitlength}{1pt}",
        rf"\begin{{picture}}(340,{height})",
    ]
    for index, (label, value) in enumerate(metrics):
        y = height - 18 - index * row_height
        width = max(2, int(round(abs(value) / max_abs * bar_width)))
        rows.extend(
            [
                rf"\put(0,{y}){{\makebox[{label_width}pt][r]{{\scriptsize {_latex_escape(label)}}}}}",
                rf"\put({label_width + 8},{y + 2}){{\rule{{{width}pt}}{{6pt}}}}",
                rf"\put({value_x},{y}){{\makebox[48pt][l]{{\scriptsize {_format_metric(value)}}}}}",
            ]
        )
    rows.extend([r"\end{picture}", r"\endgroup"])
    return "\n".join(rows) + "\n"


def _figure_snippet(artifact: Artifact) -> str:
    input_path = _figure_input_path(artifact)
    caption = _latex_escape(str(artifact.extra.get("caption") or "Generated experiment figure."))
    label = _latex_label(str(artifact.extra.get("label") or f"fig:{artifact.id}"))
    return "\n".join(
        [
            r"\begin{figure}[t]",
            r"\centering",
            rf"\input{{{input_path}}}",
            rf"\caption{{{caption}}}",
            rf"\label{{{label}}}",
            r"\end{figure}",
        ]
    )


def _figure_input_path(artifact: Artifact) -> str:
    input_path = artifact.extra.get("input_path") if isinstance(artifact.extra, dict) else None
    if not isinstance(input_path, str):
        return ""
    return _safe_latex_path(input_path)


def _safe_storage_key(key: str) -> str:
    clean = SAFE_KEY_PATTERN.sub("-", key.strip().strip("/"))
    clean = "/".join(part for part in clean.split("/") if part and part != ".")
    if not clean or clean == ".." or "/../" in f"/{clean}/":
        raise PaperFigureGenerationError(f"unsafe figure storage key: {key}")
    return clean


def _safe_latex_path(path: str) -> str:
    clean = "/".join(part for part in path.strip().split("/") if part and part != ".")
    if not clean or clean == ".." or "/../" in f"/{clean}/":
        raise PaperFigureGenerationError(f"unsafe figure input path: {path}")
    return clean.replace("\\", "/")


def _latex_escape(value: str) -> str:
    return "".join(LATEX_SPECIALS.get(char, char) for char in value)


def _latex_label(value: str) -> str:
    label = re.sub(r"[^A-Za-z0-9:._-]+", "-", value.strip())
    return label or "fig:generated"


def _format_metric(value: float) -> str:
    if abs(value) >= 100:
        return f"{value:.1f}"
    if abs(value) >= 10:
        return f"{value:.2f}"
    return f"{value:.3g}"
