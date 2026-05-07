"""FastAPI server for the local evaluate-inference GUI."""

from __future__ import annotations

import json
import re
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

from andrea.core.commands.evaluate_inference import evaluate_inference
from andrea.gui.common.reproducibility import (
    build_single_step_reproducibility_payload,
    python_path_expr,
    unavailable_reproducibility,
)
from andrea.gui.common.server_files import (
    build_zip_bundle,
    extract_zip_upload,
    output_dir_from_form,
    read_json_if_exists,
    resolve_report_path,
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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class GuiJob:
    job_id: str
    created_at: str
    status: str
    stage: str
    request_dir: str
    output_dir: str
    run_candidates: list[dict[str, Any]] = field(default_factory=list)
    truth_candidates: list[dict[str, Any]] = field(default_factory=list)
    selected_run_report: Optional[str] = None
    selected_ground_truth_manifest: Optional[str] = None
    frozen_run_report_path: Optional[str] = None
    frozen_ground_truth_manifest_path: Optional[str] = None
    evaluation_report_path: Optional[str] = None
    evaluation_dir: Optional[str] = None
    error: Optional[str] = None
    traceback: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class GuiState:
    def __init__(self) -> None:
        self.jobs: dict[str, GuiJob] = {}
        self.lock = threading.RLock()


STATE = GuiState()


def _job_payload(job: GuiJob) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "created_at": job.created_at,
        "status": job.status,
        "stage": job.stage,
        "request_dir": job.request_dir,
        "output_dir": job.output_dir,
        "run_candidates": job.run_candidates,
        "truth_candidates": job.truth_candidates,
        "selected_run_report": job.selected_run_report,
        "selected_ground_truth_manifest": job.selected_ground_truth_manifest,
        "frozen_run_report_path": job.frozen_run_report_path,
        "frozen_ground_truth_manifest_path": job.frozen_ground_truth_manifest_path,
        "evaluation_report_path": job.evaluation_report_path,
        "evaluation_dir": job.evaluation_dir,
        "error": job.error,
        "traceback": job.traceback,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
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


def _discover_run_candidates(root: Path) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for path in sorted(root.rglob("run_report.json")):
        if not path.is_file():
            continue
        payload = read_json_if_exists(path)
        if not payload:
            continue
        outputs = payload.get("outputs")
        if not isinstance(outputs, dict) or not outputs.get("merged_network_raw"):
            continue
        dataset = payload.get("dataset") if isinstance(payload.get("dataset"), dict) else {}
        rel = path.relative_to(root).as_posix()
        candidates.append(
            {
                "path": rel,
                "label": rel,
                "run_id": payload.get("run_id"),
                "status": payload.get("status"),
                "dataset_id": dataset.get("id") if isinstance(dataset, dict) else None,
            }
        )
    return candidates


def _discover_truth_candidates(root: Path) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for path in sorted(root.rglob("ground-truth-manifest.json")):
        if not path.is_file():
            continue
        payload = read_json_if_exists(path)
        if not payload:
            continue
        outputs = payload.get("outputs")
        if not isinstance(outputs, dict):
            continue
        if not outputs.get("global_network") and not outputs.get("group_networks"):
            continue
        rel = path.relative_to(root).as_posix()
        candidates.append(
            {
                "path": rel,
                "label": rel,
                "dataset_id": payload.get("dataset_id"),
                "simulator_id": payload.get("simulator_id"),
                "profile": payload.get("profile"),
            }
        )
    return candidates


def _auto_selection(
    *,
    run_candidates: list[dict[str, Any]],
    truth_candidates: list[dict[str, Any]],
) -> Optional[tuple[str, str]]:
    if len(run_candidates) == 1 and len(truth_candidates) == 1:
        return str(run_candidates[0]["path"]), str(truth_candidates[0]["path"])

    pairs: list[tuple[str, str]] = []
    for run in run_candidates:
        run_dataset = run.get("dataset_id")
        if not run_dataset:
            continue
        for truth in truth_candidates:
            if run_dataset == truth.get("dataset_id"):
                pairs.append((str(run["path"]), str(truth["path"])))
    unique_pairs = sorted(set(pairs))
    if len(unique_pairs) == 1:
        return unique_pairs[0]
    return None


def _candidate_path(
    *,
    request_dir: Path,
    kind: str,
    selected_path: str,
    candidates: list[dict[str, Any]],
) -> Path:
    allowed = {str(candidate["path"]) for candidate in candidates}
    if selected_path not in allowed:
        raise ValueError(f"Selected {kind} path is not one of the detected candidates")
    root = request_dir / kind
    path = (root / selected_path).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"Selected {kind} path escapes the extracted archive")
    if not path.exists() or not path.is_file():
        raise ValueError(f"Selected {kind} file no longer exists: {selected_path}")
    return path

def _start_evaluation_job(
    *,
    job_id: str,
    run_report: str,
    ground_truth_manifest: str,
) -> None:
    with STATE.lock:
        job = STATE.jobs[job_id]
        job.status = "queued"
        job.stage = "queued"
        job.selected_run_report = run_report
        job.selected_ground_truth_manifest = ground_truth_manifest
        job.frozen_run_report_path = None
        job.frozen_ground_truth_manifest_path = None
        job.error = None
        job.traceback = None
        job.started_at = None
        job.finished_at = None
    threading.Thread(
        target=_run_evaluation_job,
        kwargs={
            "job_id": job_id,
            "run_report": run_report,
            "ground_truth_manifest": ground_truth_manifest,
        },
        daemon=True,
    ).start()


def _run_evaluation_job(
    *,
    job_id: str,
    run_report: str,
    ground_truth_manifest: str,
) -> None:
    with STATE.lock:
        job = STATE.jobs[job_id]
        job.status = "running"
        job.stage = "evaluating"
        job.started_at = _utc_now()
        request_dir = Path(job.request_dir)
        output_dir = Path(job.output_dir)
        run_candidates = list(job.run_candidates)
        truth_candidates = list(job.truth_candidates)
    try:
        run_report_path = _candidate_path(
            request_dir=request_dir,
            kind="inference",
            selected_path=run_report,
            candidates=run_candidates,
        )
        truth_manifest_path = _candidate_path(
            request_dir=request_dir,
            kind="truth",
            selected_path=ground_truth_manifest,
            candidates=truth_candidates,
        )
        report = evaluate_inference(
            run_report_path=run_report_path,
            ground_truth_manifest_path=truth_manifest_path,
            output_dir=output_dir,
            generate_view=True,
        )
        evaluation_report = output_dir / str(report["outputs"]["evaluation_report"])
        evaluation_dir = output_dir / str(report["outputs"]["evaluation_dir"])
        frozen_run_report_path, frozen_truth_manifest_path = _freeze_evaluation_inputs(
            evaluation_dir=evaluation_dir,
            run_report_path=run_report_path,
            truth_manifest_path=truth_manifest_path,
        )
        with STATE.lock:
            job = STATE.jobs[job_id]
            job.status = "completed"
            job.stage = "completed"
            job.frozen_run_report_path = str(frozen_run_report_path.resolve())
            job.frozen_ground_truth_manifest_path = str(
                frozen_truth_manifest_path.resolve()
            )
            job.evaluation_report_path = str(evaluation_report.resolve())
            job.evaluation_dir = str(evaluation_dir.resolve())
            job.finished_at = _utc_now()
    except Exception as exc:  # noqa: BLE001
        with STATE.lock:
            job = STATE.jobs[job_id]
            job.status = "failed"
            job.stage = "failed"
            job.error = str(exc)
            job.traceback = traceback.format_exc(limit=30)
            job.finished_at = _utc_now()


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
    run_report = read_json_if_exists(run_report_path) or {}
    outputs = run_report.setdefault("outputs", {})
    if not isinstance(outputs, dict):
        outputs = {}
        run_report["outputs"] = outputs
    raw_path = resolve_report_path(run_report_path, outputs.get("merged_network_raw"))
    if raw_path is None or not raw_path.exists():
        raise ValueError(
            "Cannot freeze evaluation input: run_report outputs.merged_network_raw "
            f"is missing or unresolved ({run_report_path})"
        )
    destination_dir.mkdir(parents=True, exist_ok=True)
    frozen_network = destination_dir / "merged_network_raw.csv"
    shutil.copy2(raw_path, frozen_network)
    outputs["merged_network_raw"] = frozen_network.name
    frozen_report = destination_dir / "run_report.json"
    frozen_report.write_text(
        json.dumps(run_report, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return frozen_report


def _freeze_truth_manifest(*, truth_manifest_path: Path, destination_dir: Path) -> Path:
    manifest = read_json_if_exists(truth_manifest_path) or {}
    outputs = manifest.setdefault("outputs", {})
    if not isinstance(outputs, dict):
        outputs = {}
        manifest["outputs"] = outputs
    gene_universe_path = resolve_report_path(
        truth_manifest_path, outputs.get("gene_universe")
    )
    if gene_universe_path is None or not gene_universe_path.exists():
        raise ValueError(
            "Cannot freeze evaluation input: ground_truth_manifest outputs.gene_universe "
            f"is missing or unresolved ({truth_manifest_path})"
        )
    frozen_gene_universe = destination_dir / "truth" / "gene_universe.txt"
    frozen_gene_universe.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(gene_universe_path, frozen_gene_universe)
    outputs["gene_universe"] = "truth/gene_universe.txt"
    global_path = resolve_report_path(
        truth_manifest_path, outputs.get("global_network")
    )
    if global_path is not None:
        frozen_global = destination_dir / "truth" / "global_network.csv"
        frozen_global.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(global_path, frozen_global)
        outputs["global_network"] = "truth/global_network.csv"
    group_entries = outputs.get("group_networks", [])
    if isinstance(group_entries, list):
        frozen_entries: list[dict[str, str]] = []
        for entry in group_entries:
            if not isinstance(entry, dict):
                continue
            group = str(entry.get("group") or "").strip()
            source_path = resolve_report_path(truth_manifest_path, entry.get("path"))
            if not group or source_path is None:
                continue
            filename = f"{_slugify(group)}.csv"
            frozen_group = destination_dir / "truth" / "group_networks" / filename
            frozen_group.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, frozen_group)
            frozen_entries.append(
                {"group": group, "path": f"truth/group_networks/{filename}"}
            )
        outputs["group_networks"] = frozen_entries
    destination_dir.mkdir(parents=True, exist_ok=True)
    frozen_manifest = destination_dir / "ground-truth-manifest.json"
    frozen_manifest.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return frozen_manifest


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._-")
    return slug or "unknown"


def _bundle_sources(*, evaluation_dir: Optional[Path]) -> list[tuple[str, Path]]:
    if evaluation_dir is None or not evaluation_dir.exists():
        return []
    return [
        (path.relative_to(evaluation_dir).as_posix(), path)
        for path in sorted(evaluation_dir.rglob("*"))
        if path.is_file()
    ]


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

        try:
            extract_zip_upload(
                inference_upload,
                zip_path=request_dir / "uploads" / "inference.zip",
                extract_dir=request_dir / "inference",
            )
            extract_zip_upload(
                truth_upload,
                zip_path=request_dir / "uploads" / "truth.zip",
                extract_dir=request_dir / "truth",
            )
            run_candidates = _discover_run_candidates(request_dir / "inference")
            truth_candidates = _discover_truth_candidates(request_dir / "truth")
            if not run_candidates:
                raise ValueError("No valid run_report.json was found in inference ZIP")
            if not truth_candidates:
                raise ValueError(
                    "No valid ground-truth-manifest.json was found in truth ZIP"
                )
        except ValueError as exc:
            shutil.rmtree(request_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        job = GuiJob(
            job_id=job_id,
            created_at=_utc_now(),
            status="needs_selection",
            stage="select_inputs",
            request_dir=str(request_dir),
            output_dir=str(output_dir),
            run_candidates=run_candidates,
            truth_candidates=truth_candidates,
        )
        with STATE.lock:
            STATE.jobs[job_id] = job

        selection = _auto_selection(
            run_candidates=run_candidates, truth_candidates=truth_candidates
        )
        if selection is not None:
            run_report, truth_manifest = selection
            _start_evaluation_job(
                job_id=job_id,
                run_report=run_report,
                ground_truth_manifest=truth_manifest,
            )
        return JSONResponse(_job_response(job_id))

    @app.post("/api/evaluate-inference/jobs/{job_id}/run")
    async def api_run_selected(job_id: str, request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="JSON body is required") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="JSON body must be an object")
        run_report = str(payload.get("run_report") or "").strip()
        truth_manifest = str(payload.get("ground_truth_manifest") or "").strip()
        if not run_report or not truth_manifest:
            raise HTTPException(
                status_code=400,
                detail="run_report and ground_truth_manifest are required",
            )
        with STATE.lock:
            if job_id not in STATE.jobs:
                raise HTTPException(status_code=404, detail="Job not found")
        try:
            _start_evaluation_job(
                job_id=job_id,
                run_report=run_report,
                ground_truth_manifest=truth_manifest,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(_job_response(job_id))

    @app.get("/api/evaluate-inference/jobs/{job_id}/bundle")
    async def api_job_bundle(job_id: str) -> FileResponse:
        with STATE.lock:
            job = STATE.jobs.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Job not found")
            evaluation_dir = Path(job.evaluation_dir) if job.evaluation_dir else None
            request_dir = Path(job.request_dir)
        sources = _bundle_sources(evaluation_dir=evaluation_dir)
        if not sources:
            raise HTTPException(status_code=400, detail="Evaluation output is not ready")
        zip_path = request_dir / f"{job_id}_evaluation.zip"
        build_zip_bundle(zip_path=zip_path, sources=sources)
        return FileResponse(
            zip_path,
            media_type="application/zip",
            filename=f"andrea_evaluation_{job_id}.zip",
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
