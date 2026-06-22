"""FastAPI server for the local generate-data GUI."""

from __future__ import annotations

import json
import os
import shutil
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

from andrea.core.commands.generate_data import (
    bundles as generate_data_bundles,
    plan_generate_data_request,
    preflight_generate_data_scenario,
    run_generate_data,
)
from andrea.core.commands.generate_data.bootstrap import load_generate_bootstrap
from andrea.core.commands.generate_data.cost_planner import detect_host_ram_gb
from andrea.gui.common.reproducibility import (
    python_path_expr,
    shell_join_pretty,
    unavailable_reproducibility,
)
from andrea.gui.common.server_files import (
    MAX_TABLE_PREVIEW_ROWS,
    MAX_TEXT_PREVIEW_BYTES,
    build_bundle_entries,
    build_bundle_metadata,
    build_zip_bundle,
    bundle_resolution_payload,
    default_viewer_for_path,
    is_probably_text,
    preview_table,
    preview_text,
    read_json_if_exists,
    resolve_virtual_source,
    save_upload,
)
from andrea.gui.common.server_jobs import start_background_thread

STATIC_DIR = Path(__file__).resolve().parent / "static"
COMMON_STATIC_DIR = Path(__file__).resolve().parents[1] / "common" / "static"
GUI_TMP_ROOT = Path("/tmp/andrea_gui/generate_data")


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
    scenario_request_path: Optional[str] = None
    simulator_runs_path: Optional[str] = None
    preflight_report_path: Optional[str] = None
    plan_path: Optional[str] = None
    benchmark_root: Optional[str] = None
    error: Optional[str] = None
    traceback: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    runtime_progress: dict[str, dict[str, Any]] = field(default_factory=dict)


class GuiState:
    def __init__(self) -> None:
        self.jobs: dict[str, GuiJob] = {}
        self.lock = threading.RLock()


STATE = GuiState()


def _load_generate_bootstrap() -> dict[str, Any]:
    return load_generate_bootstrap()


def _frozen_job_artifact_paths(job: GuiJob) -> dict[str, Optional[str]]:
    benchmark_root = Path(job.benchmark_root) if job.benchmark_root else None
    if benchmark_root is None or not benchmark_root.exists():
        return {
            "scenario_request_path": job.scenario_request_path,
            "simulator_runs_path": job.simulator_runs_path,
            "preflight_report_path": job.preflight_report_path,
            "plan_path": job.plan_path,
        }

    candidates = {
        "scenario_request_path": benchmark_root / "input" / "scenario-request.json",
        "simulator_runs_path": benchmark_root / "input" / "simulator-runs.json",
        "preflight_report_path": benchmark_root / "preflight-report.json",
        "plan_path": benchmark_root / "simulation-plan.json",
    }
    return {
        key: str(path.resolve()) if path.exists() else getattr(job, key)
        for key, path in candidates.items()
    }


def _job_payload(job: GuiJob) -> dict[str, Any]:
    preferred_paths = _frozen_job_artifact_paths(job)
    return {
        "job_id": job.job_id,
        "created_at": job.created_at,
        "status": job.status,
        "stage": job.stage,
        "request_dir": job.request_dir,
        "output_dir": job.output_dir,
        "scenario_request_path": preferred_paths["scenario_request_path"],
        "simulator_runs_path": preferred_paths["simulator_runs_path"],
        "preflight_report_path": preferred_paths["preflight_report_path"],
        "plan_path": preferred_paths["plan_path"],
        "benchmark_root": job.benchmark_root,
        "run_dir": job.benchmark_root,
        "error": job.error,
        "traceback": job.traceback,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


def _scenario_payload_from_config(
    *,
    config: dict[str, Any],
    form: Any,
    request_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    scenario = config.get("scenario")
    if not isinstance(scenario, dict):
        raise ValueError("config.scenario must be an object")
    options = config.get("options") or {}
    if not isinstance(options, dict):
        raise ValueError("config.options must be an object when provided")
    data_axes = scenario.get("data_axes")
    truth_requirements = scenario.get("truth_requirements")
    if not isinstance(data_axes, dict) or not isinstance(truth_requirements, dict):
        raise ValueError("config.scenario must include data_axes and truth_requirements")

    payload = {
        "schema_version": "1.0",
        "id": str(scenario.get("id", "")).strip(),
        "data_axes": data_axes,
        "truth_requirements": truth_requirements,
        "organism": scenario.get(
            "organism", {"taxonomic_group": "synthetic", "ncbi_taxon_id": None}
        ),
        "requested_extras": list(scenario.get("requested_extras", [])),
    }
    if scenario.get("base_seed") not in (None, ""):
        payload["base_seed"] = int(scenario["base_seed"])
    if str(scenario.get("notes", "")).strip():
        payload["notes"] = str(scenario["notes"]).strip()

    inputs = scenario.get("inputs") or {}
    if not isinstance(inputs, dict):
        raise ValueError("config.scenario.inputs must be an object when provided")
    resolved_inputs: dict[str, dict[str, Any]] = {
        str(input_id): dict(meta)
        for input_id, meta in inputs.items()
        if isinstance(meta, dict)
    }

    for key in form.keys():
        if not str(key).startswith("input__"):
            continue
        input_id = str(key).removeprefix("input__").strip()
        upload = form.get(key)
        if not input_id or upload is None or not getattr(upload, "filename", ""):
            continue
        suffix = Path(str(upload.filename)).suffix
        filename = f"{input_id}{suffix}" if suffix else input_id
        destination = request_dir / "inputs" / filename
        save_upload(upload, destination)
        meta = resolved_inputs.get(input_id, {})
        meta["path"] = str(Path("inputs") / filename)
        resolved_inputs[input_id] = meta

    if resolved_inputs:
        payload["inputs"] = resolved_inputs
    return payload, options


def _write_scenario_request_file(
    *,
    request_dir: Path,
    config: dict[str, Any],
    form: Any,
) -> tuple[Path, dict[str, Any]]:
    payload, options = _scenario_payload_from_config(
        config=config,
        form=form,
        request_dir=request_dir,
    )
    scenario_path = request_dir / "scenario-request.json"
    scenario_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return scenario_path, options


def _normalize_simulator_runs(raw_runs: Any) -> dict[str, Any]:
    if not isinstance(raw_runs, list) or not raw_runs:
        raise ValueError("runs must be a non-empty array")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, raw in enumerate(raw_runs, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"runs[{idx}] must be an object")
        simulator_id = str(raw.get("simulator_id", "")).strip()
        if not simulator_id:
            raise ValueError(f"runs[{idx}].simulator_id is required")
        run_id = str(raw.get("run_id") or f"{simulator_id}__{idx:02d}").strip()
        if not run_id:
            raise ValueError(f"runs[{idx}].run_id is required")
        if run_id in seen:
            raise ValueError(f"Duplicate run_id: {run_id}")
        seen.add(run_id)
        params = raw.get("params", {})
        if not isinstance(params, dict):
            raise ValueError(f"runs[{idx}].params must be an object")
        item: dict[str, Any] = {
            "run_id": run_id,
            "simulator_id": simulator_id,
            "replicates": int(raw.get("replicates", 1)),
            "params": params,
        }
        native_outputs = raw.get("native_outputs")
        if native_outputs is not None:
            if not isinstance(native_outputs, list):
                raise ValueError(f"runs[{idx}].native_outputs must be an array")
            item["native_outputs"] = list(
                dict.fromkeys(
                    str(value).strip() for value in native_outputs if str(value).strip()
                )
            )
        if raw.get("base_seed") not in (None, ""):
            item["base_seed"] = int(raw["base_seed"])
        if str(raw.get("notes", "")).strip():
            item["notes"] = str(raw["notes"]).strip()
        normalized.append(item)
    return {"schema_version": "1.0", "runs": normalized}


def _write_simulator_runs_file(*, request_dir: Path, runs_raw: Any) -> Path:
    payload = _normalize_simulator_runs(runs_raw)
    path = request_dir / "simulator-runs.json"
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return path


def _runtime_progress_payload(job: GuiJob) -> dict[str, Any]:
    items = list(job.runtime_progress.values())
    if not items and job.plan_path:
        plan = read_json_if_exists(job.plan_path)
        if isinstance(plan, dict):
            for task in plan.get("tasks", []):
                if isinstance(task, dict):
                    items.append(
                        {
                            "task_id": task.get("task_id"),
                            "run_id": task.get("run_id"),
                            "simulator_id": task.get("simulator_id"),
                            "replicate_index": task.get("replicate_index"),
                            "seed": task.get("seed"),
                            "dataset_id": task.get("dataset_id"),
                            "percent": 0,
                            "status": "pending",
                            "phase": "pending",
                            "message": "Pending execution",
                        }
                    )
    order = {str(item.get("task_id", "")): idx for idx, item in enumerate(items)}
    plan = read_json_if_exists(job.plan_path)
    if isinstance(plan, dict):
        order = {
            str(task.get("task_id", "")): idx
            for idx, task in enumerate(plan.get("tasks", []))
            if isinstance(task, dict)
        }
    items.sort(key=lambda item: order.get(str(item.get("task_id", "")), 10_000))
    summary = {
        "total": len(items),
        "completed": sum(1 for item in items if item.get("status") == "completed"),
        "failed": sum(1 for item in items if item.get("status") == "failed"),
        "running": sum(1 for item in items if item.get("status") == "running"),
        "pending": sum(1 for item in items if item.get("status") == "pending"),
    }
    return {"tasks": items, "summary": summary}


def _progress_callback_for_job(job_id: str):
    def _callback(event: dict[str, Any]) -> None:
        task_id = str(event.get("task_id", "")).strip()
        if not task_id:
            return
        with STATE.lock:
            job = STATE.jobs.get(job_id)
            if job is None:
                return
            previous = job.runtime_progress.get(task_id, {})
            previous_percent = int(previous.get("percent", 0) or 0)
            next_event = dict(event)
            next_event["percent"] = max(
                previous_percent, int(next_event.get("percent", 0) or 0)
            )
            job.runtime_progress[task_id] = next_event

    return _callback


def _run_job(*, job_id: str, action: str, options: dict[str, Any]) -> None:
    with STATE.lock:
        job = STATE.jobs[job_id]
        job.status = "running"
        job.started_at = _utc_now()
        job.finished_at = None

    try:
        with STATE.lock:
            job = STATE.jobs[job_id]
            scenario_path = (
                Path(job.scenario_request_path) if job.scenario_request_path else None
            )
            simulator_runs_path = (
                Path(job.simulator_runs_path) if job.simulator_runs_path else None
            )
            plan_path = Path(job.plan_path) if job.plan_path else None
            output_dir = Path(job.output_dir)

        if action == "preflight":
            if scenario_path is None:
                raise ValueError("Job is missing scenario_request_path")
            report = preflight_generate_data_scenario(scenario_path)
            preflight_path = Path(job.request_dir) / "preflight-report.json"
            preflight_path.write_text(
                json.dumps(report, indent=2, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )
            with STATE.lock:
                job = STATE.jobs[job_id]
                job.status = "completed"
                job.stage = "preflight_ok"
                job.preflight_report_path = str(preflight_path)
                job.finished_at = _utc_now()
            return

        if action == "plan":
            if scenario_path is None or simulator_runs_path is None:
                raise ValueError("Job is missing scenario or simulator-runs path")
            output_path = Path(job.request_dir) / "simulation-plan.json"
            plan_generate_data_request(
                scenario_request_path=scenario_path,
                simulator_runs_path=simulator_runs_path,
                output_path=output_path,
                max_parallel_tasks=int(options.get("max_parallel_tasks", 1)),
                max_cores=int(options.get("max_cores", os.cpu_count() or 1)),
                max_ram_gb=float(options.get("max_ram_gb", detect_host_ram_gb())),
            )
            with STATE.lock:
                job = STATE.jobs[job_id]
                job.status = "completed"
                job.stage = "planned"
                job.plan_path = str(output_path)
                job.runtime_progress = {}
                job.finished_at = _utc_now()
            return

        if action == "run":
            if plan_path is None:
                raise ValueError("Job has no simulation plan to execute")
            benchmark_root = run_generate_data(
                plan_path=plan_path,
                output_dir=output_dir,
                max_parallel_tasks=(
                    int(options["max_parallel_tasks"])
                    if options.get("max_parallel_tasks") not in (None, "")
                    else None
                ),
                progress_poll_seconds=float(options.get("progress_poll_seconds", 0.5)),
                show_progress=False,
                progress_callback=_progress_callback_for_job(job_id),
            )
            with STATE.lock:
                job = STATE.jobs[job_id]
                job.status = "completed"
                job.stage = "executed"
                job.benchmark_root = str(benchmark_root.resolve())
                frozen_paths = _frozen_job_artifact_paths(job)
                job.scenario_request_path = frozen_paths["scenario_request_path"]
                job.simulator_runs_path = frozen_paths["simulator_runs_path"]
                job.preflight_report_path = frozen_paths["preflight_report_path"]
                job.plan_path = frozen_paths["plan_path"]
                job.finished_at = _utc_now()
            return

        raise ValueError(f"Unsupported job action: {action}")
    except Exception as exc:  # noqa: BLE001
        with STATE.lock:
            job = STATE.jobs[job_id]
            job.status = "failed"
            job.error = str(exc)
            job.traceback = traceback.format_exc(limit=30)
            job.finished_at = _utc_now()


def _build_reproducibility_payload(job: GuiJob) -> dict[str, Any]:
    benchmark_root = Path(job.benchmark_root) if job.benchmark_root else None
    if benchmark_root is None or not benchmark_root.exists():
        return unavailable_reproducibility(
            "Reproducibility snippets will be available after execution."
        )
    scenario_file = benchmark_root / "input" / "scenario-request.json"
    simulator_runs_file = benchmark_root / "input" / "simulator-runs.json"
    plan_file = benchmark_root / "simulation-plan.json"
    if not (
        scenario_file.exists() and simulator_runs_file.exists() and plan_file.exists()
    ):
        return unavailable_reproducibility(
            "Frozen benchmark inputs are not available yet in the output directory."
        )
    scenario_path = str(scenario_file.resolve())
    simulator_runs_path = str(simulator_runs_file.resolve())
    plan_path = str(plan_file.resolve())
    output_dir = str(benchmark_root.parent.resolve())
    progress_poll_seconds = 0.5
    plan = read_json_if_exists(plan_path) or {}
    max_parallel_tasks = int(
        (plan.get("execution") or {}).get("max_parallel_tasks", 1)
        if isinstance(plan.get("execution"), dict)
        else 1
    )
    max_cores = int(
        (plan.get("execution") or {}).get("max_cores", max_parallel_tasks)
        if isinstance(plan.get("execution"), dict)
        else max_parallel_tasks
    )
    max_ram_gb = float(
        (plan.get("execution") or {}).get("max_ram_gb", detect_host_ram_gb())
        if isinstance(plan.get("execution"), dict)
        else detect_host_ram_gb()
    )
    preflight_output_json = str((benchmark_root / "preflight-report.json").resolve())

    cli_unified = [
        "andrea",
        "generate-data",
        "execute",
        "--scenario",
        scenario_path,
        "--simulator-runs",
        simulator_runs_path,
        "--output-dir",
        output_dir,
        "--max-parallel-tasks",
        str(max_parallel_tasks),
        "--max-cores",
        str(max_cores),
        "--max-ram-gb",
        str(max_ram_gb),
        "--progress-poll-seconds",
        str(progress_poll_seconds),
    ]
    cli_preflight = [
        "andrea",
        "generate-data",
        "preflight",
        "--scenario",
        scenario_path,
        "--output-json",
        preflight_output_json,
    ]
    cli_plan = [
        "andrea",
        "generate-data",
        "plan",
        "--scenario",
        scenario_path,
        "--simulator-runs",
        simulator_runs_path,
        "--max-parallel-tasks",
        str(max_parallel_tasks),
        "--max-cores",
        str(max_cores),
        "--max-ram-gb",
        str(max_ram_gb),
        "--out",
        plan_path,
    ]
    cli_run = [
        "andrea",
        "generate-data",
        "run",
        "--plan",
        plan_path,
        "--output-dir",
        output_dir,
        "--progress-poll-seconds",
        str(progress_poll_seconds),
    ]

    python_unified = "\n".join(
        [
            "from pathlib import Path",
            "",
            "from andrea.core.commands.generate_data import execute_generate_data",
            "",
            "benchmark_root = execute_generate_data(",
            f"    scenario_request_path={python_path_expr(scenario_path)},",
            f"    simulator_runs_path={python_path_expr(simulator_runs_path)},",
            f"    output_dir={python_path_expr(output_dir)},",
            f"    max_parallel_tasks={max_parallel_tasks},",
            f"    max_cores={max_cores},",
            f"    max_ram_gb={max_ram_gb},",
            f"    progress_poll_seconds={progress_poll_seconds},",
            ")",
            "",
            "print(benchmark_root)",
        ]
    )
    python_steps = "\n".join(
        [
            "from pathlib import Path",
            "",
            "from andrea.core.commands.generate_data import (",
            "    plan_generate_data_request,",
            "    preflight_generate_data_scenario,",
            "    run_generate_data,",
            ")",
            "",
            f"scenario_path = {python_path_expr(scenario_path)}",
            f"simulator_runs_path = {python_path_expr(simulator_runs_path)}",
            f"plan_path = {python_path_expr(plan_path)}",
            f"output_dir = {python_path_expr(output_dir)}",
            "",
            "preflight_report = preflight_generate_data_scenario(scenario_path)",
            "plan_generate_data_request(",
            "    scenario_request_path=scenario_path,",
            "    simulator_runs_path=simulator_runs_path,",
            "    output_path=plan_path,",
            f"    max_parallel_tasks={max_parallel_tasks},",
            f"    max_cores={max_cores},",
            f"    max_ram_gb={max_ram_gb},",
            ")",
            "benchmark_root = run_generate_data(",
            "    plan_path=plan_path,",
            "    output_dir=output_dir,",
            f"    progress_poll_seconds={progress_poll_seconds},",
            ")",
            "",
            "print(preflight_report)",
            "print(benchmark_root)",
        ]
    )
    return {
        "available": True,
        "cli": {
            "title": "CLI",
            "summary": "Replay this GUI job using the frozen scenario and simulator-runs files stored in the benchmark output directory.",
            "primary_label": "Unified command",
            "primary_language": "bash",
            "primary_code": shell_join_pretty(cli_unified),
            "steps_label": "If you prefer by steps",
            "steps": [
                {
                    "title": "1. Preflight",
                    "language": "bash",
                    "code": shell_join_pretty(cli_preflight),
                },
                {
                    "title": "2. Plan",
                    "language": "bash",
                    "code": shell_join_pretty(cli_plan),
                },
                {
                    "title": "3. Run",
                    "language": "bash",
                    "code": shell_join_pretty(cli_run),
                },
            ],
        },
        "python": {
            "title": "Python",
            "summary": "Replay this GUI job using the current generate-data Python API and the frozen benchmark inputs.",
            "primary_label": "Unified code",
            "primary_language": "python",
            "primary_code": python_unified,
            "steps_label": "If you prefer by steps",
            "steps": [
                {
                    "title": "1-3. Preflight, plan, and run",
                    "language": "python",
                    "code": python_steps,
                }
            ],
        },
    }


def _resolve_bundle(
    *,
    benchmark_root: Optional[Path],
    bundle_id: str,
    dataset_id: str | None = None,
) -> Any:
    if benchmark_root is None or not benchmark_root.exists():
        raise ValueError("Benchmark output is not ready")
    return generate_data_bundles.resolve_bundle(
        bundle_id=bundle_id,
        benchmark_root=benchmark_root,
        dataset_id=dataset_id,
    )


def _unavailable_bundle_payload(spec: Any, *, message: str) -> dict[str, Any]:
    return {
        "id": str(spec.id),
        "label": str(spec.label),
        "purpose": str(spec.purpose),
        "intended_downstream_commands": list(spec.intended_downstream_commands),
        "cli_note": str(spec.cli_note),
        "contents_summary": list(spec.contents_summary),
        "available": False,
        "output_ready": True,
        "missing_required": [message],
        "skipped_optional": [],
        "file_count": 0,
        "total_size_bytes": 0,
        "files": [],
    }


def _bundle_metadata_for_generate_data(
    *, benchmark_root: Optional[Path]
) -> list[dict[str, Any]]:
    if benchmark_root is None or not benchmark_root.exists():
        return build_bundle_metadata(
            specs=generate_data_bundles.bundle_specs(),
            resolver=None,
            unavailable_reason="Benchmark output is not ready",
        )

    bundles: list[dict[str, Any]] = []
    for spec in generate_data_bundles.bundle_specs():
        if spec.id != "analysis":
            try:
                resolution = generate_data_bundles.resolve_bundle(
                    bundle_id=spec.id,
                    benchmark_root=benchmark_root,
                )
                bundles.append(bundle_resolution_payload(resolution))
            except Exception as exc:  # noqa: BLE001
                bundles.append(_unavailable_bundle_payload(spec, message=str(exc)))
            continue

        dataset_ids = generate_data_bundles.analysis_dataset_ids(
            benchmark_root=benchmark_root
        )
        if not dataset_ids:
            payload = _unavailable_bundle_payload(spec, message="datasets/*")
            payload["display_id"] = "analysis"
            bundles.append(payload)
            continue

        for dataset_id in dataset_ids:
            resolution = generate_data_bundles.resolve_bundle(
                bundle_id="analysis",
                benchmark_root=benchmark_root,
                dataset_id=dataset_id,
            )
            payload = bundle_resolution_payload(resolution)
            payload.update(
                {
                    "dataset_id": dataset_id,
                    "variant_id": dataset_id,
                    "display_id": f"analysis · {dataset_id}",
                    "label": f"Analysis Bundle - {dataset_id}",
                    "purpose": (
                        "Minimal ground-truth handoff for this dataset. Upload this ZIP "
                        "directly as the generate-data input in evaluate-inference."
                    ),
                    "contents_summary": [
                        "ground-truth-manifest.json",
                        "truth/networks.csv",
                        "truth/gene_universe.txt",
                    ],
                }
            )
            bundles.append(payload)
    return bundles


def _require_bundle_available(resolution: Any) -> None:
    if resolution.available and resolution.sources:
        return
    missing = ", ".join(resolution.missing_required) or "no files"
    raise ValueError(
        f"Bundle '{resolution.spec.id}' is not available; missing required files: {missing}"
    )


def _viewer_for_virtual_path(path: str) -> str:
    if Path(path.lower()).name == "simulation-plan.json":
        return "plan"
    return default_viewer_for_path(path)


def _artifact_guide(path: str) -> Optional[dict[str, Any]]:
    normalized = path.lower()
    basename = Path(normalized).name
    extras_guides: dict[str, dict[str, Any]] = {
        "groups.tsv": {
            "title": "Groups",
            "summary": (
                "Expression-column-to-group assignment table. The first column contains "
                "expression column identifiers and the cluster column stores the exported group label."
            ),
            "badges": ["standardized extra", "group context"],
            "tips": [
                "Referenced by dataset-manifest.json when group-aware tools are run.",
                "Required by scenario templates or methods that derive one network per group.",
            ],
        },
        "column_phenotypes.tsv": {
            "title": "Column phenotypes",
            "summary": (
                "Ordered expression-column-to-phenotype assignment table for methods "
                "that model transitions between observed states."
            ),
            "badges": ["standardized extra", "ordered states"],
            "tips": [
                "Each expression column appears once with a phenotype label and integer order.",
                "This is stricter than groups.tsv because the order column carries state progression.",
            ],
        },
        "column_descriptors.tsv": {
            "title": "Column descriptors",
            "summary": (
                "Expression-column metadata table with categorical or scalar descriptors "
                "such as batch, condition, donor, cell type or assay state."
            ),
            "badges": ["standardized extra", "column metadata"],
            "tips": [
                "Each expression column should appear once.",
                "Used by tools that consume auxiliary sample, cell or condition descriptors.",
            ],
        },
        "cluster_identities.tsv": {
            "title": "Cluster identities",
            "summary": (
                "Cluster annotation table mapping group identifiers to readable labels "
                "and, when available, an ordering column."
            ),
            "badges": ["standardized extra", "group annotation"],
            "tips": [
                "Cluster IDs should match labels from groups.tsv.",
                "Used by tools that need named or ordered cluster annotations.",
            ],
        },
        "enrichment_background.txt": {
            "title": "Enrichment background",
            "summary": (
                "Gene universe for enrichment-style analyses, stored as one gene "
                "identifier per line."
            ),
            "badges": ["standardized extra", "gene list"],
            "tips": [
                "Tools that perform enrichment can use this instead of their internal background.",
                "When absent, compatible tools may fall back to the expression gene universe.",
            ],
        },
        "lineage_tree.tsv": {
            "title": "Lineage tree",
            "summary": (
                "Group-level lineage table with child, parent, gain_rate and loss_rate "
                "columns."
            ),
            "badges": ["standardized extra", "trajectory"],
            "tips": [
                "Root groups use parent __root__ with zero gain and loss rates.",
                "Used by methods that model regulatory changes across a group lineage.",
            ],
        },
        "interventions.tsv": {
            "title": "Interventions",
            "summary": (
                "Intervention-definition table describing perturbed targets, effects, "
                "optional signs and doses."
            ),
            "badges": ["standardized extra", "perturbation"],
            "tips": [
                "Target gene IDs should match expression.tsv and truth/gene_universe.txt.",
                "Use with perturbation_design.tsv when per-column condition assignments are available.",
            ],
        },
        "perturbation_design.tsv": {
            "title": "Perturbation design",
            "summary": (
                "Expression-column perturbation metadata table with condition labels and "
                "optional target, dose, timepoint, replicate and control columns."
            ),
            "badges": ["standardized extra", "perturbation"],
            "tips": [
                "Each expression column should appear once.",
                "Condition labels should be stable enough for downstream filtering and grouping.",
            ],
        },
        "prior_grn.tsv": {
            "title": "Global prior GRN",
            "summary": (
                "Prior regulatory network with source regulator, target gene and score "
                "columns."
            ),
            "badges": ["standardized extra", "prior network"],
            "tips": [
                "Scores can encode confidence, sign or magnitude depending on the source.",
                "Used by prior-informed inference tools; it is not the benchmark truth network.",
            ],
        },
        "prior_grn_by_group.tsv": {
            "title": "Group-specific prior GRN",
            "summary": (
                "Group-resolved prior regulatory edges with group, source, target and "
                "score columns."
            ),
            "badges": ["standardized extra", "group prior"],
            "tips": [
                "Group labels should match groups.tsv.",
                "Used by tools that accept a different prior regulatory network per group.",
            ],
        },
        "pseudotime.tsv": {
            "title": "Pseudotime",
            "summary": (
                "Column-level pseudotime table mapping each expression column to a numeric "
                "trajectory value."
            ),
            "badges": ["standardized extra", "trajectory"],
            "tips": [
                "Each expression column should appear once.",
                "Used by trajectory-aware tools or filters that depend on column ordering.",
            ],
        },
        "replicates.tsv": {
            "title": "Replicates",
            "summary": (
                "Expression-column replicate metadata table for biological or technical "
                "replicate labels."
            ),
            "badges": ["standardized extra", "replicates"],
            "tips": [
                "Each expression column should appear once.",
                "Use this separately from groups.tsv when replicate structure is not the grouping axis.",
            ],
        },
        "timepoints.tsv": {
            "title": "Timepoints",
            "summary": (
                "Expression-column timepoint table with observed sampling times or ordered "
                "time coordinates."
            ),
            "badges": ["standardized extra", "time series"],
            "tips": [
                "Each expression column should appear once.",
                "This represents observed time, while pseudotime.tsv represents an inferred or simulated trajectory coordinate.",
            ],
        },
        "tf_list.txt": {
            "title": "TF list",
            "summary": "Candidate regulator list stored as one TF identifier per line.",
            "badges": ["standardized extra", "gene list"],
            "tips": [
                "Entries should be present in expression.tsv gene identifiers.",
                "Used to restrict inference to regulator candidates.",
            ],
        },
    }
    if basename == "benchmark-manifest.json":
        return {
            "title": "Benchmark manifest",
            "summary": (
                "Top-level index of generated datasets, simulator runs, seeds and "
                "benchmark artifacts."
            ),
            "badges": ["benchmark index", "provenance"],
            "tips": [
                "Use this file to audit which simulator configuration produced each dataset.",
                "Full archives keep this file at the root so every dataset can be traced back to the request.",
            ],
        }
    if basename == "scenario-request.json":
        return {
            "title": "Scenario request",
            "summary": (
                "Frozen generate-data request created by the GUI before preflight and "
                "planning."
            ),
            "badges": ["input contract", "scenario"],
            "tips": [
                "Contains the selected benchmark scenario, benchmark ID, requested extras and scenario-level settings.",
                "Use this with simulator-runs.json to reproduce planning from the CLI.",
            ],
        }
    if basename == "simulator-runs.json":
        return {
            "title": "Simulator runs",
            "summary": (
                "Frozen list of selected simulator configurations, run IDs, replicates "
                "and parameter overrides."
            ),
            "badges": ["input contract", "simulator selection"],
            "tips": [
                "This is the generate-data counterpart of selected tool parameters in infer-network.",
                "Each entry becomes one or more planned simulator tasks.",
            ],
        }
    if basename == "preflight-report.json":
        return {
            "title": "Preflight report",
            "summary": (
                "Simulator compatibility report generated before building the execution plan."
            ),
            "badges": ["preflight", "simulator eligibility"],
            "tips": [
                "Shows eligible, warning and blocked simulators for the requested scenario axes, truth contexts and extras.",
                "Use it to understand why a simulator did or did not enter the plan.",
            ],
        }
    if basename == "simulation-plan.json":
        return {
            "title": "Simulation plan",
            "summary": (
                "Frozen resource plan used to schedule simulator runs and output assembly."
            ),
            "badges": ["planning", "resource waves"],
            "tips": [
                "Contains planned simulator tasks, resources and output locations.",
                "This file is for reproducibility and debugging, not an infer-network input.",
            ],
        }
    if basename == "dataset-manifest.json":
        return {
            "title": "Dataset manifest",
            "summary": (
                "Frozen input contract for one generated dataset. It points to "
                "expression.tsv and any standardized extras available for inference."
            ),
            "badges": ["infer-network handoff", "dataset contract"],
            "tips": [
                "Pass this file to infer-network preflight, plan or run for this dataset.",
                "The manifest references files; it does not duplicate expression or extra-input contents.",
            ],
        }
    if basename == "ground-truth-manifest.json":
        return {
            "title": "Ground-truth manifest",
            "summary": (
                "Strict evaluation handoff for one generated dataset. It indexes the "
                "truth network table and the gene universe used by evaluation."
            ),
            "badges": ["evaluate-inference handoff", "truth index"],
            "tips": [
                "Evaluation flows should use this manifest rather than guessing truth file paths.",
                "The analysis bundle contains this file at the ZIP root for direct GUI upload.",
            ],
        }
    if basename == "expression.tsv":
        return {
            "title": "Expression matrix",
            "summary": (
                "Normalized simulated expression matrix with genes in rows and expression "
                "columns representing samples, cells, timepoints, perturbations or other axes."
            ),
            "badges": ["required input", "TSV matrix"],
            "tips": [
                "This is the expression input referenced by dataset-manifest.json.",
                "Column names are the expression-column identifiers used by extras such as groups.tsv.",
            ],
        }
    if basename in extras_guides:
        return extras_guides[basename]
    if "truth/" in normalized and basename == "networks.csv":
        return {
            "title": "Truth networks",
            "summary": (
                "Unified public ground-truth edge list used by evaluate-inference. "
                "All truth granularities are represented in this single table."
            ),
            "badges": ["ground truth", "network table"],
            "tips": [
                "The context column distinguishes global and scoped contexts such as group:<id> or column:<id>.",
                "Scoped truth stores one network per declared context value in this single file.",
                "Scores are positive magnitudes and direction is stored separately in sign.",
            ],
        }
    if "truth/" in normalized and basename == "gene_universe.txt":
        return {
            "title": "Truth gene universe",
            "summary": (
                "Gene identifiers covered by the exported ground-truth networks, one "
                "identifier per line."
            ),
            "badges": ["ground truth", "gene list"],
            "tips": [
                "Evaluation uses this universe to keep inferred and truth networks comparable.",
                "The file is referenced by ground-truth-manifest.json.",
            ],
        }
    if "truth/" in normalized:
        return {
            "title": "Truth artifact",
            "summary": "Public simulated ground truth for benchmark evaluation.",
            "badges": ["ground truth"],
            "tips": [
                "Use the normalized truth artifacts through ground-truth-manifest.json for evaluation."
            ],
        }
    if basename == "simulator-output-manifest.json":
        return {
            "title": "Simulator output manifest",
            "summary": (
                "Adapter-level output contract produced by the simulator wrapper before "
                "ANDREA assembles the public dataset bundle."
            ),
            "badges": ["provenance", "debug"],
            "tips": ["This is provenance/debug metadata, not an infer-network input."],
        }
    if basename == "simulator-run.json":
        return {
            "title": "Simulator run metadata",
            "summary": (
                "Per-dataset simulator invocation record with the resolved simulator "
                "configuration and wrapper metadata."
            ),
            "badges": ["provenance", "debug"],
            "tips": [
                "Use this to trace a generated dataset back to a concrete simulator run.",
                "Public handoff should use dataset-manifest.json and ground-truth-manifest.json instead.",
            ],
        }
    if basename == "progress.json":
        return {
            "title": "Wrapper progress",
            "summary": "Progress/status file emitted by a simulator wrapper while running.",
            "badges": ["runtime", "debug"],
            "tips": [
                "Useful for diagnosing failed or interrupted simulator executions.",
                "It is not a public analysis input.",
            ],
        }
    if basename in {
        "observed_counts.tsv",
        "true_counts.tsv",
        "cell_meta.tsv",
        "velocity.tsv",
        "rna_velocity.tsv",
        "atac_counts.tsv",
        "milestone_network.tsv",
        "milestone_percentages.tsv",
        "progressions.tsv",
        "regulatory_network_sc.tsv",
        "cell_specific_grn.rds",
    }:
        return {
            "title": f"Native simulator output: {basename}",
            "summary": (
                "Simulator-native artifact kept for inspection and provenance. ANDREA "
                "derives public files such as expression.tsv, truth/networks.csv and "
                "standardized extras from these outputs when applicable."
            ),
            "badges": ["native output", "provenance"],
            "tips": [
                "Use public standardized files for downstream ANDREA commands.",
                "Use native outputs when auditing how the wrapper translated simulator-specific data.",
            ],
        }
    return None


def create_app() -> FastAPI:
    app = FastAPI(title="ANDREA GUI - generate-data")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.mount(
        "/static-common", StaticFiles(directory=COMMON_STATIC_DIR), name="static-common"
    )
    bootstrap = _load_generate_bootstrap()

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"}
        )

    @app.get("/api/generate-data/bootstrap")
    async def api_bootstrap() -> JSONResponse:
        return JSONResponse(bootstrap)

    @app.get("/api/generate-data/jobs")
    async def api_jobs() -> JSONResponse:
        with STATE.lock:
            jobs = [_job_payload(job) for job in STATE.jobs.values()]
        jobs.sort(key=lambda item: item["created_at"], reverse=True)
        return JSONResponse({"jobs": jobs})

    @app.get("/api/generate-data/jobs/{job_id}")
    async def api_job(job_id: str) -> JSONResponse:
        with STATE.lock:
            job = STATE.jobs.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Job not found")
            payload = _job_payload(job)
            runtime_progress = _runtime_progress_payload(job)
            reproducibility = _build_reproducibility_payload(job)
        return JSONResponse(
            {
                "job": payload,
                "preflight_report": read_json_if_exists(
                    payload.get("preflight_report_path")
                ),
                "plan": read_json_if_exists(payload.get("plan_path")),
                "benchmark_manifest": (
                    read_json_if_exists(
                        Path(payload["benchmark_root"]) / "benchmark-manifest.json"
                    )
                    if payload.get("benchmark_root")
                    else None
                ),
                "runtime_progress": runtime_progress,
                "reproducibility": reproducibility,
            }
        )

    @app.get("/api/generate-data/jobs/{job_id}/plan")
    async def api_job_plan(job_id: str) -> JSONResponse:
        with STATE.lock:
            job = STATE.jobs.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Job not found")
            plan_path = _frozen_job_artifact_paths(job)["plan_path"]
            status = job.status
        return JSONResponse(
            {
                "status": status,
                "plan": read_json_if_exists(plan_path),
                "plan_path": plan_path,
            }
        )

    @app.get("/api/generate-data/jobs/{job_id}/bundles")
    async def api_job_bundles(job_id: str) -> JSONResponse:
        with STATE.lock:
            job = STATE.jobs.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Job not found")
            benchmark_root = Path(job.benchmark_root) if job.benchmark_root else None
            status = job.status
        output_ready = bool(benchmark_root and benchmark_root.exists())
        return JSONResponse(
            {
                "status": status,
                "output_ready": output_ready,
                "bundles": _bundle_metadata_for_generate_data(
                    benchmark_root=benchmark_root
                ),
            }
        )

    @app.get("/api/generate-data/jobs/{job_id}/files")
    async def api_job_files(
        job_id: str,
        bundle_id: str = "report",
        dataset_id: Optional[str] = None,
    ) -> JSONResponse:
        with STATE.lock:
            job = STATE.jobs.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Job not found")
            benchmark_root = Path(job.benchmark_root) if job.benchmark_root else None
            status = job.status
        try:
            resolution = _resolve_bundle(
                benchmark_root=benchmark_root,
                bundle_id=bundle_id,
                dataset_id=dataset_id,
            )
            _require_bundle_available(resolution)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        sources = resolution.source_tuples
        return JSONResponse(
            {
                "status": status,
                "bundle_id": resolution.spec.id,
                "dataset_id": dataset_id,
                "mode": resolution.spec.id,
                "missing_required": list(resolution.missing_required),
                "skipped_optional": list(resolution.skipped_optional),
                "entries": build_bundle_entries(
                    sources, viewer_for_path=_viewer_for_virtual_path
                ),
            }
        )

    @app.get("/api/generate-data/jobs/{job_id}/file-content")
    async def api_job_file_content(
        job_id: str,
        path: str,
        bundle_id: str = "report",
        dataset_id: Optional[str] = None,
        max_rows: int = MAX_TABLE_PREVIEW_ROWS,
    ) -> JSONResponse:
        requested_path = str(path or "").strip().lstrip("/")
        if not requested_path:
            raise HTTPException(status_code=400, detail="path is required")
        with STATE.lock:
            job = STATE.jobs.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Job not found")
            benchmark_root = Path(job.benchmark_root) if job.benchmark_root else None
        try:
            resolution = _resolve_bundle(
                benchmark_root=benchmark_root,
                bundle_id=bundle_id,
                dataset_id=dataset_id,
            )
            _require_bundle_available(resolution)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        sources = resolution.source_tuples
        source = resolve_virtual_source(sources=sources, virtual_path=requested_path)
        if source is None or not source.exists() or not source.is_file():
            raise HTTPException(
                status_code=404, detail=f"File not found in bundle: {requested_path}"
            )

        viewer = _viewer_for_virtual_path(requested_path)
        guide = _artifact_guide(requested_path)
        if viewer == "plan":
            return JSONResponse(
                {
                    "path": requested_path,
                    "viewer": "json",
                    "text": json.dumps(
                        read_json_if_exists(source) or {}, indent=2, ensure_ascii=True
                    ),
                    "guide": guide,
                }
            )
        if viewer == "json":
            try:
                payload = json.loads(source.read_text(encoding="utf-8"))
                text = json.dumps(payload, indent=2, ensure_ascii=True)
            except Exception:
                text = source.read_text(encoding="utf-8", errors="replace")
            return JSONResponse(
                {
                    "path": requested_path,
                    "viewer": "json",
                    "text": text,
                    "truncated": False,
                    "guide": guide,
                }
            )
        if viewer == "table_csv":
            table = preview_table(
                source=source,
                delimiter=",",
                max_rows=max(1, min(int(max_rows), MAX_TABLE_PREVIEW_ROWS)),
            )
            return JSONResponse(
                {"path": requested_path, "viewer": "table_csv", **table, "guide": guide}
            )
        if viewer == "table_tsv":
            table = preview_table(
                source=source,
                delimiter="\t",
                max_rows=max(1, min(int(max_rows), MAX_TABLE_PREVIEW_ROWS)),
            )
            return JSONResponse(
                {"path": requested_path, "viewer": "table_tsv", **table, "guide": guide}
            )
        if viewer == "text":
            return JSONResponse(
                {
                    "path": requested_path,
                    "viewer": "text",
                    **preview_text(source, MAX_TEXT_PREVIEW_BYTES),
                    "guide": guide,
                }
            )
        if is_probably_text(source):
            return JSONResponse(
                {
                    "path": requested_path,
                    "viewer": "text",
                    **preview_text(source, MAX_TEXT_PREVIEW_BYTES),
                    "guide": guide,
                }
            )
        if guide:
            return JSONResponse(
                {"path": requested_path, "viewer": "artifact_guide", **guide}
            )
        raise HTTPException(
            status_code=400,
            detail=f"Preview is not available for this file type: {requested_path}",
        )

    @app.get("/api/generate-data/jobs/{job_id}/bundle")
    async def api_job_bundle(
        job_id: str,
        bundle_id: str = "full",
        dataset_id: Optional[str] = None,
    ) -> FileResponse:
        with STATE.lock:
            job = STATE.jobs.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Job not found")
            request_dir = Path(job.request_dir)
            benchmark_root = Path(job.benchmark_root) if job.benchmark_root else None
        try:
            resolution = _resolve_bundle(
                benchmark_root=benchmark_root,
                bundle_id=bundle_id,
                dataset_id=dataset_id,
            )
            _require_bundle_available(resolution)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        suffix = (
            f"{resolution.spec.id}_{dataset_id}"
            if resolution.spec.id == "analysis" and dataset_id
            else resolution.spec.id
        )
        zip_path = request_dir / f"{job_id}_bundle_{suffix}.zip"
        build_zip_bundle(zip_path=zip_path, sources=resolution.source_tuples)
        return FileResponse(
            zip_path,
            media_type="application/zip",
            filename=f"andrea_generate_{job_id}_{suffix}.zip",
        )

    @app.post("/api/generate-data/preflight")
    async def api_preflight(request: Request) -> JSONResponse:
        form = await request.form()
        config_raw = form.get("config")
        if not isinstance(config_raw, str) or not config_raw.strip():
            raise HTTPException(status_code=400, detail="config JSON is required")
        try:
            config = json.loads(config_raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"config JSON is malformed at line {exc.lineno}, column {exc.colno}: {exc.msg}",
            ) from exc
        if not isinstance(config, dict):
            raise HTTPException(status_code=400, detail="config must be a JSON object")

        job_id = uuid.uuid4().hex[:12]
        request_dir = (GUI_TMP_ROOT / job_id).resolve()
        request_dir.mkdir(parents=True, exist_ok=True)
        try:
            scenario_path, options = _write_scenario_request_file(
                request_dir=request_dir,
                config=config,
                form=form,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        output_dir = Path(
            str(options.get("output_dir", "./benchmarks") or "./benchmarks")
        ).resolve()
        job = GuiJob(
            job_id=job_id,
            created_at=_utc_now(),
            status="queued",
            stage="draft",
            request_dir=str(request_dir),
            output_dir=str(output_dir),
            scenario_request_path=str(scenario_path),
        )
        with STATE.lock:
            STATE.jobs[job_id] = job
        start_background_thread(
            target=_run_job,
            kwargs={"job_id": job_id, "action": "preflight", "options": options},
            daemon=True,
        )
        return JSONResponse(
            {
                "job_id": job_id,
                "status": "queued",
                "stage": "draft",
                "scenario_request_path": str(scenario_path),
            }
        )

    @app.post("/api/generate-data/plan")
    async def api_plan(request: Request) -> JSONResponse:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=400, detail="Request body must be a JSON object"
            )
        job_id = str(payload.get("job_id", "")).strip()
        if not job_id:
            raise HTTPException(status_code=400, detail="job_id is required")
        options = payload.get("options") or {}
        if not isinstance(options, dict):
            raise HTTPException(status_code=400, detail="options must be an object")
        with STATE.lock:
            job = STATE.jobs.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Job not found")
            if job.status == "running":
                raise HTTPException(status_code=409, detail="Job is already running")
            if job.stage not in {"preflight_ok", "planned", "executed"}:
                raise HTTPException(
                    status_code=400,
                    detail=f"Job is not ready for planning (stage={job.stage})",
                )
            request_dir = Path(job.request_dir)
        try:
            simulator_runs_path = _write_simulator_runs_file(
                request_dir=request_dir, runs_raw=payload.get("runs")
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        with STATE.lock:
            job = STATE.jobs[job_id]
            job.status = "queued"
            job.stage = "preflight_ok"
            job.simulator_runs_path = str(simulator_runs_path)
            if options.get("output_dir") not in (None, ""):
                job.output_dir = str(Path(str(options["output_dir"])).resolve())
            job.plan_path = None
            job.benchmark_root = None
            job.error = None
            job.traceback = None
            job.runtime_progress = {}
        start_background_thread(
            target=_run_job,
            kwargs={"job_id": job_id, "action": "plan", "options": options},
            daemon=True,
        )
        return JSONResponse(
            {
                "job_id": job_id,
                "status": "queued",
                "stage": "preflight_ok",
                "simulator_runs_path": str(simulator_runs_path),
            }
        )

    @app.post("/api/generate-data/run")
    async def api_run(request: Request) -> JSONResponse:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=400, detail="Request body must be a JSON object"
            )
        job_id = str(payload.get("job_id", "")).strip()
        if not job_id:
            raise HTTPException(status_code=400, detail="job_id is required")
        options = payload.get("options") or {}
        if not isinstance(options, dict):
            raise HTTPException(status_code=400, detail="options must be an object")
        with STATE.lock:
            job = STATE.jobs.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Job not found")
            if job.status == "running":
                raise HTTPException(status_code=409, detail="Job is already running")
            if not job.plan_path:
                raise HTTPException(
                    status_code=400, detail="No planned simulation found for this job"
                )
            if job.stage not in {"planned", "executed"}:
                raise HTTPException(
                    status_code=400,
                    detail=f"Job is not in planned state (stage={job.stage})",
                )
            if options.get("output_dir") not in (None, ""):
                job.output_dir = str(Path(str(options["output_dir"])).resolve())
            job.status = "queued"
            job.error = None
            job.traceback = None
            job.benchmark_root = None
            job.runtime_progress = {}
        start_background_thread(
            target=_run_job,
            kwargs={"job_id": job_id, "action": "run", "options": options},
            daemon=True,
        )
        return JSONResponse({"job_id": job_id, "status": "queued", "stage": "planned"})

    return app


def run_server(*, host: str, port: int, open_browser: bool) -> None:
    app = create_app()
    if open_browser:
        url = f"http://{host}:{port}/"
        timer = threading.Timer(
            0.8, lambda: webbrowser.open(url, new=2, autoraise=True)
        )
        timer.daemon = True
        timer.start()
    uvicorn.run(app, host=host, port=port, log_level="info")
