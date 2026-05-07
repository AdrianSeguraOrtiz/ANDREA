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

from andrea.core.commands.compare_networks import compare_networks
from andrea.core.shared.json_io import write_json
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
    uploaded_file,
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


def _discover_run_candidates(root: Path) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for path in sorted(root.rglob("run_report.json")):
        if not path.is_file():
            continue
        payload = read_json_if_exists(path)
        if not payload:
            continue
        outputs = payload.get("outputs")
        if not isinstance(outputs, dict):
            continue
        normalized_path = resolve_report_path(
            path, outputs.get("merged_network_normalized")
        )
        if normalized_path is None or not normalized_path.exists():
            continue
        dataset = (
            payload.get("dataset") if isinstance(payload.get("dataset"), dict) else {}
        )
        tools = payload.get("tools") if isinstance(payload.get("tools"), dict) else {}
        rel = path.relative_to(root).as_posix()
        candidates.append(
            {
                "path": rel,
                "label": rel,
                "run_id": payload.get("run_id"),
                "status": payload.get("status"),
                "dataset_id": dataset.get("id") if isinstance(dataset, dict) else None,
                "tools_completed": (
                    tools.get("completed") if isinstance(tools, dict) else None
                ),
            }
        )
    return candidates


def _discover_evaluation_candidates(root: Path) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for path in sorted(root.rglob("evaluation_report.json")):
        if not path.is_file():
            continue
        payload = read_json_if_exists(path)
        if not payload or not isinstance(payload.get("metrics"), list):
            continue
        inputs = (
            payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
        )
        rel = path.relative_to(root).as_posix()
        candidates.append(
            {
                "path": rel,
                "label": rel,
                "inference_run_id": inputs.get("inference_run_id"),
                "inference_dataset_id": inputs.get("inference_dataset_id"),
                "metrics": len(payload.get("metrics", [])),
            }
        )
    return candidates


def _source_root(*, request_dir: Path, source_id: str, kind: str) -> Path:
    return request_dir / "sources" / source_id / kind


def _candidate_path(
    *,
    request_dir: Path,
    source: dict[str, Any],
    kind: str,
    selected_path: str,
) -> Path:
    candidate_key = "run_candidates" if kind == "inference" else "evaluation_candidates"
    allowed = {str(candidate["path"]) for candidate in source.get(candidate_key, [])}
    if selected_path not in allowed:
        raise ValueError(
            f"Selected {kind} path for {source['source_id']} is not one of the detected candidates"
        )
    root = _source_root(
        request_dir=request_dir, source_id=str(source["source_id"]), kind=kind
    )
    path = (root / selected_path).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(
            f"Selected {kind} path for {source['source_id']} escapes the extracted archive"
        )
    if not path.exists() or not path.is_file():
        raise ValueError(
            f"Selected {kind} file no longer exists for {source['source_id']}: {selected_path}"
        )
    return path


def _needs_selection(source: dict[str, Any]) -> bool:
    if len(source.get("run_candidates", [])) != 1:
        return True
    if (
        source.get("evaluation_uploaded")
        and len(source.get("evaluation_candidates", [])) != 1
    ):
        return True
    return False


def _auto_selected_sources(
    sources: list[dict[str, Any]]
) -> Optional[list[dict[str, Any]]]:
    if any(_needs_selection(source) for source in sources):
        return None
    selected: list[dict[str, Any]] = []
    for source in sources:
        selection = {
            "source_id": source["source_id"],
            "label": source["label"],
            "run_report": source["run_candidates"][0]["path"],
            "evaluation_report": None,
        }
        if source.get("evaluation_uploaded"):
            selection["evaluation_report"] = source["evaluation_candidates"][0]["path"]
        selected.append(selection)
    return selected


def _normalize_selected_sources(
    *,
    sources: list[dict[str, Any]],
    raw_selected: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_source = {source["source_id"]: source for source in sources}
    selected_by_source = {
        str(item.get("source_id", "")): item
        for item in raw_selected
        if isinstance(item, dict)
    }
    selected: list[dict[str, Any]] = []
    for source in sources:
        source_id = source["source_id"]
        raw = selected_by_source.get(source_id, {})
        run_report = str(raw.get("run_report") or "").strip()
        if not run_report and len(source.get("run_candidates", [])) == 1:
            run_report = str(source["run_candidates"][0]["path"])
        if not run_report:
            raise ValueError(f"run_report is required for {source_id}")

        evaluation_report_raw = raw.get("evaluation_report")
        evaluation_report = (
            str(evaluation_report_raw).strip()
            if evaluation_report_raw is not None
            else ""
        )
        if (
            not evaluation_report
            and source.get("evaluation_uploaded")
            and len(source.get("evaluation_candidates", [])) == 1
        ):
            evaluation_report = str(source["evaluation_candidates"][0]["path"])

        selected.append(
            {
                "source_id": source_id,
                "label": by_source[source_id]["label"],
                "run_report": run_report,
                "evaluation_report": evaluation_report or None,
            }
        )
    return selected


def _start_comparison_job(
    *,
    job_id: str,
    selected_sources: list[dict[str, Any]],
) -> None:
    with STATE.lock:
        job = STATE.jobs[job_id]
        job.status = "queued"
        job.stage = "queued"
        job.selected_sources = selected_sources
        job.frozen_comparison_request_path = None
        job.error = None
        job.traceback = None
        job.started_at = None
        job.finished_at = None
    threading.Thread(
        target=_run_comparison_job,
        kwargs={"job_id": job_id, "selected_sources": selected_sources},
        daemon=True,
    ).start()


def _run_comparison_job(
    *,
    job_id: str,
    selected_sources: list[dict[str, Any]],
) -> None:
    with STATE.lock:
        job = STATE.jobs[job_id]
        job.status = "running"
        job.stage = "comparing"
        job.started_at = _utc_now()
        request_dir = Path(job.request_dir)
        output_dir = Path(job.output_dir)
        source_defs = list(job.sources)
    try:
        request_id = f"gui_compare_{job_id}"
        comparison_dir = _create_gui_comparison_dir(
            output_dir=output_dir,
            request_id=request_id,
        )
        frozen_request_path = _freeze_comparison_inputs(
            request_dir=request_dir,
            source_defs=source_defs,
            selected_sources=selected_sources,
            comparison_dir=comparison_dir,
            request_id=request_id,
        )
        report = compare_networks(
            request_path=frozen_request_path,
            output_dir=output_dir,
            comparison_dir=comparison_dir,
        )
        comparison_report_path = output_dir / str(
            report["outputs"]["comparison_report"]
        )
        comparison_dir = output_dir / str(report["outputs"]["comparison_dir"])
        with STATE.lock:
            job = STATE.jobs[job_id]
            job.status = "completed"
            job.stage = "completed"
            job.comparison_request_path = str(frozen_request_path.resolve())
            job.frozen_comparison_request_path = str(frozen_request_path.resolve())
            job.comparison_report_path = str(comparison_report_path.resolve())
            job.comparison_dir = str(comparison_dir.resolve())
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
    source_defs: list[dict[str, Any]],
    selected_sources: list[dict[str, Any]],
    comparison_dir: Path,
    request_id: str,
) -> Path:
    source_by_id = {source["source_id"]: source for source in source_defs}
    input_dir = comparison_dir / "input"
    frozen_sources: list[dict[str, Any]] = []
    for selection in selected_sources:
        source_id = str(selection.get("source_id") or "")
        source = source_by_id.get(source_id)
        if source is None:
            raise ValueError(f"Unknown source_id: {source_id}")
        source_dir = input_dir / "sources" / source_id
        source_dir.mkdir(parents=True, exist_ok=True)
        run_report_path = _candidate_path(
            request_dir=request_dir,
            source=source,
            kind="inference",
            selected_path=str(selection.get("run_report") or ""),
        )
        frozen_run_report_path = _freeze_run_report(
            run_report_path=run_report_path,
            destination_dir=source_dir,
        )
        frozen_source: dict[str, Any] = {
            "source_id": source_id,
            "label": str(selection.get("label") or source.get("label") or source_id),
            "run_report": frozen_run_report_path.relative_to(comparison_dir).as_posix(),
        }
        evaluation_report = selection.get("evaluation_report")
        if isinstance(evaluation_report, str) and evaluation_report.strip():
            evaluation_path = _candidate_path(
                request_dir=request_dir,
                source=source,
                kind="evaluation",
                selected_path=evaluation_report,
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


def _comparison_bundle_sources(
    *, comparison_dir: Optional[Path]
) -> list[tuple[str, Path]]:
    if comparison_dir is None or not comparison_dir.exists():
        return []
    return [
        (path.relative_to(comparison_dir).as_posix(), path)
        for path in sorted(comparison_dir.rglob("*"))
        if path.is_file()
    ]


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

        try:
            for idx in range(source_count):
                source_id = f"source_{idx + 1}"
                label = str(form.get(f"source_{idx}_label") or source_id).strip()
                inference_upload = uploaded_file(form, f"source_{idx}_inference_zip")
                evaluation_upload = uploaded_file(form, f"source_{idx}_evaluation_zip")
                if inference_upload is None:
                    raise ValueError(f"source_{idx}_inference_zip is required")

                source_dir = request_dir / "sources" / source_id
                extract_zip_upload(
                    inference_upload,
                    zip_path=source_dir / "uploads" / "inference.zip",
                    extract_dir=source_dir / "inference",
                )
                run_candidates = _discover_run_candidates(source_dir / "inference")
                if not run_candidates:
                    raise ValueError(
                        f"No valid run_report.json with merged_network_normalized was found in source {idx + 1}"
                    )

                evaluation_candidates: list[dict[str, Any]] = []
                if evaluation_upload is not None:
                    extract_zip_upload(
                        evaluation_upload,
                        zip_path=source_dir / "uploads" / "evaluation.zip",
                        extract_dir=source_dir / "evaluation",
                    )
                    evaluation_candidates = _discover_evaluation_candidates(
                        source_dir / "evaluation"
                    )
                    if not evaluation_candidates:
                        raise ValueError(
                            f"No valid evaluation_report.json was found in source {idx + 1}"
                        )

                sources.append(
                    {
                        "source_id": source_id,
                        "label": label or source_id,
                        "evaluation_uploaded": evaluation_upload is not None,
                        "run_candidates": run_candidates,
                        "evaluation_candidates": evaluation_candidates,
                    }
                )
        except ValueError as exc:
            shutil.rmtree(request_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        job = GuiJob(
            job_id=job_id,
            created_at=_utc_now(),
            status="needs_selection",
            stage="select_sources",
            request_dir=str(request_dir),
            output_dir=str(output_dir),
            sources=sources,
        )
        with STATE.lock:
            STATE.jobs[job_id] = job

        selected_sources = _auto_selected_sources(sources)
        if selected_sources is not None:
            _start_comparison_job(job_id=job_id, selected_sources=selected_sources)
        return JSONResponse(_job_response(job_id))

    @app.post("/api/compare-networks/jobs/{job_id}/run")
    async def api_run_selected(job_id: str, request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=400, detail="JSON body is required"
            ) from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="JSON body must be an object")
        raw_sources = payload.get("sources")
        if not isinstance(raw_sources, list):
            raise HTTPException(status_code=400, detail="sources array is required")
        with STATE.lock:
            job = STATE.jobs.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Job not found")
            source_defs = list(job.sources)
        try:
            selected_sources = _normalize_selected_sources(
                sources=source_defs,
                raw_selected=raw_sources,
            )
            _start_comparison_job(job_id=job_id, selected_sources=selected_sources)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(_job_response(job_id))

    @app.get("/api/compare-networks/jobs/{job_id}/bundle")
    async def api_job_bundle(job_id: str) -> FileResponse:
        with STATE.lock:
            job = STATE.jobs.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Job not found")
            comparison_dir = Path(job.comparison_dir) if job.comparison_dir else None
            request_dir = Path(job.request_dir)
        sources = _comparison_bundle_sources(comparison_dir=comparison_dir)
        if not sources:
            raise HTTPException(
                status_code=400, detail="Comparison output is not ready"
            )
        zip_path = request_dir / f"{job_id}_comparison.zip"
        build_zip_bundle(zip_path=zip_path, sources=sources)
        return FileResponse(
            zip_path,
            media_type="application/zip",
            filename=f"andrea_comparison_{job_id}.zip",
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
