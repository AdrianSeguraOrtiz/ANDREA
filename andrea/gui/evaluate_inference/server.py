"""FastAPI server for the local evaluate-inference GUI."""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
import traceback
import uuid
import webbrowser
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from andrea.core.commands.evaluate_inference import bundles as evaluation_bundles
from andrea.core.commands.evaluate_inference import (
    evaluate_inference,
    validate_inference_analysis_inputs,
)
from andrea.core.shared.json_io import validate_json_instance
from andrea.core.shared.output_capabilities import validate_frozen_output_capabilities
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
    save_upload,
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
EVALUATION_VIEW_ASSETS_DIR = (
    Path(__file__).resolve().parents[2]
    / "core"
    / "commands"
    / "evaluate_inference"
    / "view_assets"
)
GUI_TMP_ROOT = Path(tempfile.gettempdir()) / "andrea_gui" / "evaluate_inference"
GROUND_TRUTH_SCHEMA_PACKAGE = "andrea.catalog_simulation_data_tools"
GROUND_TRUTH_SCHEMA_RESOURCE = "schemas/ground-truth-manifest.schema.json"


@dataclass
class GuiJob:
    job_id: str
    created_at: str
    status: str
    stage: str
    request_dir: str
    output_dir: str
    frozen_run_report_path: Optional[str] = None
    frozen_ground_truth_manifest_path: Optional[str] = None
    evaluation_report_path: Optional[str] = None
    evaluation_dir: Optional[str] = None
    error: Optional[str] = None
    traceback: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    progress_percent: int = 0
    progress_label: str = "Queued"
    progress_detail: str = ""
    timings: list[dict[str, Any]] = field(default_factory=list)


class GuiState:
    def __init__(self) -> None:
        self.jobs: dict[str, GuiJob] = {}
        self.lock = threading.RLock()


STATE = GuiState()


def _validate_strict_candidate_space(
    manifest: dict[str, Any], *, label: str
) -> dict[str, Any]:
    candidate_space = manifest.get("candidate_space")
    expected_keys = {"sources", "targets", "allow_self_edges"}
    if not isinstance(candidate_space, dict) or set(candidate_space) != expected_keys:
        raise ValueError(
            f"Invalid {label}: candidate_space must contain exactly sources, "
            "targets and allow_self_edges"
        )
    for key in ("sources", "targets"):
        reference = candidate_space[key]
        if (
            not isinstance(reference, str)
            or not reference
            or reference != reference.strip()
        ):
            raise ValueError(
                f"Invalid {label}: candidate_space.{key} must be a non-empty "
                "path without surrounding whitespace"
            )
    if candidate_space["allow_self_edges"] is not False:
        raise ValueError(
            f"Invalid {label}: candidate_space.allow_self_edges must be false"
        )
    return candidate_space


def _validate_ground_truth_manifest_schema(
    manifest: dict[str, Any], *, label: str
) -> None:
    schema = json.loads(
        resources.files(GROUND_TRUTH_SCHEMA_PACKAGE)
        .joinpath(GROUND_TRUTH_SCHEMA_RESOURCE)
        .read_text(encoding="utf-8")
    )
    validate_json_instance(instance=manifest, schema=schema, label=label)


EVALUATION_STAGE_PROGRESS = {
    "loading_run_report": 52,
    "loading_inferred_network": 58,
    "loading_truth_networks": 64,
    "preparing_evaluation_inputs": 68,
    "computing": 75,
    "writing_outputs": 92,
}


def _job_payload(job: GuiJob) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "created_at": job.created_at,
        "status": job.status,
        "stage": job.stage,
        "request_dir": job.request_dir,
        "output_dir": job.output_dir,
        "frozen_run_report_path": job.frozen_run_report_path,
        "frozen_ground_truth_manifest_path": job.frozen_ground_truth_manifest_path,
        "evaluation_report_path": job.evaluation_report_path,
        "evaluation_dir": job.evaluation_dir,
        "error": job.error,
        "traceback": job.traceback,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "progress_percent": job.progress_percent,
        "progress_label": job.progress_label,
        "progress_detail": job.progress_detail,
        "timings": list(job.timings),
        "artifact_errors": [],
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
        "evaluation_report": read_json_if_exists(payload.get("evaluation_report_path")),
        "reproducibility": _build_reproducibility_payload(job),
    }


def _prepare_strict_inference_bundle(root: Path) -> Path:
    run_report_path = require_root_file(
        root=root,
        rel_path="run_report.json",
        bundle_label="infer-network analysis",
    )
    require_root_file(
        root=root,
        rel_path="merged_network_raw.csv",
        bundle_label="infer-network analysis",
    )
    run_report = load_strict_json_object(
        run_report_path, label="infer-network analysis run_report.json"
    )
    validate_frozen_output_capabilities(
        run_report.get("tools"),
        label="infer-network analysis run_report.json tools",
    )
    outputs = run_report.get("outputs")
    if not isinstance(outputs, dict):
        raise ValueError(
            "Invalid infer-network analysis run_report.json: outputs must be an object"
        )
    raw_reference = outputs.get("merged_network_raw")
    if raw_reference != "merged_network_raw.csv":
        raise ValueError(
            "Invalid infer-network analysis run_report.json: "
            "outputs.merged_network_raw must be exactly "
            "'merged_network_raw.csv'"
        )
    validate_inference_analysis_inputs(run_report_path=run_report_path)
    return run_report_path


def _prepare_strict_truth_bundle(root: Path) -> Path:
    truth_manifest_path = require_root_file(
        root=root,
        rel_path="ground-truth-manifest.json",
        bundle_label="generate-data analysis",
    )
    require_root_file(
        root=root,
        rel_path="truth/networks.csv",
        bundle_label="generate-data analysis",
    )
    require_root_file(
        root=root,
        rel_path="truth/gene_universe.txt",
        bundle_label="generate-data analysis",
    )
    manifest = load_strict_json_object(
        truth_manifest_path, label="generate-data analysis ground-truth-manifest.json"
    )
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        raise ValueError(
            "Invalid generate-data analysis ground-truth-manifest.json: "
            "outputs must be an object"
        )
    expected_references = {
        "networks": "truth/networks.csv",
        "gene_universe": "truth/gene_universe.txt",
    }
    for key, expected_reference in expected_references.items():
        if outputs.get(key) != expected_reference:
            raise ValueError(
                "Invalid generate-data analysis ground-truth-manifest.json: "
                f"outputs.{key} must be exactly '{expected_reference}'"
            )
    candidate_space = _validate_strict_candidate_space(
        manifest, label="generate-data analysis ground-truth-manifest.json"
    )
    for key in ("sources", "targets"):
        reference = candidate_space[key]
        require_root_file(
            root=root,
            rel_path=reference,
            bundle_label="generate-data analysis",
            source_label=f"candidate_space.{key}",
        )
    _validate_ground_truth_manifest_schema(
        manifest,
        label="Generate-data analysis ground-truth-manifest.json",
    )
    return truth_manifest_path


def _validate_strict_uploads(request_dir: Path) -> tuple[Path, Path]:
    run_report_path = _prepare_strict_inference_bundle(request_dir / "inference")
    truth_manifest_path = _prepare_strict_truth_bundle(request_dir / "truth")
    return run_report_path, truth_manifest_path


def _start_evaluation_job(
    *,
    job_id: str,
) -> None:
    with STATE.lock:
        job = STATE.jobs[job_id]
        job.status = "queued"
        job.stage = "queued"
        job.progress_percent = max(job.progress_percent, 10)
        job.progress_label = "Queued"
        job.progress_detail = "Waiting to start evaluation."
        job.frozen_run_report_path = None
        job.frozen_ground_truth_manifest_path = None
        job.error = None
        job.traceback = None
        job.started_at = None
        job.finished_at = None
    start_background_thread(
        target=_run_evaluation_job,
        kwargs={"job_id": job_id},
        daemon=True,
    )


def _run_evaluation_job(*, job_id: str) -> None:
    with STATE.lock:
        job = STATE.jobs[job_id]
        job.status = "running"
        job.stage = "extracting_uploads"
        job.started_at = utc_now()
        request_dir = Path(job.request_dir)
        output_dir = Path(job.output_dir)
    try:
        with timed_job_stage(
            state=STATE,
            job_id=job_id,
            stage="extracting_uploads",
            label="Extracting uploads",
            detail="Extracting infer-network and generate-data analysis ZIPs.",
            percent=25,
        ):
            run_parallel(
                [
                    lambda: extract_zip_path(
                        zip_path=request_dir / "uploads" / "inference.zip",
                        extract_dir=request_dir / "inference",
                    ),
                    lambda: extract_zip_path(
                        zip_path=request_dir / "uploads" / "truth.zip",
                        extract_dir=request_dir / "truth",
                    ),
                ],
                max_workers=2,
            )
        with timed_job_stage(
            state=STATE,
            job_id=job_id,
            stage="validating_inputs",
            label="Validating inputs",
            detail="Checking strict analysis bundle layouts.",
            percent=45,
        ):
            _validate_strict_uploads(request_dir)
        run_report_path = request_dir / "inference" / "run_report.json"
        truth_manifest_path = request_dir / "truth" / "ground-truth-manifest.json"
        report = evaluate_inference(
            run_report_path=run_report_path,
            ground_truth_manifest_path=truth_manifest_path,
            output_dir=output_dir,
            generate_view=True,
            progress_callback=make_core_progress_callback(
                state=STATE,
                job_id=job_id,
                stage_progress=EVALUATION_STAGE_PROGRESS,
                default_percent=70,
            ),
        )
        evaluation_report = output_dir / str(report["outputs"]["evaluation_report"])
        evaluation_dir = output_dir / str(report["outputs"]["evaluation_dir"])
        set_job_progress(
            state=STATE,
            job_id=job_id,
            stage="writing_outputs",
            label="Finalizing output",
            detail="Freezing reproducibility inputs.",
            percent=96,
        )
        frozen_run_report_path, frozen_truth_manifest_path = _freeze_evaluation_inputs(
            evaluation_dir=evaluation_dir,
            run_report_path=run_report_path,
            truth_manifest_path=truth_manifest_path,
        )
        with STATE.lock:
            job = STATE.jobs[job_id]
            job.status = "completed"
            job.stage = "completed"
            job.progress_percent = 100
            job.progress_label = "Ready"
            job.progress_detail = "Evaluation results are ready."
            job.frozen_run_report_path = str(frozen_run_report_path.resolve())
            job.frozen_ground_truth_manifest_path = str(
                frozen_truth_manifest_path.resolve()
            )
            job.evaluation_report_path = str(evaluation_report.resolve())
            job.evaluation_dir = str(evaluation_dir.resolve())
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
    if job.status != "completed":
        return unavailable_reproducibility(
            "Reproducibility snippets will be available after execution."
        )
    if not job.frozen_run_report_path or not job.frozen_ground_truth_manifest_path:
        return unavailable_reproducibility(
            "Frozen evaluation inputs are not available for this job."
        )
    run_report_path = Path(job.frozen_run_report_path).resolve()
    truth_manifest_path = Path(job.frozen_ground_truth_manifest_path).resolve()
    if not run_report_path.exists() or not truth_manifest_path.exists():
        return unavailable_reproducibility(
            "Frozen evaluation inputs no longer exist for this job."
        )
    output_dir = Path(job.output_dir).resolve()
    cli_args = [
        "andrea",
        "evaluate-inference",
        "--run-report",
        str(run_report_path),
        "--ground-truth-manifest",
        str(truth_manifest_path),
        "--output-dir",
        str(output_dir),
        "--view",
    ]
    python_code = "\n".join(
        [
            "from pathlib import Path",
            "",
            "from andrea.core.commands.evaluate_inference import evaluate_inference",
            "",
            "report = evaluate_inference(",
            f"    run_report_path={python_path_expr(run_report_path)},",
            f"    ground_truth_manifest_path={python_path_expr(truth_manifest_path)},",
            f"    output_dir={python_path_expr(output_dir)},",
            "    generate_view=True,",
            ")",
            "",
            "print(report['outputs']['evaluation_dir'])",
        ]
    )
    return build_single_step_reproducibility_payload(
        cli_summary=(
            "Replay this GUI evaluation using the frozen inputs stored in the "
            "evaluation output package."
        ),
        cli_args=cli_args,
        python_summary=(
            "Replay this GUI evaluation using the current evaluate-inference "
            "Python API and the frozen inputs stored in the output package."
        ),
        python_code=python_code,
    )


def _freeze_evaluation_inputs(
    *,
    evaluation_dir: Path,
    run_report_path: Path,
    truth_manifest_path: Path,
) -> tuple[Path, Path]:
    input_dir = evaluation_dir / "input"
    frozen_run_report = _freeze_run_report(
        run_report_path=run_report_path,
        destination_dir=input_dir / "inference",
    )
    frozen_truth_manifest = _freeze_truth_manifest(
        truth_manifest_path=truth_manifest_path,
        destination_dir=input_dir / "ground_truth",
    )
    return frozen_run_report, frozen_truth_manifest


def _freeze_run_report(*, run_report_path: Path, destination_dir: Path) -> Path:
    run_report = load_strict_json_object(
        run_report_path,
        label="infer-network analysis run_report.json to freeze",
    )
    validate_inference_analysis_inputs(run_report_path=run_report_path)
    validate_frozen_output_capabilities(
        run_report.get("tools"),
        label="run_report.json tools to freeze",
    )
    outputs = run_report.get("outputs")
    if not isinstance(outputs, dict):
        raise ValueError(
            "Cannot freeze evaluation input: run_report outputs must be an object"
        )
    if outputs.get("merged_network_raw") != "merged_network_raw.csv":
        raise ValueError(
            "Cannot freeze evaluation input: run_report "
            "outputs.merged_network_raw must be exactly "
            f"'merged_network_raw.csv' ({run_report_path})"
        )
    raw_path = require_root_file(
        root=run_report_path.parent,
        rel_path="merged_network_raw.csv",
        bundle_label="infer-network analysis",
    )
    destination_dir.mkdir(parents=True, exist_ok=True)
    frozen_network = destination_dir / "merged_network_raw.csv"
    shutil.copy2(raw_path, frozen_network)
    frozen_report = destination_dir / "run_report.json"
    frozen_report.write_text(
        json.dumps(run_report, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return frozen_report


def _freeze_truth_manifest(*, truth_manifest_path: Path, destination_dir: Path) -> Path:
    manifest = load_strict_json_object(
        truth_manifest_path,
        label="generate-data analysis ground-truth-manifest.json to freeze",
    )
    _validate_ground_truth_manifest_schema(
        manifest,
        label="Ground-truth manifest to freeze",
    )
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        raise ValueError(
            "Cannot freeze evaluation input: ground_truth_manifest outputs must be an object"
        )
    expected_outputs = {
        "gene_universe": "truth/gene_universe.txt",
        "networks": "truth/networks.csv",
    }
    if outputs != expected_outputs:
        raise ValueError(
            "Cannot freeze evaluation input: ground_truth_manifest outputs must "
            "contain exactly the canonical gene_universe and networks references"
        )
    gene_universe_path = require_root_file(
        root=truth_manifest_path.parent,
        rel_path="truth/gene_universe.txt",
        bundle_label="generate-data analysis",
    )
    networks_path = require_root_file(
        root=truth_manifest_path.parent,
        rel_path="truth/networks.csv",
        bundle_label="generate-data analysis",
    )
    frozen_gene_universe = destination_dir / "truth" / "gene_universe.txt"
    frozen_gene_universe.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(gene_universe_path, frozen_gene_universe)
    frozen_networks = destination_dir / "truth" / "networks.csv"
    shutil.copy2(networks_path, frozen_networks)
    candidate_space = _validate_strict_candidate_space(
        manifest, label="ground_truth_manifest to freeze"
    )
    frozen_references = {
        "sources": "candidate_space/sources.txt",
        "targets": "candidate_space/targets.txt",
    }
    for key, frozen_reference in frozen_references.items():
        source_path = require_root_file(
            root=truth_manifest_path.parent,
            rel_path=candidate_space[key],
            bundle_label="generate-data analysis",
            source_label=f"candidate_space.{key}",
        )
        if source_path == gene_universe_path:
            candidate_space[key] = "truth/gene_universe.txt"
            continue
        frozen_path = destination_dir / frozen_reference
        frozen_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, frozen_path)
        candidate_space[key] = frozen_reference
    destination_dir.mkdir(parents=True, exist_ok=True)
    frozen_manifest = destination_dir / "ground-truth-manifest.json"
    frozen_manifest.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return frozen_manifest


def _resolve_bundle(*, evaluation_dir: Optional[Path], bundle_id: str) -> Any:
    if evaluation_dir is None or not evaluation_dir.exists():
        raise ValueError("Evaluation output is not ready")
    return evaluation_bundles.resolve_bundle(
        bundle_id=bundle_id,
        evaluation_dir=evaluation_dir,
    )


def _require_bundle_available(resolution: Any) -> None:
    if resolution.available and resolution.sources:
        return
    missing = ", ".join(resolution.missing_required) or "no files"
    raise ValueError(
        f"Bundle '{resolution.spec.id}' is not available; missing required files: {missing}"
    )


def _job_bundle_status(job: GuiJob) -> dict[str, dict[str, Any]]:
    evaluation_dir = Path(job.evaluation_dir) if job.evaluation_dir else None
    output_ready = bool(evaluation_dir and evaluation_dir.exists())
    resolver = (
        (
            lambda bundle_id: evaluation_bundles.resolve_bundle(
                bundle_id=bundle_id,
                evaluation_dir=evaluation_dir,  # type: ignore[arg-type]
            )
        )
        if output_ready
        else None
    )
    bundles = build_bundle_metadata(
        specs=evaluation_bundles.bundle_specs(),
        resolver=resolver,
        unavailable_reason="Evaluation output is not ready",
    )
    return bundle_status_payload(bundles)


def create_app() -> FastAPI:
    app = FastAPI(title="ANDREA GUI - evaluate-inference")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.mount(
        "/static-common", StaticFiles(directory=COMMON_STATIC_DIR), name="static-common"
    )
    app.mount(
        "/evaluation-view-assets",
        StaticFiles(directory=EVALUATION_VIEW_ASSETS_DIR),
        name="evaluation-view-assets",
    )

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"}
        )

    @app.get("/api/evaluate-inference/jobs/{job_id}")
    async def api_job(job_id: str) -> JSONResponse:
        return JSONResponse(_job_response(job_id))

    @app.get("/api/evaluate-inference/jobs/{job_id}/bundles")
    async def api_job_bundles(job_id: str) -> JSONResponse:
        with STATE.lock:
            job = STATE.jobs.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Job not found")
            evaluation_dir = Path(job.evaluation_dir) if job.evaluation_dir else None
            status = job.status
        output_ready = bool(evaluation_dir and evaluation_dir.exists())
        resolver = (
            (
                lambda bundle_id: evaluation_bundles.resolve_bundle(
                    bundle_id=bundle_id,
                    evaluation_dir=evaluation_dir,  # type: ignore[arg-type]
                )
            )
            if output_ready
            else None
        )
        bundles = build_bundle_metadata(
            specs=evaluation_bundles.bundle_specs(),
            resolver=resolver,
            unavailable_reason="Evaluation output is not ready",
        )
        return JSONResponse(
            {
                "status": status,
                "output_ready": output_ready,
                "bundles": bundles,
                "bundle_status": bundle_status_payload(bundles),
            }
        )

    @app.post("/api/evaluate-inference/run")
    async def api_run(request: Request) -> JSONResponse:
        form = await request.form()
        inference_upload = form.get("inference_zip")
        truth_upload = form.get("truth_zip")
        if inference_upload is None or not getattr(inference_upload, "filename", ""):
            raise HTTPException(status_code=400, detail="inference_zip is required")
        if truth_upload is None or not getattr(truth_upload, "filename", ""):
            raise HTTPException(status_code=400, detail="truth_zip is required")

        job_id = uuid.uuid4().hex[:12]
        request_dir = (GUI_TMP_ROOT / job_id).resolve()
        output_dir = output_dir_from_form(form, default="./evaluations")
        request_dir.mkdir(parents=True, exist_ok=True)

        job = GuiJob(
            job_id=job_id,
            created_at=utc_now(),
            status="queued",
            stage="saving_uploads",
            request_dir=str(request_dir),
            output_dir=str(output_dir),
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
                detail="Saving uploaded ZIPs to the local request directory.",
                percent=10,
            ):
                save_upload(inference_upload, request_dir / "uploads" / "inference.zip")
                save_upload(truth_upload, request_dir / "uploads" / "truth.zip")
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

        _start_evaluation_job(job_id=job_id)
        return JSONResponse(_job_response(job_id))

    @app.get("/api/evaluate-inference/jobs/{job_id}/bundle")
    async def api_job_bundle(
        job_id: str,
        bundle_id: str = "full",
    ) -> FileResponse:
        with STATE.lock:
            job = STATE.jobs.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Job not found")
            evaluation_dir = Path(job.evaluation_dir) if job.evaluation_dir else None
            request_dir = Path(job.request_dir)
        try:
            resolution = _resolve_bundle(
                evaluation_dir=evaluation_dir,
                bundle_id=bundle_id,
            )
            _require_bundle_available(resolution)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        zip_path = request_dir / f"{job_id}_evaluation_{resolution.spec.id}.zip"
        build_zip_bundle(zip_path=zip_path, sources=resolution.source_tuples)
        return FileResponse(
            zip_path,
            media_type="application/zip",
            filename=f"andrea_evaluation_{job_id}_{resolution.spec.id}.zip",
        )

    return app


def run_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8767,
    open_browser: bool = False,
) -> None:
    url = f"http://{host}:{port}"
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(), host=host, port=port)
