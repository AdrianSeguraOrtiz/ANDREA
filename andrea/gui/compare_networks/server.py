"""FastAPI server for the local compare-networks GUI."""

from __future__ import annotations

import shutil
import tempfile
import threading
import traceback
import uuid
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from andrea.core.commands.compare_networks import bundles as comparison_bundles
from andrea.core.commands.compare_networks import (
    compare_networks,
    export_edge_scores_csv_from_sqlite,
)
from andrea.core.commands.compare_networks.store import (
    distance_view,
    edge_variability,
    list_contexts,
)
from andrea.core.shared.json_io import write_json
from andrea.gui.common.reproducibility import (
    build_single_step_reproducibility_payload,
    python_path_expr,
    unavailable_reproducibility,
)
from andrea.gui.common.server_files import (
    build_bundle_metadata,
    build_zip_bundle,
    bundle_status_payload,
    extract_zip_path,
    load_strict_json_object,
    output_dir_from_form,
    read_json_if_exists,
    require_root_file,
    resolve_report_path,
    save_upload,
    uploaded_file,
)
from andrea.gui.common.server_jobs import (
    make_core_progress_callback,
    run_parallel,
    set_job_progress,
    start_background_thread,
    timed_job_stage,
    utc_now,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
COMMON_STATIC_DIR = Path(__file__).resolve().parents[1] / "common" / "static"
COMPARISON_VIEW_ASSETS_DIR = (
    Path(__file__).resolve().parents[2]
    / "core"
    / "commands"
    / "compare_networks"
    / "view_assets"
)
GUI_TMP_ROOT = Path(tempfile.gettempdir()) / "andrea_gui" / "compare_networks"


@dataclass
class GuiJob:
    job_id: str
    created_at: str
    status: str
    stage: str
    request_dir: str
    output_dir: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    selected_sources: list[dict[str, Any]] = field(default_factory=list)
    comparison_request_path: Optional[str] = None
    frozen_comparison_request_path: Optional[str] = None
    comparison_report_path: Optional[str] = None
    comparison_dir: Optional[str] = None
    error: Optional[str] = None
    traceback: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    progress_percent: int = 0
    progress_label: str = "Queued"
    progress_detail: str = ""
    timings: list[dict[str, Any]] = field(default_factory=list)
    artifact_status: dict[str, str] = field(default_factory=dict)
    artifact_errors: list[str] = field(default_factory=list)


class GuiState:
    def __init__(self) -> None:
        self.jobs: dict[str, GuiJob] = {}
        self.lock = threading.RLock()


STATE = GuiState()

COMPARISON_STAGE_PROGRESS = {
    "loading_request": 52,
    "loading_source_networks": 58,
    "freezing_inputs": 62,
    "building_network_tables": 68,
    "computing_distances": 74,
    "computing_coordinates": 78,
    "writing_request": 78,
    "writing_network_index_csv": 80,
    "writing_edge_scores_csv": 84,
    "writing_distances_csv": 88,
    "writing_distance_coordinates_csv": 90,
    "writing_comparison_sqlite": 94,
    "writing_report": 95,
    "exporting_edge_scores_csv": 98,
}


def _job_payload(job: GuiJob) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "created_at": job.created_at,
        "status": job.status,
        "stage": job.stage,
        "request_dir": job.request_dir,
        "output_dir": job.output_dir,
        "sources": job.sources,
        "selected_sources": job.selected_sources,
        "comparison_request_path": job.comparison_request_path,
        "frozen_comparison_request_path": job.frozen_comparison_request_path,
        "comparison_report_path": job.comparison_report_path,
        "comparison_dir": job.comparison_dir,
        "error": job.error,
        "traceback": job.traceback,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "progress_percent": job.progress_percent,
        "progress_label": job.progress_label,
        "progress_detail": job.progress_detail,
        "timings": list(job.timings),
        "artifact_status": dict(job.artifact_status),
        "artifact_errors": list(job.artifact_errors),
        "bundle_status": _job_bundle_status(job),
    }


def _job_response(job_id: str) -> dict[str, Any]:
    with STATE.lock:
        job = STATE.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        payload = _job_payload(job)
    return {
        "job": payload,
        "comparison_report": read_json_if_exists(payload.get("comparison_report_path")),
        "reproducibility": _build_reproducibility_payload(job),
    }

def _update_comparison_report_artifacts(
    *,
    report_path: Path,
    artifact_status: dict[str, str],
    artifact_errors: list[str],
) -> None:
    report = read_json_if_exists(str(report_path)) or {}
    if not isinstance(report, dict):
        return
    report["artifact_status"] = dict(artifact_status)
    report["artifact_errors"] = list(artifact_errors)
    write_json(report_path, report)


def _source_root(*, request_dir: Path, source_id: str, kind: str) -> Path:
    return request_dir / "sources" / source_id / kind


def _prepare_strict_inference_bundle(
    *, root: Path, source_label: str
) -> Path:
    run_report_path = require_root_file(
        root=root,
        rel_path="run_report.json",
        bundle_label="infer-network analysis",
        source_label=source_label,
    )
    require_root_file(
        root=root,
        rel_path="merged_network_normalized.csv",
        bundle_label="infer-network analysis",
        source_label=source_label,
    )
    run_report = load_strict_json_object(
        run_report_path,
        label="infer-network analysis run_report.json",
        source_label=source_label,
    )
    outputs = run_report.setdefault("outputs", {})
    if not isinstance(outputs, dict):
        outputs = {}
        run_report["outputs"] = outputs
    outputs["merged_network_normalized"] = "merged_network_normalized.csv"
    write_json(run_report_path, run_report)
    return run_report_path


def _prepare_strict_evaluation_bundle(
    *, root: Path, source_label: str
) -> Path:
    evaluation_report_path = require_root_file(
        root=root,
        rel_path="evaluation_report.json",
        bundle_label="evaluate-inference analysis",
        source_label=source_label,
    )
    load_strict_json_object(
        evaluation_report_path,
        label="evaluate-inference analysis evaluation_report.json",
        source_label=source_label,
    )
    return evaluation_report_path


def _extract_source_uploads(*, request_dir: Path, source: dict[str, Any]) -> None:
    source_id = str(source["source_id"])
    source_dir = request_dir / "sources" / source_id
    extract_zip_path(
        zip_path=source_dir / "uploads" / "inference.zip",
        extract_dir=source_dir / "inference",
    )
    if source.get("evaluation_uploaded"):
        extract_zip_path(
            zip_path=source_dir / "uploads" / "evaluation.zip",
            extract_dir=source_dir / "evaluation",
        )


def _validate_source_uploads(*, request_dir: Path, source: dict[str, Any]) -> None:
    source_id = str(source["source_id"])
    source_label = str(source.get("label") or source_id)
    source_dir = request_dir / "sources" / source_id
    _prepare_strict_inference_bundle(
        root=source_dir / "inference",
        source_label=source_label,
    )
    if source.get("evaluation_uploaded"):
        _prepare_strict_evaluation_bundle(
            root=source_dir / "evaluation",
            source_label=source_label,
        )


def _start_comparison_job(
    *,
    job_id: str,
    selected_sources: list[dict[str, Any]],
) -> None:
    with STATE.lock:
        job = STATE.jobs[job_id]
        job.status = "queued"
        job.stage = "queued"
        job.progress_percent = max(job.progress_percent, 10)
        job.progress_label = "Queued"
        job.progress_detail = "Waiting to start comparison."
        job.selected_sources = selected_sources
        job.frozen_comparison_request_path = None
        job.error = None
        job.traceback = None
        job.started_at = None
        job.finished_at = None
        job.artifact_status = {}
        job.artifact_errors = []
    start_background_thread(
        target=_run_comparison_job,
        kwargs={"job_id": job_id, "selected_sources": selected_sources},
        daemon=True,
    )


def _run_comparison_job(
    *,
    job_id: str,
    selected_sources: list[dict[str, Any]],
) -> None:
    with STATE.lock:
        job = STATE.jobs[job_id]
        job.status = "running"
        job.stage = "extracting_uploads"
        job.started_at = utc_now()
        request_dir = Path(job.request_dir)
        output_dir = Path(job.output_dir)
        sources = [dict(item) for item in job.sources]
    try:
        max_workers = max(1, min(4, len(sources) or 1))
        with timed_job_stage(
            state=STATE,
            job_id=job_id,
            stage="extracting_uploads",
            label="Extracting uploads",
            detail="Extracting source analysis ZIPs.",
            percent=25,
        ):
            run_parallel(
                [
                    lambda source=source: _extract_source_uploads(
                        request_dir=request_dir,
                        source=source,
                    )
                    for source in sources
                ],
                max_workers=max_workers,
            )
        with timed_job_stage(
            state=STATE,
            job_id=job_id,
            stage="validating_inputs",
            label="Validating inputs",
            detail="Checking strict analysis bundle layouts.",
            percent=45,
        ):
            run_parallel(
                [
                    lambda source=source: _validate_source_uploads(
                        request_dir=request_dir,
                        source=source,
                    )
                    for source in sources
                ],
                max_workers=max_workers,
            )
        request_id = f"gui_compare_{job_id}"
        comparison_dir = _create_gui_comparison_dir(
            output_dir=output_dir,
            request_id=request_id,
        )
        set_job_progress(
            state=STATE,
            job_id=job_id,
            stage="loading_inputs",
            label="Preparing comparison inputs",
            detail="Freezing validated source reports for comparison.",
            percent=50,
        )
        frozen_request_path = _freeze_comparison_inputs(
            request_dir=request_dir,
            selected_sources=selected_sources,
            comparison_dir=comparison_dir,
            request_id=request_id,
        )
        report = compare_networks(
            request_path=frozen_request_path,
            output_dir=output_dir,
            comparison_dir=comparison_dir,
            progress_callback=make_core_progress_callback(
                state=STATE,
                job_id=job_id,
                stage_progress=COMPARISON_STAGE_PROGRESS,
                default_percent=70,
            ),
            write_edge_scores_csv=False,
        )
        comparison_report_path = output_dir / str(
            report["outputs"]["comparison_report"]
        )
        comparison_dir = output_dir / str(report["outputs"]["comparison_dir"])
        with STATE.lock:
            job = STATE.jobs[job_id]
            job.status = "finalizing_artifacts"
            job.stage = "exporting_edge_scores_csv"
            job.progress_percent = 96
            job.progress_label = "Explorer Ready"
            job.progress_detail = "Comparison explorer is ready while edge_scores.csv is exported."
            job.comparison_request_path = str(frozen_request_path.resolve())
            job.frozen_comparison_request_path = str(frozen_request_path.resolve())
            job.comparison_report_path = str(comparison_report_path.resolve())
            job.comparison_dir = str(comparison_dir.resolve())
            job.artifact_status = {"edge_scores_csv": "exporting"}
        edge_scores_path = output_dir / str(report["outputs"]["edge_scores_csv"])
        if edge_scores_path.exists():
            edge_scores_path.unlink()
        try:
            with timed_job_stage(
                state=STATE,
                job_id=job_id,
                stage="exporting_edge_scores_csv",
                label="Exporting edge-score CSV",
                detail="Writing full edge_scores.csv artifact for full-archive downloads.",
                percent=98,
            ):
                export_edge_scores_csv_from_sqlite(
                    sqlite_path=output_dir / str(report["outputs"]["comparison_sqlite"]),
                    output_path=edge_scores_path,
                )
            with STATE.lock:
                job = STATE.jobs[job_id]
                job.artifact_status["edge_scores_csv"] = "ready"
        except Exception as exc:  # noqa: BLE001
            with STATE.lock:
                job = STATE.jobs[job_id]
                job.artifact_status["edge_scores_csv"] = "failed"
                job.artifact_errors.append(f"edge_scores.csv export failed: {exc}")
            _update_comparison_report_artifacts(
                report_path=comparison_report_path,
                artifact_status={"edge_scores_csv": "failed"},
                artifact_errors=[f"edge_scores.csv export failed: {exc}"],
            )
        else:
            _update_comparison_report_artifacts(
                report_path=comparison_report_path,
                artifact_status={"edge_scores_csv": "ready"},
                artifact_errors=[],
            )
        with STATE.lock:
            job = STATE.jobs[job_id]
            job.status = "completed"
            job.stage = "completed"
            job.progress_percent = 100
            job.progress_label = "Ready"
            if job.artifact_errors:
                job.progress_detail = "Comparison results are ready; one optional artifact failed."
            else:
                job.progress_detail = "Comparison results and downloadable artifacts are ready."
            job.finished_at = utc_now()
    except Exception as exc:  # noqa: BLE001
        with STATE.lock:
            job = STATE.jobs[job_id]
            job.status = "failed"
            job.stage = "failed"
            job.progress_percent = 100
            job.progress_label = "Failed"
            job.progress_detail = str(exc)
            job.error = str(exc)
            job.traceback = traceback.format_exc(limit=30)
            job.finished_at = utc_now()


def _build_reproducibility_payload(job: GuiJob) -> dict[str, Any]:
    if job.status not in {"completed", "finalizing_artifacts"}:
        return unavailable_reproducibility(
            "Reproducibility snippets will be available after execution."
        )
    if not job.frozen_comparison_request_path:
        return unavailable_reproducibility(
            "The frozen comparison request is not available for this job."
        )
    request_path = Path(job.frozen_comparison_request_path).resolve()
    if not request_path.exists():
        return unavailable_reproducibility(
            "The frozen comparison request no longer exists for this job."
        )
    output_dir = Path(job.output_dir).resolve()
    cli_args = [
        "andrea",
        "compare-networks",
        "--request",
        str(request_path),
        "--output-dir",
        str(output_dir),
    ]
    python_code = "\n".join(
        [
            "from pathlib import Path",
            "",
            "from andrea.core.commands.compare_networks import compare_networks",
            "",
            "report = compare_networks(",
            f"    request_path={python_path_expr(request_path)},",
            f"    output_dir={python_path_expr(output_dir)},",
            ")",
            "",
            "print(report['outputs']['comparison_dir'])",
        ]
    )
    return build_single_step_reproducibility_payload(
        cli_summary=(
            "Replay this GUI comparison using the frozen comparison request "
            "stored in the comparison output package."
        ),
        cli_args=cli_args,
        python_summary=(
            "Replay this GUI comparison using the current compare-networks "
            "Python API and the frozen comparison request."
        ),
        python_code=python_code,
    )


def _create_gui_comparison_dir(*, output_dir: Path, request_id: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dirname = f"comparison_{request_id}_{timestamp}"
    candidate = output_dir / dirname
    suffix = 2
    while candidate.exists():
        candidate = output_dir / f"{dirname}_{suffix:02d}"
        suffix += 1
    candidate.mkdir(parents=False, exist_ok=False)
    return candidate


def _freeze_comparison_inputs(
    *,
    request_dir: Path,
    selected_sources: list[dict[str, Any]],
    comparison_dir: Path,
    request_id: str,
) -> Path:
    input_dir = comparison_dir / "input"
    frozen_sources: list[dict[str, Any]] = []
    for selection in selected_sources:
        source_id = str(selection.get("source_id") or "")
        source_dir = input_dir / "sources" / source_id
        source_dir.mkdir(parents=True, exist_ok=True)
        run_report_path = (
            _source_root(request_dir=request_dir, source_id=source_id, kind="inference")
            / "run_report.json"
        )
        frozen_run_report_path = _freeze_run_report(
            run_report_path=run_report_path,
            destination_dir=source_dir,
        )
        frozen_source: dict[str, Any] = {
            "source_id": source_id,
            "label": str(selection.get("label") or source_id),
            "run_report": frozen_run_report_path.relative_to(comparison_dir).as_posix(),
        }
        evaluation_report = selection.get("evaluation_report")
        if isinstance(evaluation_report, str) and evaluation_report.strip():
            evaluation_path = (
                _source_root(
                    request_dir=request_dir,
                    source_id=source_id,
                    kind="evaluation",
                )
                / "evaluation_report.json"
            )
            frozen_evaluation_path = source_dir / "evaluation_report.json"
            shutil.copy2(evaluation_path, frozen_evaluation_path)
            frozen_source["evaluation_report"] = frozen_evaluation_path.relative_to(
                comparison_dir
            ).as_posix()
        frozen_sources.append(frozen_source)
    frozen_request_path = comparison_dir / "comparison-request.json"
    write_json(
        frozen_request_path,
        {
            "schema_version": "1.0",
            "id": request_id,
            "sources": frozen_sources,
        },
    )
    return frozen_request_path


def _freeze_run_report(*, run_report_path: Path, destination_dir: Path) -> Path:
    run_report = read_json_if_exists(run_report_path) or {}
    outputs = run_report.setdefault("outputs", {})
    if not isinstance(outputs, dict):
        outputs = {}
        run_report["outputs"] = outputs
    normalized_path = resolve_report_path(
        run_report_path, outputs.get("merged_network_normalized")
    )
    if normalized_path is None or not normalized_path.exists():
        raise ValueError(
            "Cannot freeze comparison input: run_report "
            f"outputs.merged_network_normalized is missing ({run_report_path})"
        )
    frozen_network = destination_dir / "merged_network_normalized.csv"
    shutil.copy2(normalized_path, frozen_network)
    outputs["merged_network_normalized"] = frozen_network.name
    frozen_report = destination_dir / "run_report.json"
    write_json(frozen_report, run_report)
    return frozen_report


def _resolve_bundle(*, comparison_dir: Optional[Path], bundle_id: str) -> Any:
    if comparison_dir is None or not comparison_dir.exists():
        raise ValueError("Comparison output is not ready")
    return comparison_bundles.resolve_bundle(
        bundle_id=bundle_id,
        comparison_dir=comparison_dir,
    )


def _edge_scores_artifact_status(job: GuiJob, comparison_dir: Optional[Path]) -> str:
    explicit = str(job.artifact_status.get("edge_scores_csv") or "").strip()
    if explicit:
        return explicit
    if comparison_dir is not None and (comparison_dir / "edge_scores.csv").is_file():
        return "ready"
    if job.status == "completed" and job.artifact_errors:
        return "failed"
    if job.status == "finalizing_artifacts":
        return "exporting"
    return "pending"


def _compare_bundle_readiness(
    *, bundle_id: str, job: GuiJob, comparison_dir: Optional[Path]
) -> list[dict[str, str]]:
    explorer_ready = bool(
        comparison_dir
        and job.comparison_report_path
        and (comparison_dir / "comparison.sqlite").is_file()
    )
    edge_status = _edge_scores_artifact_status(job, comparison_dir)
    if bundle_id == "full":
        return [
            {"label": "Explorer", "status": "ready" if explorer_ready else "pending"},
            {"label": "edge_scores.csv", "status": edge_status},
        ]
    if bundle_id == "report":
        return [
            {"label": "Explorer", "status": "ready" if explorer_ready else "pending"},
            {"label": "edge_scores.csv", "status": "not required"},
        ]
    return []


def _apply_compare_bundle_runtime_status(
    *,
    bundles: list[dict[str, Any]],
    job: GuiJob,
    comparison_dir: Optional[Path],
) -> list[dict[str, Any]]:
    edge_status = _edge_scores_artifact_status(job, comparison_dir)
    for bundle in bundles:
        bundle_id = str(bundle.get("id") or "")
        bundle["readiness"] = _compare_bundle_readiness(
            bundle_id=bundle_id,
            job=job,
            comparison_dir=comparison_dir,
        )
        if bundle_id == "full" and edge_status != "ready":
            bundle["available"] = False
            missing = list(bundle.get("missing_required") or [])
            reason = (
                "edge_scores.csv export failed"
                if edge_status == "failed"
                else "edge_scores.csv export is not complete"
            )
            if reason not in missing:
                missing.append(reason)
            bundle["missing_required"] = missing
    return bundles


def _job_bundle_status(job: GuiJob) -> dict[str, dict[str, Any]]:
    comparison_dir = Path(job.comparison_dir) if job.comparison_dir else None
    explorer_ready = bool(
        comparison_dir
        and job.comparison_report_path
        and (comparison_dir / "comparison.sqlite").is_file()
    )
    edge_status = _edge_scores_artifact_status(job, comparison_dir)
    return {
        "full": {
            "available": explorer_ready and edge_status == "ready",
            "state": "ready" if explorer_ready and edge_status == "ready" else "blocked",
            "missing_required": []
            if edge_status == "ready"
            else ["edge_scores.csv export is not complete"],
            "readiness": _compare_bundle_readiness(
                bundle_id="full",
                job=job,
                comparison_dir=comparison_dir,
            ),
        },
        "report": {
            "available": explorer_ready,
            "state": "ready" if explorer_ready else "blocked",
            "missing_required": [] if explorer_ready else ["Comparison output is not ready"],
            "readiness": _compare_bundle_readiness(
                bundle_id="report",
                job=job,
                comparison_dir=comparison_dir,
            ),
        },
    }


def _comparison_sqlite_path(job: GuiJob) -> Path:
    if job.status not in {"completed", "finalizing_artifacts"} or not job.comparison_report_path:
        raise ValueError("Comparison output is not ready")
    report = read_json_if_exists(job.comparison_report_path) or {}
    sqlite_rel = report.get("outputs", {}).get("comparison_sqlite")
    if not sqlite_rel:
        raise ValueError("Comparison SQLite store is not available")
    sqlite_path = Path(sqlite_rel)
    if not sqlite_path.is_absolute():
        sqlite_path = Path(job.output_dir) / sqlite_path
    sqlite_path = sqlite_path.resolve()
    if not sqlite_path.exists():
        raise ValueError("Comparison SQLite store no longer exists")
    return sqlite_path


def _get_completed_job(job_id: str) -> GuiJob:
    with STATE.lock:
        job = STATE.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status not in {"completed", "finalizing_artifacts"}:
            raise HTTPException(status_code=400, detail="Comparison output is not ready")
        return job


def _require_bundle_available(resolution: Any) -> None:
    if resolution.available and resolution.sources:
        return
    missing = ", ".join(resolution.missing_required) or "no files"
    raise ValueError(
        f"Bundle '{resolution.spec.id}' is not available; missing required files: {missing}"
    )


def _parse_source_count(form: Any) -> int:
    try:
        count = int(str(form.get("source_count") or "0"))
    except ValueError as exc:
        raise ValueError("source_count must be an integer") from exc
    if count <= 0:
        raise ValueError("At least one comparison source is required")
    return count


def create_app() -> FastAPI:
    app = FastAPI(title="ANDREA GUI - compare-networks")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.mount(
        "/static-common", StaticFiles(directory=COMMON_STATIC_DIR), name="static-common"
    )
    app.mount(
        "/comparison-view-assets",
        StaticFiles(directory=COMPARISON_VIEW_ASSETS_DIR),
        name="comparison-view-assets",
    )

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"}
        )

    @app.get("/api/compare-networks/jobs/{job_id}")
    async def api_job(job_id: str) -> JSONResponse:
        return JSONResponse(_job_response(job_id))

    @app.get("/api/compare-networks/jobs/{job_id}/bundles")
    async def api_job_bundles(job_id: str) -> JSONResponse:
        with STATE.lock:
            job = STATE.jobs.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Job not found")
            comparison_dir = Path(job.comparison_dir) if job.comparison_dir else None
            status = job.status
        output_ready = bool(comparison_dir and comparison_dir.exists())
        resolver = (
            (
                lambda bundle_id: comparison_bundles.resolve_bundle(
                    bundle_id=bundle_id,
                    comparison_dir=comparison_dir,  # type: ignore[arg-type]
                )
            )
            if output_ready
            else None
        )
        bundles = build_bundle_metadata(
            specs=comparison_bundles.bundle_specs(),
            resolver=resolver,
            unavailable_reason="Comparison output is not ready",
        )
        bundles = _apply_compare_bundle_runtime_status(
            bundles=bundles,
            job=job,
            comparison_dir=comparison_dir,
        )
        return JSONResponse(
            {
                "status": status,
                "output_ready": output_ready,
                "bundles": bundles,
                "bundle_status": bundle_status_payload(bundles),
            }
        )

    @app.get("/api/compare-networks/jobs/{job_id}/contexts")
    async def api_job_contexts(
        job_id: str,
        source_id: str,
        family: str,
        query: str = "",
        limit: int = 100,
    ) -> JSONResponse:
        try:
            sqlite_path = _comparison_sqlite_path(_get_completed_job(job_id))
            payload = list_contexts(
                sqlite_path,
                source_id=source_id,
                family=family,
                query=query,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(payload)

    @app.get("/api/compare-networks/jobs/{job_id}/distance-view")
    async def api_job_distance_view(
        job_id: str,
        source_id: str,
        context_family: str,
        distance_metric: str,
        evaluation_metric: Optional[str] = None,
        contexts: Optional[str] = None,
    ) -> JSONResponse:
        try:
            sqlite_path = _comparison_sqlite_path(_get_completed_job(job_id))
            selected_contexts = (
                [item.strip() for item in contexts.split(",") if item.strip()]
                if contexts
                else None
            )
            payload = distance_view(
                sqlite_path,
                source_id=source_id,
                context_family=context_family,
                distance_metric=distance_metric,
                evaluation_metric=evaluation_metric,
                contexts=selected_contexts,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(payload)

    @app.post("/api/compare-networks/jobs/{job_id}/edge-variability")
    async def api_job_edge_variability(job_id: str, request: Request) -> JSONResponse:
        try:
            body = await request.json()
            sqlite_path = _comparison_sqlite_path(_get_completed_job(job_id))
            payload = edge_variability(
                sqlite_path,
                selected_networks=list(body.get("selected_networks") or []),
                limit=int(body.get("limit") or 100),
                evaluation_metric=body.get("evaluation_metric"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(payload)

    @app.post("/api/compare-networks/run")
    async def api_run(request: Request) -> JSONResponse:
        form = await request.form()
        try:
            source_count = _parse_source_count(form)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        job_id = uuid.uuid4().hex[:12]
        request_dir = (GUI_TMP_ROOT / job_id).resolve()
        output_dir = output_dir_from_form(form, default="./comparisons")
        request_dir.mkdir(parents=True, exist_ok=True)
        sources: list[dict[str, Any]] = []
        selected_sources: list[dict[str, Any]] = []
        uploads_to_save: list[tuple[Any, Path]] = []

        try:
            for idx in range(source_count):
                source_id = f"source_{idx + 1}"
                label = str(form.get(f"source_{idx}_label") or source_id).strip()
                source_label = label or source_id
                inference_upload = uploaded_file(form, f"source_{idx}_inference_zip")
                evaluation_upload = uploaded_file(form, f"source_{idx}_evaluation_zip")
                if inference_upload is None:
                    raise ValueError(f"source_{idx}_inference_zip is required")

                source_dir = request_dir / "sources" / source_id
                uploads_to_save.append(
                    (inference_upload, source_dir / "uploads" / "inference.zip")
                )

                selected_evaluation_report = None
                if evaluation_upload is not None:
                    uploads_to_save.append(
                        (evaluation_upload, source_dir / "uploads" / "evaluation.zip")
                    )
                    selected_evaluation_report = "evaluation_report.json"

                sources.append(
                    {
                        "source_id": source_id,
                        "label": source_label,
                        "evaluation_uploaded": evaluation_upload is not None,
                    }
                )
                selected_sources.append(
                    {
                        "source_id": source_id,
                        "label": source_label,
                        "run_report": "run_report.json",
                        "evaluation_report": selected_evaluation_report,
                    }
                )
        except ValueError as exc:
            shutil.rmtree(request_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        job = GuiJob(
            job_id=job_id,
            created_at=utc_now(),
            status="queued",
            stage="saving_uploads",
            request_dir=str(request_dir),
            output_dir=str(output_dir),
            sources=sources,
            progress_percent=0,
            progress_label="Saving uploads",
        )
        with STATE.lock:
            STATE.jobs[job_id] = job
        try:
            with timed_job_stage(
                state=STATE,
                job_id=job_id,
                stage="saving_uploads",
                label="Saving uploads",
                detail="Saving source ZIPs to the local request directory.",
                percent=10,
            ):
                for upload, destination in uploads_to_save:
                    save_upload(upload, destination)
        except Exception as exc:  # noqa: BLE001
            with STATE.lock:
                job = STATE.jobs[job_id]
                job.status = "failed"
                job.stage = "failed"
                job.progress_percent = 100
                job.progress_label = "Failed"
                job.progress_detail = str(exc)
                job.error = str(exc)
                job.traceback = traceback.format_exc(limit=30)
                job.finished_at = utc_now()
            return JSONResponse(_job_response(job_id))

        _start_comparison_job(job_id=job_id, selected_sources=selected_sources)
        return JSONResponse(_job_response(job_id))

    @app.get("/api/compare-networks/jobs/{job_id}/bundle")
    async def api_job_bundle(
        job_id: str,
        bundle_id: str = "full",
    ) -> FileResponse:
        with STATE.lock:
            job = STATE.jobs.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Job not found")
            comparison_dir = Path(job.comparison_dir) if job.comparison_dir else None
            request_dir = Path(job.request_dir)
        try:
            resolution = _resolve_bundle(
                comparison_dir=comparison_dir,
                bundle_id=bundle_id,
            )
            if (
                bundle_id == "full"
                and _edge_scores_artifact_status(job, comparison_dir) != "ready"
            ):
                raise ValueError("edge_scores.csv export is not complete")
            _require_bundle_available(resolution)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        zip_path = request_dir / f"{job_id}_comparison_{resolution.spec.id}.zip"
        build_zip_bundle(zip_path=zip_path, sources=resolution.source_tuples)
        return FileResponse(
            zip_path,
            media_type="application/zip",
            filename=f"andrea_comparison_{job_id}_{resolution.spec.id}.zip",
        )

    return app


def run_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8768,
    open_browser: bool = False,
) -> None:
    url = f"http://{host}:{port}"
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(), host=host, port=port)
