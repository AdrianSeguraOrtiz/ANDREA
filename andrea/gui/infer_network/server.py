"""Local web GUI for infer-network."""

from __future__ import annotations

import copy
import csv
import json
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

from andrea.core.commands.infer_network import (
    bundles as infer_network_bundles,
    plan_infer_network,
    preflight_infer_network,
    run_infer_network_plan,
)
from andrea.core.shared.bundles import (
    BundleResolution,
    BundleSpec,
    all_files,
)
from andrea.core.commands.infer_network.commons.catalog import (
    _load_schema_constraints,
    _resolve_catalog_paths,
)
from andrea.core.commands.infer_network.commons.dataset import (
    _inspect_expression_tsv,
    _load_input_specs,
)
from andrea.core.commands.infer_network.commons.execution_state import (
    execution_state_path,
    read_execution_state_if_exists,
)
from andrea.gui.common.reproducibility import (
    append_cli_option,
    python_literal,
    python_path_expr,
    shell_join_pretty,
    unavailable_reproducibility,
)
from andrea.gui.common.server_files import (
    build_bundle_entries,
    build_bundle_metadata,
    build_zip_bundle,
    bundle_status_payload,
    read_json_if_exists,
    resolve_virtual_source,
    save_upload,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
COMMON_STATIC_DIR = Path(__file__).resolve().parents[1] / "common" / "static"
GUI_TMP_ROOT = Path(tempfile.gettempdir()) / "andrea_gui" / "infer_network"
MAX_TEXT_PREVIEW_BYTES = 256 * 1024
MAX_TABLE_PREVIEW_ROWS = 400


@dataclass
class GuiJob:
    job_id: str
    created_at: str
    status: str
    request_dir: str
    output_dir: str
    stage: str = "draft"
    run_dir: Optional[str] = None
    dataset_manifest_path: Optional[str] = None
    tools_params_path: Optional[str] = None
    preflight_report_path: Optional[str] = None
    run_report_path: Optional[str] = None
    plan_path: Optional[str] = None
    error: Optional[str] = None
    traceback: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    active_action: Optional[str] = None
    progress_percent: int = 0
    progress_label: str = ""
    progress_detail: str = ""
    planner: Optional[str] = None
    planner_time_limit_seconds: Optional[float] = None


@dataclass
class GuiState:
    jobs: dict[str, GuiJob] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)


STATE = GuiState()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_tools_bootstrap() -> dict[str, Any]:
    tools_root, schemas_dir = _resolve_catalog_paths()
    constraints = _load_schema_constraints(schemas_dir)
    input_specs = _load_input_specs()
    expr_spec = input_specs.get("expression_matrix", {})

    extra_examples = {
        "groups": "sample\tcluster\nS1\tA\nS2\tB",
        "cell_phenotypes": "cell\tphenotype\torder\ncell_1\tIP\t0\ncell_2\tIP\t0\ncell_3\tD12\t1",
        "lineage_tree": "child\tparent\tgain_rate\tloss_rate\nC2\tC1\t0.2\t0.1",
        "pseudotime": "cell\tpseudotime\ncell_1\t0.0\ncell_2\t0.4\ncell_3\t1.0",
        "tf_list": "SOX2\nMYC\nTP53",
        "prior_grn_by_group": "group\tsource\ttarget\tscore\nA\tG1\tG2\t0.82\nB\tG1\tG3\t0.41",
    }

    tools: list[dict[str, Any]] = []
    extra_usage_by_input: dict[str, dict[str, list[dict[str, Any]]]] = {}

    def _parse_extra_usage_entries(raw_items: Any) -> list[dict[str, str]]:
        if not isinstance(raw_items, list):
            return []
        entries: list[dict[str, str]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            input_key = str(item.get("input", "")).strip()
            item_usage = str(item.get("usage", "")).strip()
            if input_key:
                entries.append({"input": input_key, "usage": item_usage})
        return entries

    for toolspec_path in sorted(tools_root.glob("*/toolspec.json")):
        tool_dir = toolspec_path.parent
        tool_id = tool_dir.name
        with toolspec_path.open("r", encoding="utf-8") as fh:
            toolspec = json.load(fh)

        raw_params = toolspec.get("params", {})
        params_schema = raw_params if isinstance(raw_params, dict) else {}
        defaults = {
            key: _default_for_param(param_def)
            for key, param_def in params_schema.items()
            if isinstance(param_def, dict)
        }

        extra_inputs = toolspec.get("extra_inputs", {})
        required_extras = []
        optional_extras = []
        conditional_required_extras = []
        if isinstance(extra_inputs, dict):
            req = extra_inputs.get("required", [])
            opt = extra_inputs.get("optional", [])
            cond = extra_inputs.get("conditional_required", [])
            required_entries = _parse_extra_usage_entries(req)
            optional_entries = _parse_extra_usage_entries(opt)
            required_extras = [item["input"] for item in required_entries]
            optional_extras = [item["input"] for item in optional_entries]
            if isinstance(cond, list):
                conditional_required_extras = [
                    item for item in cond if isinstance(item, dict)
                ]
            for relation, entries in (
                ("required", required_entries),
                ("optional", optional_entries),
            ):
                for entry in entries:
                    input_key = entry["input"]
                    usage_bucket = extra_usage_by_input.setdefault(
                        input_key,
                        {"required": [], "optional": [], "conditional": []},
                    )
                    usage_bucket[relation].append(
                        {
                            "tool_id": tool_id,
                            "name": str(toolspec.get("name", tool_id)),
                            "usage": entry["usage"],
                        }
                    )
            for rule in conditional_required_extras:
                input_key = str(rule.get("input", "")).strip()
                if not input_key:
                    continue
                usage_bucket = extra_usage_by_input.setdefault(
                    input_key,
                    {"required": [], "optional": [], "conditional": []},
                )
                usage_bucket["conditional"].append(
                    {
                        "tool_id": tool_id,
                        "name": str(toolspec.get("name", tool_id)),
                        "usage": str(rule.get("usage", "")).strip(),
                        "condition": {
                            key: rule.get(key)
                            for key in ("param", "execution", "op", "value", "message")
                            if key in rule
                        },
                    }
                )

        tools.append(
            {
                "tool_id": tool_id,
                "name": str(toolspec.get("name", tool_id)),
                "schema_version": str(toolspec.get("schema_version", "")),
                "execution_capabilities": [
                    str(x)
                    for x in toolspec.get("execution_capabilities", [])
                    if isinstance(x, str)
                ],
                "taxonomic_scope": toolspec.get("taxonomic_scope", {}),
                "compatibility_rules": toolspec.get("compatibility_rules", []),
                "method_summary": str(toolspec.get("method_summary", "")),
                "method_keywords": [
                    x for x in toolspec.get("method_keywords", []) if isinstance(x, str)
                ],
                "assumes": str(toolspec.get("assumes", "")),
                "accepts": [
                    x for x in toolspec.get("accepts", []) if isinstance(x, str)
                ],
                "required_extras": required_extras,
                "optional_extras": optional_extras,
                "conditional_required_extras": conditional_required_extras,
                "publication": (
                    [
                        str(x)
                        for x in toolspec.get("publication", [])
                        if isinstance(x, str)
                    ]
                    if isinstance(toolspec.get("publication"), list)
                    else (
                        [str(toolspec.get("publication"))]
                        if toolspec.get("publication")
                        else []
                    )
                ),
                "first_author": str(toolspec.get("first_author", "")),
                "year": toolspec.get("year"),
                "implementation_url": str(toolspec.get("implementation_url", "")),
                "docker_image": str(toolspec.get("docker_image", "")),
                "outputs": toolspec.get("outputs", {}),
                "progress": toolspec.get("progress", {}),
                "artifacts_aux": toolspec.get("artifacts_aux", []),
                "params_schema": params_schema,
                "default_params": defaults,
                "spec": toolspec,
            }
        )

    return {
        "column_kinds": sorted(constraints.column_kinds),
        "expression_profiles": sorted(constraints.expression_profiles),
        "taxonomic_groups": sorted(constraints.taxonomic_groups),
        "dataset_input_help": {
            "expression_matrix": {
                "description": str(expr_spec.get("description", "")),
                "example": str(
                    expr_spec.get(
                        "example",
                        "gene\tcell_1\tcell_2\nG1\t0.12\t0.20\nG2\t1.30\t0.95",
                    )
                ),
                "file_kind": str(expr_spec.get("file_kind", "tsv")),
                "delimiter": str(expr_spec.get("delimiter", "\t")),
                "header": bool(expr_spec.get("header", True)),
                "min_rows": int(expr_spec.get("min_rows", 1) or 1),
                "min_columns": int(expr_spec.get("min_columns", 2) or 2),
                "required_columns": [
                    str(col)
                    for col in expr_spec.get("required_columns", [])
                    if isinstance(col, str)
                ],
                "column_types": (
                    expr_spec.get("column_types", {})
                    if isinstance(expr_spec.get("column_types", {}), dict)
                    else {}
                ),
                "first_column_role": str(expr_spec.get("first_column_role", "none")),
                "first_column_disallowed_names": [
                    str(x)
                    for x in expr_spec.get("first_column_disallowed_names", [])
                    if isinstance(x, str)
                ],
                "unique_first_column": bool(
                    expr_spec.get("unique_first_column", False)
                ),
                "data_columns_type": str(expr_spec.get("data_columns_type", "any")),
                "data_numeric_min_fraction": float(
                    expr_spec.get("data_numeric_min_fraction", 1.0) or 1.0
                ),
            }
        },
        "extra_inputs": [
            {
                "key": key,
                "default_filename": constraints.extra_input_filenames.get(
                    key, f"{key}.tsv"
                ),
                "description": str(input_specs.get(key, {}).get("description", "")),
                "example": str(
                    input_specs.get(key, {}).get("example", extra_examples.get(key, ""))
                ),
                "file_kind": str(input_specs.get(key, {}).get("file_kind", "tsv")),
                "delimiter": str(input_specs.get(key, {}).get("delimiter", "\t")),
                "header": bool(input_specs.get(key, {}).get("header", True)),
                "min_rows": int(input_specs.get(key, {}).get("min_rows", 0) or 0),
                "min_columns": int(input_specs.get(key, {}).get("min_columns", 1) or 1),
                "required_columns": [
                    str(col)
                    for col in input_specs.get(key, {}).get("required_columns", [])
                    if isinstance(col, str)
                ],
                "column_types": (
                    input_specs.get(key, {}).get("column_types", {})
                    if isinstance(
                        input_specs.get(key, {}).get("column_types", {}), dict
                    )
                    else {}
                ),
                "first_column_role": str(
                    input_specs.get(key, {}).get("first_column_role", "none")
                ),
                "first_column_disallowed_names": [
                    str(x)
                    for x in input_specs.get(key, {}).get(
                        "first_column_disallowed_names", []
                    )
                    if isinstance(x, str)
                ],
                "unique_first_column": bool(
                    input_specs.get(key, {}).get("unique_first_column", False)
                ),
                "data_columns_type": str(
                    input_specs.get(key, {}).get("data_columns_type", "any")
                ),
                "data_numeric_min_fraction": float(
                    input_specs.get(key, {}).get("data_numeric_min_fraction", 1.0)
                    or 1.0
                ),
                "used_by": extra_usage_by_input.get(
                    key, {"required": [], "optional": [], "conditional": []}
                ),
            }
            for key in sorted(constraints.extra_input_keys)
        ],
        "tools": tools,
    }


def _default_for_param(param_def: dict[str, Any]) -> Any:
    if "default" in param_def:
        return copy.deepcopy(param_def.get("default"))

    param_type = param_def.get("type")
    if param_type == "object":
        properties = param_def.get("properties", {})
        if not isinstance(properties, dict):
            return {}
        out: dict[str, Any] = {}
        for key, sub_def in properties.items():
            if not isinstance(sub_def, dict):
                continue
            has_default = "default" in sub_def
            is_required = bool(sub_def.get("required"))
            if has_default or is_required:
                out[key] = _default_for_param(sub_def)
        return out

    if param_type == "array":
        return []

    return None


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return float(value)


def _safe_int(value: Any, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, str) and not value.strip():
        return default
    return int(value)


def _normalize_runs(raw_runs: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_runs, list) or not raw_runs:
        raise ValueError("runs must be a non-empty array")

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for idx, raw in enumerate(raw_runs, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"runs[{idx}] must be an object")

        tool_id = str(raw.get("tool_id", "")).strip()
        if not tool_id:
            raise ValueError(f"runs[{idx}].tool_id is required")

        run_id_raw = raw.get("run_id")
        if run_id_raw is None or (
            isinstance(run_id_raw, str) and not run_id_raw.strip()
        ):
            run_id = f"{tool_id}__{idx:02d}"
        elif isinstance(run_id_raw, str):
            run_id = run_id_raw.strip()
        else:
            raise ValueError(f"runs[{idx}].run_id must be string when provided")

        if run_id in seen_ids:
            raise ValueError(f"Duplicate run_id: {run_id}")
        seen_ids.add(run_id)

        params = raw.get("params", {})
        if not isinstance(params, dict):
            raise ValueError(f"runs[{idx}].params must be an object")
        execution = raw.get("execution", {})
        if execution is None:
            execution = {}
        if not isinstance(execution, dict):
            raise ValueError(f"runs[{idx}].execution must be an object")

        normalized.append(
            {
                "run_id": run_id,
                "tool_id": tool_id,
                "params": params,
                "execution": execution,
            }
        )

    return normalized


def _build_dataset_manifest_file(
    *,
    config: dict[str, Any],
    form: Any,
    request_dir: Path,
    bootstrap: dict[str, Any],
) -> tuple[Path, Path, dict[str, Any]]:
    dataset_cfg = config.get("dataset")
    if not isinstance(dataset_cfg, dict):
        raise ValueError("config.dataset must be an object")

    options_cfg = config.get("options")
    if options_cfg is None:
        options_cfg = {}
    if not isinstance(options_cfg, dict):
        raise ValueError("config.options must be an object when provided")

    dataset_id = str(dataset_cfg.get("id", "")).strip()
    if not dataset_id:
        raise ValueError("config.dataset.id is required")

    column_kind = str(dataset_cfg.get("column_kind", "")).strip()
    if column_kind not in set(bootstrap["column_kinds"]):
        raise ValueError(
            f"config.dataset.column_kind must be one of {bootstrap['column_kinds']}"
        )

    expression_profile = str(dataset_cfg.get("expression_profile", "")).strip()
    if expression_profile not in set(bootstrap["expression_profiles"]):
        raise ValueError(
            "config.dataset.expression_profile must be one of "
            f"{bootstrap['expression_profiles']}"
        )

    organism_cfg = dataset_cfg.get("organism")
    if not isinstance(organism_cfg, dict):
        raise ValueError("config.dataset.organism must be an object")
    organism_keys = set(organism_cfg)
    expected_organism_keys = {"taxonomic_group", "ncbi_taxon_id"}
    if organism_keys != expected_organism_keys:
        raise ValueError(
            "config.dataset.organism must contain exactly taxonomic_group and ncbi_taxon_id"
        )
    taxonomic_group = str(organism_cfg.get("taxonomic_group", "")).strip()
    if taxonomic_group not in set(bootstrap["taxonomic_groups"]):
        raise ValueError(
            "config.dataset.organism.taxonomic_group must be one of "
            f"{bootstrap['taxonomic_groups']}"
        )
    ncbi_taxon_id_raw = organism_cfg.get("ncbi_taxon_id")
    if ncbi_taxon_id_raw is None:
        ncbi_taxon_id = None
    else:
        ncbi_taxon_id = _safe_int(ncbi_taxon_id_raw, default=0)
        if ncbi_taxon_id < 1:
            raise ValueError(
                "config.dataset.organism.ncbi_taxon_id must be an integer >= 1 or null"
            )
    if taxonomic_group not in {"synthetic", "unknown"} and ncbi_taxon_id is None:
        raise ValueError(
            "config.dataset.organism.ncbi_taxon_id must be provided for biological taxonomic groups"
        )

    expression_upload = form.get("expression_file")
    if expression_upload is None or not getattr(expression_upload, "filename", ""):
        raise ValueError("expression_file is required")

    inputs_dir = request_dir / "inputs"
    extras_dir = inputs_dir / "extra"
    expression_path = inputs_dir / "expression.tsv"
    save_upload(expression_upload, expression_path)

    observed_genes, observed_columns = _inspect_expression_tsv(expression_path)
    genes = _safe_int(dataset_cfg.get("genes"), default=observed_genes)
    columns = _safe_int(dataset_cfg.get("columns"), default=observed_columns)

    if genes != observed_genes or columns != observed_columns:
        raise ValueError(
            "The provided genes/columns do not match the uploaded expression matrix: "
            f"provided={genes}x{columns}, observed={observed_genes}x{observed_columns}"
        )

    extras_payload: dict[str, Optional[str]] = {}
    extra_keys = [item["key"] for item in bootstrap["extra_inputs"]]
    extra_default_filenames = {
        item["key"]: item["default_filename"] for item in bootstrap["extra_inputs"]
    }
    for key in extra_keys:
        upload = form.get(f"extra__{key}")
        if upload is None or not getattr(upload, "filename", ""):
            extras_payload[key] = None
            continue
        filename = extra_default_filenames.get(key, f"{key}.tsv")
        destination = extras_dir / filename
        save_upload(upload, destination)
        extras_payload[key] = str(Path("inputs") / "extra" / filename)

    dataset_manifest = {
        "schema_version": "1.0",
        "id": dataset_id,
        "dataset": {
            "spec": {
                "schema_version": "1.0",
                "id": dataset_id,
                "name": dataset_id,
                "expression": {
                    "column_kind": column_kind,
                    "expression_profile": expression_profile,
                    "genes": genes,
                    "columns": columns,
                },
                "organism": {
                    "taxonomic_group": taxonomic_group,
                    "ncbi_taxon_id": ncbi_taxon_id,
                },
            },
            "expression_matrix": str(Path("inputs") / "expression.tsv"),
        },
        "extras": extras_payload,
    }

    dataset_manifest_path = request_dir / "dataset-manifest.json"

    dataset_manifest_path.write_text(
        json.dumps(dataset_manifest, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return dataset_manifest_path, expression_path, options_cfg


def _write_tools_params_file(
    *,
    request_dir: Path,
    runs_raw: Any,
) -> Path:
    runs = _normalize_runs(runs_raw)
    tools_params = {"runs": runs}
    tools_params_path = request_dir / "tools_params.json"
    tools_params_path.write_text(
        json.dumps(tools_params, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return tools_params_path


def _job_payload(job: GuiJob) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "created_at": job.created_at,
        "status": job.status,
        "stage": job.stage,
        "request_dir": job.request_dir,
        "output_dir": job.output_dir,
        "run_dir": job.run_dir,
        "dataset_manifest_path": job.dataset_manifest_path,
        "tools_params_path": job.tools_params_path,
        "preflight_report_path": job.preflight_report_path,
        "run_report_path": job.run_report_path,
        "plan_path": job.plan_path,
        "error": job.error,
        "traceback": job.traceback,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "active_action": job.active_action,
        "progress_percent": job.progress_percent,
        "progress_label": job.progress_label,
        "progress_detail": job.progress_detail,
        "planner": job.planner,
        "planner_time_limit_seconds": job.planner_time_limit_seconds,
        "artifact_errors": [],
        "bundle_status": _job_bundle_status(job),
    }


def _build_reproducibility_payload(job: GuiJob) -> dict[str, Any]:
    run_dir_raw = str(job.run_dir or "").strip()
    if not run_dir_raw:
        return unavailable_reproducibility(
            "Reproducibility snippets will be available after planning and execution."
        )
    run_dir = Path(run_dir_raw).resolve()
    dataset_manifest = (run_dir / "input" / "dataset-manifest.json").resolve()
    tools_params = (run_dir / "input" / "tools_params.json").resolve()
    if not dataset_manifest.exists() or not tools_params.exists():
        return unavailable_reproducibility(
            "Frozen run inputs are not available yet in the output directory."
        )

    dataset_manifest_path = str(dataset_manifest)
    tools_params_path = str(tools_params)
    plan_payload = (
        read_json_if_exists(job.plan_path or str(run_dir / "plan.json")) or {}
    )
    resource_limits = (
        plan_payload.get("resource_limits", {})
        if isinstance(plan_payload.get("resource_limits"), dict)
        else {}
    )
    planner_payload = (
        plan_payload.get("planner", {})
        if isinstance(plan_payload.get("planner"), dict)
        else {}
    )

    output_dir = str(job.output_dir).strip()
    max_cores = resource_limits.get("max_cores", 4)
    max_ram_gb = resource_limits.get("max_ram_gb")
    planner = str(planner_payload.get("requested", "auto") or "auto")
    planner_time_limit_seconds = float(
        planner_payload.get("cp_sat_time_limit_seconds", 100.0)
    )
    progress_poll_seconds = 0.5
    preflight_output_json = str((run_dir / "preflight_report.json").resolve())

    cli_unified_args = [
        "andrea",
        "infer-network",
        "execute",
        "--dataset-manifest",
        dataset_manifest_path,
        "--tools-params",
        tools_params_path,
        "--output-dir",
        output_dir,
        "--max-cores",
        str(max_cores),
        "--planner",
        planner,
        "--planner-time-limit-seconds",
        str(planner_time_limit_seconds),
        "--progress-poll-seconds",
        str(progress_poll_seconds),
    ]
    append_cli_option(cli_unified_args, "--max-ram-gb", max_ram_gb)

    cli_preflight_args = [
        "andrea",
        "infer-network",
        "preflight",
        "--dataset-manifest",
        dataset_manifest_path,
        "--tools-params",
        tools_params_path,
        "--output-json",
        preflight_output_json,
    ]

    cli_plan_args = [
        "andrea",
        "infer-network",
        "plan",
        "--dataset-manifest",
        dataset_manifest_path,
        "--tools-params",
        tools_params_path,
        "--output-dir",
        output_dir,
        "--max-cores",
        str(max_cores),
        "--planner",
        planner,
        "--planner-time-limit-seconds",
        str(planner_time_limit_seconds),
    ]
    append_cli_option(cli_plan_args, "--max-ram-gb", max_ram_gb)

    cli_run_args = [
        "andrea",
        "infer-network",
        "run",
        "--run-dir",
        "<run_dir produced by the plan step>",
        "--progress-poll-seconds",
        str(progress_poll_seconds),
    ]

    python_unified = "\n".join(
        [
            "from pathlib import Path",
            "",
            "from andrea.core.commands.infer_network import infer_network",
            "",
            "run_dir = infer_network(",
            f"    dataset_manifest_path={python_path_expr(dataset_manifest_path)},",
            f"    tools_params_path={python_path_expr(tools_params_path)},",
            f"    output_dir={python_path_expr(output_dir)},",
            f"    max_cores={int(max_cores)},",
            f"    max_ram_gb={python_literal(max_ram_gb)},",
            f"    planner={python_literal(planner)},",
            f"    planner_time_limit_seconds={planner_time_limit_seconds},",
            f"    progress_poll_seconds={progress_poll_seconds},",
            ")",
            "",
            "print(run_dir)",
        ]
    )

    python_steps = "\n".join(
        [
            "from pathlib import Path",
            "",
            "from andrea.core.commands.infer_network import (",
            "    plan_infer_network,",
            "    preflight_infer_network,",
            "    run_infer_network_plan,",
            ")",
            "",
            f"dataset_manifest_path = {python_path_expr(dataset_manifest_path)}",
            f"tools_params_path = {python_path_expr(tools_params_path)}",
            f"output_dir = {python_path_expr(output_dir)}",
            "",
            "preflight_report = preflight_infer_network(",
            "    dataset_manifest_path=dataset_manifest_path,",
            "    tools_params_path=tools_params_path,",
            ")",
            "",
            "run_dir = plan_infer_network(",
            "    dataset_manifest_path=dataset_manifest_path,",
            "    tools_params_path=tools_params_path,",
            "    output_dir=output_dir,",
            f"    max_cores={int(max_cores)},",
            f"    max_ram_gb={python_literal(max_ram_gb)},",
            f"    planner={python_literal(planner)},",
            f"    planner_time_limit_seconds={planner_time_limit_seconds},",
            "    preflight_report=preflight_report,",
            ")",
            "",
            "run_dir = run_infer_network_plan(",
            "    run_dir=run_dir,",
            f"    progress_poll_seconds={progress_poll_seconds},",
            ")",
            "",
            "print(run_dir)",
        ]
    )

    return {
        "available": True,
        "cli": {
            "title": "CLI",
            "summary": "Replay this GUI job using the frozen inputs stored in the run output directory.",
            "primary_label": "Unified command",
            "primary_language": "bash",
            "primary_code": shell_join_pretty(cli_unified_args),
            "steps_label": "If you prefer by steps",
            "steps": [
                {
                    "title": "1. Preflight",
                    "language": "bash",
                    "code": shell_join_pretty(cli_preflight_args),
                },
                {
                    "title": "2. Plan",
                    "language": "bash",
                    "code": shell_join_pretty(cli_plan_args),
                },
                {
                    "title": "3. Run",
                    "language": "bash",
                    "code": shell_join_pretty(cli_run_args),
                },
            ],
        },
        "python": {
            "title": "Python",
            "summary": "Replay this GUI job using the current infer-network Python API and the frozen run inputs.",
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


def _collect_runtime_progress(*, run_dir: Optional[Path]) -> dict[str, Any]:
    if run_dir is None or not run_dir.exists() or not run_dir.is_dir():
        return {
            "tools": [],
            "summary": {
                "total": 0,
                "completed": 0,
                "failed": 0,
                "running": 0,
                "pending": 0,
            },
        }

    plan_payload = read_json_if_exists(str(run_dir / "plan.json"))
    status_by_tool: dict[str, str] = {}
    logical_results: dict[str, Any] = {}
    run_report = read_json_if_exists(str(run_dir / "run_report.json"))
    if isinstance(run_report, dict):
        tools_payload = run_report.get("tools")
        if isinstance(tools_payload, dict):
            status_payload = tools_payload.get("status_by_tool")
            if isinstance(status_payload, dict):
                status_by_tool = {
                    str(tool_id): str(status)
                    for tool_id, status in status_payload.items()
                    if isinstance(tool_id, str)
                }
            results_payload = tools_payload.get("results")
            if isinstance(results_payload, dict):
                logical_results = {
                    str(run_id): value
                    for run_id, value in results_payload.items()
                    if isinstance(run_id, str) and isinstance(value, dict)
                }

    logical_runs: list[dict[str, Any]] = []
    if isinstance(plan_payload, dict):
        raw_runs = plan_payload.get("runs")
        if isinstance(raw_runs, list):
            logical_runs = [item for item in raw_runs if isinstance(item, dict)]

    tool_entries: list[dict[str, Any]] = []
    if logical_runs:
        for logical in logical_runs:
            run_id = str(logical.get("run_id", "")).strip()
            if not run_id:
                continue
            tool_dir = run_dir / "tools" / run_id
            progress_file = tool_dir / "io" / "out" / "progress.json"
            direct_payload: dict[str, Any] = {}
            percent = 0
            status = status_by_tool.get(run_id, "pending")
            phase = "pending"
            message = ""
            updated_at: Optional[str] = None

            if progress_file.exists() and progress_file.is_file():
                try:
                    direct_payload = json.loads(
                        progress_file.read_text(encoding="utf-8")
                    )
                except Exception:  # noqa: BLE001
                    direct_payload = {}
                percent = int(direct_payload.get("percent", 0))
                status = str(direct_payload.get("status", status))
                phase = str(direct_payload.get("phase", phase))
                message = str(direct_payload.get("message", ""))
                updated_at = (
                    datetime.fromtimestamp(
                        progress_file.stat().st_mtime, tz=timezone.utc
                    )
                    .isoformat()
                    .replace("+00:00", "Z")
                )
            else:
                physical_tasks = logical.get("physical_tasks", [])
                if isinstance(physical_tasks, list) and len(physical_tasks) > 1:
                    child_results = logical_results.get(run_id, {}).get(
                        "child_results", {}
                    )
                    weighted_total = 0.0
                    weighted_progress = 0.0
                    completed = 0
                    failed = 0
                    running = 0
                    pending = 0
                    for child in physical_tasks:
                        if not isinstance(child, dict):
                            continue
                        output_dir = str(child.get("output_dir", "")).strip()
                        if not output_dir:
                            continue
                        weight = float(child.get("eta_seconds", 0.0) or 0.0)
                        weight = max(weight, 1.0)
                        child_progress_file = (
                            run_dir / output_dir / "io" / "out" / "progress.json"
                        )
                        child_percent = 0
                        child_status = "pending"
                        child_updated_at: Optional[str] = None
                        if (
                            child_progress_file.exists()
                            and child_progress_file.is_file()
                        ):
                            try:
                                payload = json.loads(
                                    child_progress_file.read_text(encoding="utf-8")
                                )
                            except Exception:  # noqa: BLE001
                                payload = {}
                            child_percent = int(payload.get("percent", 0))
                            child_status = str(payload.get("status", "pending"))
                            child_updated_at = (
                                datetime.fromtimestamp(
                                    child_progress_file.stat().st_mtime,
                                    tz=timezone.utc,
                                )
                                .isoformat()
                                .replace("+00:00", "Z")
                            )
                        else:
                            task_id = str(child.get("task_id", "")).strip()
                            if (
                                isinstance(child_results, dict)
                                and task_id
                                and isinstance(child_results.get(task_id), dict)
                            ):
                                payload = child_results[task_id]
                                child_status = str(payload.get("status", "pending"))
                                if child_status in {"completed", "failed"}:
                                    child_percent = 100

                        child_status = child_status.lower().strip()
                        if child_status == "completed":
                            completed += 1
                        elif child_status == "failed":
                            failed += 1
                        elif child_status == "running":
                            running += 1
                        else:
                            pending += 1

                        if child_updated_at is not None:
                            if updated_at is None or child_updated_at > updated_at:
                                updated_at = child_updated_at
                        weighted_total += weight
                        weighted_progress += weight * max(0, min(100, child_percent))

                    if weighted_total > 0:
                        percent = int(round(weighted_progress / weighted_total))
                    if status not in {"completed", "failed"}:
                        if running > 0:
                            status = "running"
                        elif pending == len(physical_tasks):
                            status = "pending"
                        elif completed > 0 and failed == 0 and pending == 0:
                            status = "completed"
                        elif failed == len(physical_tasks):
                            status = "failed"
                        else:
                            status = "running"
                    phase = "grouped"
                    message = (
                        f"{completed}/{len(physical_tasks)} groups completed"
                        + (f", {failed} failed" if failed else "")
                        + (f", {running} running" if running else "")
                    )
                elif status in {"completed", "failed"}:
                    percent = 100
                    phase = "done" if status == "completed" else "failed"

            percent = max(0, min(100, int(percent)))
            normalized_status = status.lower().strip()
            if normalized_status not in {"pending", "running", "completed", "failed"}:
                normalized_status = "running" if percent > 0 else "pending"
            tool_entries.append(
                {
                    "run_id": run_id,
                    "percent": percent,
                    "status": normalized_status,
                    "phase": phase,
                    "message": message,
                    "updated_at": updated_at,
                }
            )
    else:
        tools_root = run_dir / "tools"
        if tools_root.exists() and tools_root.is_dir():
            for tool_dir in sorted(tools_root.iterdir()):
                if not tool_dir.is_dir():
                    continue
                run_id = tool_dir.name
                progress_file = tool_dir / "io" / "out" / "progress.json"
                percent = 0
                status = status_by_tool.get(run_id, "pending")
                phase = "pending"
                message = ""
                updated_at: Optional[str] = None

                if progress_file.exists() and progress_file.is_file():
                    try:
                        payload = json.loads(progress_file.read_text(encoding="utf-8"))
                    except Exception:  # noqa: BLE001
                        payload = {}
                    percent = int(payload.get("percent", percent))
                    status = str(payload.get("status", status))
                    phase = str(payload.get("phase", phase))
                    message = str(payload.get("message", ""))
                    updated_at = (
                        datetime.fromtimestamp(
                            progress_file.stat().st_mtime, tz=timezone.utc
                        )
                        .isoformat()
                        .replace("+00:00", "Z")
                    )
                elif status in {"completed", "failed"}:
                    percent = 100
                    phase = "done" if status == "completed" else "failed"

                percent = max(0, min(100, int(percent)))
                normalized_status = status.lower().strip()
                if normalized_status not in {
                    "pending",
                    "running",
                    "completed",
                    "failed",
                }:
                    normalized_status = "running" if percent > 0 else "pending"
                tool_entries.append(
                    {
                        "run_id": run_id,
                        "percent": percent,
                        "status": normalized_status,
                        "phase": phase,
                        "message": message,
                        "updated_at": updated_at,
                    }
                )

    summary = {
        "total": len(tool_entries),
        "completed": sum(1 for item in tool_entries if item["status"] == "completed"),
        "failed": sum(1 for item in tool_entries if item["status"] == "failed"),
        "running": sum(1 for item in tool_entries if item["status"] == "running"),
        "pending": sum(1 for item in tool_entries if item["status"] == "pending"),
    }
    return {"tools": tool_entries, "summary": summary}


def _read_execution_state_payload(*, run_dir: Optional[Path]) -> Optional[dict[str, Any]]:
    if run_dir is None or not run_dir.exists() or not run_dir.is_dir():
        return None
    return read_execution_state_if_exists(execution_state_path(run_dir))


def _runtime_progress_from_execution_state(
    execution_state: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    if not isinstance(execution_state, dict):
        return None

    entries_payload = execution_state.get("logical_runs")
    if not isinstance(entries_payload, dict) or not entries_payload:
        entries_payload = execution_state.get("tools")
    if not isinstance(entries_payload, dict):
        return None

    def _normalize_execution_status(status: Any) -> str:
        value = str(status or "").strip().lower()
        if value == "queued":
            return "pending"
        if value == "completed_with_warnings":
            return "completed"
        if value in {"pending", "running", "completed", "failed"}:
            return value
        return "running" if value else "pending"

    tool_entries: list[dict[str, Any]] = []
    for fallback_id, entry in sorted(entries_payload.items()):
        if not isinstance(entry, dict):
            continue
        run_id = str(entry.get("run_id") or fallback_id).strip()
        if not run_id:
            continue
        tool_entries.append(
            {
                "run_id": run_id,
                "tool_id": str(entry.get("tool_id", "")).strip(),
                "percent": max(0, min(100, int(entry.get("percent", 0) or 0))),
                "status": _normalize_execution_status(entry.get("status")),
                "phase": str(entry.get("phase", "")).strip(),
                "message": str(entry.get("message", "")).strip(),
                "updated_at": execution_state.get("updated_at"),
                "errors": entry.get("errors", []),
                "warnings": entry.get("warnings", []),
            }
        )

    summary_payload = execution_state.get("summary")
    if isinstance(summary_payload, dict):
        summary = {
            "total": int(summary_payload.get("total", len(tool_entries)) or 0),
            "completed": int(summary_payload.get("completed", 0) or 0),
            "failed": int(summary_payload.get("failed", 0) or 0),
            "running": int(summary_payload.get("running", 0) or 0),
            "pending": int(summary_payload.get("queued", 0) or 0),
            "warnings": int(summary_payload.get("warnings", 0) or 0),
        }
    else:
        summary = {
            "total": len(tool_entries),
            "completed": sum(
                1 for item in tool_entries if item["status"] == "completed"
            ),
            "failed": sum(1 for item in tool_entries if item["status"] == "failed"),
            "running": sum(
                1 for item in tool_entries if item["status"] == "running"
            ),
            "pending": sum(
                1 for item in tool_entries if item["status"] == "pending"
            ),
            "warnings": sum(
                len(item.get("warnings", []))
                for item in tool_entries
                if isinstance(item.get("warnings", []), list)
            ),
        }

    return {"tools": tool_entries, "summary": summary}


def _collect_output_readiness(
    *,
    run_dir: Optional[Path],
    run_report: Optional[dict[str, Any]],
    execution_state: Optional[dict[str, Any]],
) -> dict[str, Any]:
    if run_dir is None or not run_dir.exists() or not run_dir.is_dir():
        return {
            "explorer_available": False,
            "csv_ready": False,
            "raw_csv_ready": False,
            "normalized_csv_ready": False,
            "run_report_file_ready": False,
            "final_report_ready": False,
            "graph_exports_ready": False,
            "partial": False,
            "failed_runs": 0,
            "finalizing_artifacts": False,
            "message": "Results Explorer will be available after execution.",
            "paths": {},
        }

    raw_csv = run_dir / "merged_network_raw.csv"
    normalized_csv = run_dir / "merged_network_normalized.csv"
    run_report_file = run_dir / "run_report.json"
    run_report_file_ready = run_report_file.is_file()
    final_report_ready = (
        isinstance(run_report, dict) and run_report.get("status") == "executed"
    )
    csv_ready = raw_csv.is_file() and normalized_csv.is_file()

    graph_paths = {
        "merged_network_raw_gexf": run_dir / "merged_network_raw.gexf",
        "merged_network_raw_graphml": run_dir / "merged_network_raw.graphml",
        "merged_network_normalized_gexf": run_dir / "merged_network_normalized.gexf",
        "merged_network_normalized_graphml": run_dir
        / "merged_network_normalized.graphml",
        "merged_network_normalized_cytoscape_script": run_dir
        / "merged_network_normalized_cytoscape.py",
    }
    graph_exports_ready = bool(csv_ready) and all(
        path.is_file() for path in graph_paths.values()
    )

    tools_failed = 0
    if isinstance(run_report, dict):
        execution = run_report.get("execution", {})
        if isinstance(execution, dict):
            try:
                tools_failed = int(execution.get("tools_failed", 0) or 0)
            except (TypeError, ValueError):
                tools_failed = 0
        tools = run_report.get("tools", {})
        if tools_failed == 0 and isinstance(tools, dict):
            failed = tools.get("failed", {})
            if isinstance(failed, dict):
                tools_failed = len(failed)

    state_status = (
        str(execution_state.get("status", "")).strip()
        if isinstance(execution_state, dict)
        else ""
    )
    partial = bool(tools_failed > 0 or state_status == "completed_with_failures")
    finalizing_artifacts = bool(csv_ready and not final_report_ready)
    explorer_available = bool(csv_ready or final_report_ready)

    if finalizing_artifacts:
        message = (
            "Merged CSV outputs are available. ANDREA is still finalizing graph "
            "artifacts and the final run report."
        )
    elif final_report_ready and partial and csv_ready:
        message = "Partial merged results are available; one or more runs failed."
    elif final_report_ready and partial:
        message = (
            "The final run report is available, but no merged CSV outputs were "
            "produced."
        )
    elif final_report_ready:
        message = "Execution outputs are available."
    elif csv_ready:
        message = "Merged CSV outputs are available."
    else:
        phase = (
            str(execution_state.get("phase", "")).strip()
            if isinstance(execution_state, dict)
            else ""
        )
        if phase in {"exporting_artifacts", "writing_report"}:
            message = "ANDREA is finalizing output artifacts."
        else:
            message = (
                "Results Explorer will be available after merged outputs are "
                "written."
            )

    paths = {
        "merged_network_raw": str(raw_csv) if raw_csv.is_file() else None,
        "merged_network_normalized": (
            str(normalized_csv) if normalized_csv.is_file() else None
        ),
        "run_report": str(run_report_file) if run_report_file_ready else None,
        **{
            key: str(path) if path.is_file() else None
            for key, path in graph_paths.items()
        },
    }

    return {
        "explorer_available": explorer_available,
        "csv_ready": csv_ready,
        "raw_csv_ready": raw_csv.is_file(),
        "normalized_csv_ready": normalized_csv.is_file(),
        "run_report_file_ready": run_report_file_ready,
        "final_report_ready": final_report_ready,
        "graph_exports_ready": graph_exports_ready,
        "partial": partial,
        "failed_runs": tools_failed,
        "finalizing_artifacts": finalizing_artifacts,
        "message": message,
        "paths": paths,
    }


def _resolve_bundle(*, run_dir: Optional[Path], bundle_id: str) -> Any:
    if run_dir is None or not run_dir.exists() or not run_dir.is_dir():
        raise ValueError("Inference output is not ready")
    return infer_network_bundles.resolve_bundle(bundle_id=bundle_id, run_dir=run_dir)


def _resolve_available_output_files(*, run_dir: Optional[Path]) -> BundleResolution:
    if run_dir is None or not run_dir.exists() or not run_dir.is_dir():
        raise ValueError("Inference output is not ready")
    root = run_dir.resolve()
    sources = all_files(root)
    spec = BundleSpec(
        id="available_outputs",
        label="Available Output Files",
        purpose=(
            "Live view of files currently present in the inference output "
            "directory. This is for exploration only; download bundles keep "
            "their own strict readiness rules."
        ),
        contents_summary=(
            "All currently generated files under the run directory, preserving "
            "their relative folder layout.",
        ),
    )
    return BundleResolution(
        spec=spec,
        root=root,
        sources=sources,
        missing_required=() if sources else ("no output files are available yet",),
    )


def _resolve_files_bundle(*, run_dir: Optional[Path], bundle_id: str) -> Any:
    if bundle_id == "available_outputs":
        return _resolve_available_output_files(run_dir=run_dir)
    return _resolve_bundle(run_dir=run_dir, bundle_id=bundle_id)


def _infer_bundle_readiness(
    *, bundle_id: str, output_readiness: dict[str, Any]
) -> list[dict[str, str]]:
    csv_status = "ready" if output_readiness.get("csv_ready") else "pending"
    report_file_status = (
        "ready" if output_readiness.get("run_report_file_ready") else "pending"
    )
    report_status = (
        "ready" if output_readiness.get("final_report_ready") else "pending"
    )
    graphs_status = (
        "ready" if output_readiness.get("graph_exports_ready") else "pending"
    )
    if bundle_id == "full":
        return [
            {"label": "Merged CSVs", "status": csv_status},
            {"label": "Run report", "status": report_status},
            {"label": "Graph exports", "status": graphs_status},
        ]
    if bundle_id == "analysis":
        return [
            {"label": "Merged CSVs", "status": csv_status},
            {"label": "Run report snapshot", "status": report_file_status},
        ]
    if bundle_id == "report":
        return [{"label": "Run report", "status": report_status}]
    if bundle_id == "graphs":
        return [{"label": "Graph exports", "status": graphs_status}]
    return []


def _infer_bundle_runtime_missing(
    *, bundle_id: str, output_readiness: dict[str, Any]
) -> list[str]:
    missing: list[str] = []
    report_ready = bool(output_readiness.get("final_report_ready"))
    report_file_ready = bool(output_readiness.get("run_report_file_ready"))
    csv_ready = bool(output_readiness.get("csv_ready"))
    graphs_ready = bool(output_readiness.get("graph_exports_ready"))
    if bundle_id in {"full", "report"} and not report_ready:
        missing.append("run_report.json final report is not complete")
    if bundle_id == "analysis" and not report_file_ready:
        missing.append("run_report.json is not available")
    if bundle_id in {"full", "analysis"} and not csv_ready:
        missing.append("merged CSV outputs are not complete")
    if bundle_id == "full" and csv_ready and not graphs_ready:
        missing.append("graph exports are not complete")
    if bundle_id == "graphs" and not graphs_ready:
        missing.append("graph exports are not complete")
    return missing


def _apply_infer_bundle_runtime_status(
    *, bundles: list[dict[str, Any]], output_readiness: dict[str, Any]
) -> list[dict[str, Any]]:
    for bundle in bundles:
        bundle_id = str(bundle.get("id") or "")
        bundle["readiness"] = _infer_bundle_readiness(
            bundle_id=bundle_id,
            output_readiness=output_readiness,
        )
        runtime_missing = _infer_bundle_runtime_missing(
            bundle_id=bundle_id,
            output_readiness=output_readiness,
        )
        if runtime_missing:
            bundle["available"] = False
            missing = list(bundle.get("missing_required") or [])
            for reason in runtime_missing:
                if reason not in missing:
                    missing.append(reason)
            bundle["missing_required"] = missing
    return bundles


def _build_infer_bundle_metadata(
    *, run_dir: Optional[Path], output_readiness: dict[str, Any]
) -> list[dict[str, Any]]:
    output_ready = bool(run_dir and run_dir.exists() and run_dir.is_dir())
    resolver = (
        (
            lambda bundle_id: infer_network_bundles.resolve_bundle(
                bundle_id=bundle_id,
                run_dir=run_dir,  # type: ignore[arg-type]
            )
        )
        if output_ready
        else None
    )
    bundles = build_bundle_metadata(
        specs=infer_network_bundles.bundle_specs(),
        resolver=resolver,
        unavailable_reason="Inference output is not ready",
    )
    return _apply_infer_bundle_runtime_status(
        bundles=bundles,
        output_readiness=output_readiness,
    )


def _job_bundle_status(job: GuiJob) -> dict[str, dict[str, Any]]:
    run_dir = Path(job.run_dir) if job.run_dir else None
    run_report = read_json_if_exists(job.run_report_path or "")
    if run_report is None and run_dir is not None:
        run_report = read_json_if_exists(run_dir / "run_report.json")
    execution_state = _read_execution_state_payload(run_dir=run_dir)
    output_readiness = _collect_output_readiness(
        run_dir=run_dir,
        run_report=run_report,
        execution_state=execution_state,
    )
    bundles = _build_infer_bundle_metadata(
        run_dir=run_dir,
        output_readiness=output_readiness,
    )
    return bundle_status_payload(bundles)


def _require_bundle_available(resolution: Any) -> None:
    if resolution.available and resolution.sources:
        return
    missing = ", ".join(resolution.missing_required) or "no files"
    raise ValueError(
        f"Bundle '{resolution.spec.id}' is not available; missing required files: {missing}"
    )


def _viewer_for_virtual_path(path: str) -> str:
    normalized = path.lower()
    basename = Path(normalized).name
    if normalized == "plan.json":
        return "plan"
    if normalized.endswith("_cytoscape.py") or ".cytoscape." in basename:
        return "network_cytoscape_script"
    if normalized.endswith(".gexf"):
        return "network_gexf"
    if normalized.endswith(".graphml"):
        return "network_graphml"
    if normalized.endswith(".json"):
        return "json"
    if normalized.endswith(".csv"):
        return "table_csv"
    if normalized.endswith(".tsv"):
        return "table_tsv"
    if (
        normalized.endswith(".txt")
        or normalized.endswith(".log")
        or basename.endswith(".out")
        or basename.endswith(".err")
        or basename.endswith(".stderr")
        or basename.endswith(".stdout")
        or "log" in basename
    ):
        return "text"
    return "none"


def _artifact_guide(path: str) -> Optional[dict[str, Any]]:
    normalized = path.lower()
    basename = Path(normalized).name
    if basename == "merged_network_normalized.csv":
        return {
            "title": "Normalized merged network",
            "summary": (
                "Merged inferred network after ANDREA score normalization. This is "
                "the canonical handoff for evaluate-inference and compare-networks."
            ),
            "badges": ["analysis handoff", "normalized scores"],
            "tips": [
                "Use this file for downstream evaluation and network comparison.",
                "Rows keep the unified schema: source, target, score, sign, evidence, context and tool_id.",
                "The context column identifies global, group or cell-specific inferred networks.",
            ],
        }
    if basename == "merged_network_raw.csv":
        return {
            "title": "Raw merged network",
            "summary": (
                "Merged inferred network before ANDREA score normalization. It preserves "
                "wrapper-exported score magnitudes in the same public edge-table schema."
            ),
            "badges": ["debug", "raw scores"],
            "tips": [
                "Use this file for debugging wrapper outputs or method score scales.",
                "Prefer merged_network_normalized.csv for benchmark evaluation and comparison.",
            ],
        }
    if basename == "network.csv":
        return {
            "title": "Tool network output",
            "summary": (
                "Raw public network emitted by one physical wrapper execution before "
                "ANDREA merges it into the run-level outputs."
            ),
            "badges": ["tool workspace", "raw output"],
            "tips": [
                "Use this when debugging one tool run in isolation.",
                "The run-level handoff remains merged_network_normalized.csv.",
            ],
        }
    if basename in {"network.normalized.csv", "network.cell_native.csv"}:
        return {
            "title": "Tool normalized network",
            "summary": (
                "Per-tool normalized network emitted or derived inside a tool workspace "
                "before run-level merging."
            ),
            "badges": ["tool workspace", "normalized output"],
            "tips": [
                "This is useful for inspecting one wrapper execution before merge.",
                "Use merged_network_normalized.csv for downstream ANDREA commands.",
            ],
        }
    if basename == "expression.tsv":
        return {
            "title": "Runtime expression input",
            "summary": (
                "Expression matrix copy staged for a specific wrapper execution. Genes "
                "are rows and cells or samples are columns."
            ),
            "badges": ["tool workspace", "runtime input"],
            "tips": [
                "This may be a prepared copy of the dataset expression matrix.",
                "Use dataset-manifest.json to identify the original input contract.",
            ],
        }
    if basename == "run_report.json":
        return {
            "title": "Run report",
            "summary": (
                "Execution report for this infer-network job, including status, selected "
                "runs, output paths and runtime metadata."
            ),
            "badges": ["report", "reproducibility"],
            "tips": [
                "This is the compact machine-readable summary used by report bundles.",
                "During artifact finalization, this may be a snapshot until the final report is written.",
            ],
        }
    if basename == "plan.json":
        return {
            "title": "Execution plan",
            "summary": "Frozen plan used to schedule tool runs and resource waves.",
            "badges": ["planning", "resource waves"],
            "tips": [
                "Use this file to audit how ANDREA mapped selected configurations to physical executions.",
                "Grouped and aggregated modes may expand one logical run into multiple physical tasks.",
            ],
        }
    if basename == "preflight_report.json":
        return {
            "title": "Preflight report",
            "summary": "Compatibility report generated before planning the inference run.",
            "badges": ["preflight", "tool eligibility"],
            "tips": [
                "Shows eligible, warning and blocked tools for the uploaded dataset and requested inputs.",
                "Useful when a tool is absent from the generated plan.",
            ],
        }
    if basename == "dataset-manifest.json":
        return {
            "title": "Dataset manifest",
            "summary": (
                "Frozen dataset input contract used by this infer-network run. It "
                "references the expression matrix and standardized extra inputs."
            ),
            "badges": ["input contract", "dataset"],
            "tips": [
                "This is the same manifest consumed by infer-network core commands.",
                "Paths are resolved at run time; the manifest does not duplicate file contents.",
            ],
        }
    if basename == "tools_params.json":
        return {
            "title": "Tool parameters",
            "summary": "Frozen selected tool configurations and parameter overrides.",
            "badges": ["input contract", "tool selection"],
            "tips": [
                "Use this file with dataset-manifest.json to reproduce the planned inference run.",
                "The final per-run values are also written as resolved_params.json files under each tool workspace.",
            ],
        }
    if basename == "resolved_params.json":
        return {
            "title": "Resolved tool parameters",
            "summary": "Per-run parameter values after applying defaults and GUI overrides.",
            "badges": ["tool workspace", "resolved params"],
            "tips": [
                "Useful for auditing the exact parameters sent to a wrapper.",
                "This file is per physical tool execution, not a global run summary.",
            ],
        }
    if basename == "execution.json":
        return {
            "title": "Execution metadata",
            "summary": "Per-run execution contract produced by the planner.",
            "badges": ["tool workspace", "execution contract"],
            "tips": [
                "Contains the execution mode and task metadata used by the runtime.",
                "For grouped emulation, one logical configuration may correspond to multiple execution tasks.",
            ],
        }
    if basename == "resolved_execution.json":
        return {
            "title": "Resolved execution metadata",
            "summary": (
                "Planner-resolved execution metadata for a physical wrapper task."
            ),
            "badges": ["tool workspace", "execution contract"],
            "tips": [
                "Use this alongside resolved_params.json to audit the exact task sent to the runtime.",
            ],
        }
    if basename == "params.json":
        return {
            "title": "Wrapper params",
            "summary": (
                "Parameter file mounted into one wrapper container for execution."
            ),
            "badges": ["tool workspace", "runtime input"],
            "tips": [
                "This is the container-facing parameter contract, after GUI/core resolution.",
                "For a cleaner audit view, inspect resolved_params.json when available.",
            ],
        }
    if basename == "progress.json":
        return {
            "title": "Wrapper progress",
            "summary": "Progress/status file emitted by a wrapper during execution.",
            "badges": ["tool workspace", "runtime"],
            "tips": [
                "Useful when a run is still active or failed before writing a final network.",
                "The run-level progress view aggregates these files when available.",
            ],
        }
    if basename == "execution_state.json":
        return {
            "title": "Execution state",
            "summary": (
                "Run-level runtime state used by the GUI to render waves, logical runs, "
                "physical tasks and finalization progress."
            ),
            "badges": ["runtime", "GUI state"],
            "tips": [
                "This file is operational state, not a downstream analysis input.",
                "It is useful for debugging progress rendering or failed executions.",
            ],
        }
    if basename.endswith(".log"):
        return {
            "title": "Execution log",
            "summary": "Wrapper/runtime log captured during execution.",
            "badges": ["tool workspace", "log"],
            "tips": [
                "Use logs to debug tool failures, warnings or unexpected empty outputs.",
            ],
        }
    return None


def _preview_table(
    *,
    source: Path,
    delimiter: str,
    max_rows: int,
) -> dict[str, Any]:
    headers: list[str] = []
    rows: list[list[str]] = []
    total_rows = 0

    with source.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh, delimiter=delimiter)
        first = next(reader, None)
        if first is None:
            return {"headers": [], "rows": [], "total_rows": 0, "truncated": False}
        headers = [str(x) for x in first]
        for row in reader:
            total_rows += 1
            if len(rows) < max_rows:
                rows.append([str(x) for x in row])

    return {
        "headers": headers,
        "rows": rows,
        "total_rows": total_rows,
        "truncated": total_rows > len(rows),
    }


def _preview_text(source: Path, max_bytes: int) -> dict[str, Any]:
    with source.open("rb") as fh:
        raw = fh.read(max_bytes + 1)
    truncated = len(raw) > max_bytes
    if truncated:
        raw = raw[:max_bytes]
    text = raw.decode("utf-8", errors="replace")
    return {"text": text, "truncated": truncated, "size_bytes": source.stat().st_size}


def _is_probably_text(source: Path, sample_bytes: int = 4096) -> bool:
    try:
        with source.open("rb") as fh:
            raw = fh.read(sample_bytes)
    except Exception:  # noqa: BLE001
        return False
    if not raw:
        return True
    if b"\x00" in raw:
        return False
    return True

def _run_job(
    *,
    job_id: str,
    action: str,
    options: dict[str, Any],
) -> None:
    with STATE.lock:
        job = STATE.jobs[job_id]
        job.status = "running"
        job.started_at = _utc_now()
        job.finished_at = None
        job.active_action = action
        if action == "preflight":
            job.progress_percent = 15
            job.progress_label = "Running preflight"
            job.progress_detail = "Validating dataset inputs and tool compatibility."
        elif action == "plan":
            job.progress_percent = 20
            job.progress_label = "Planning execution"
            job.progress_detail = "Preparing planner inputs."
            job.planner = str(options.get("planner", "auto") or "auto")
            job.planner_time_limit_seconds = float(
                options.get("planner_time_limit_seconds", 100.0)
            )
        elif action == "run":
            job.progress_percent = 5
            job.progress_label = "Starting execution"
            job.progress_detail = "Preparing planned tool runs."

    try:
        with STATE.lock:
            job = STATE.jobs[job_id]
            dataset_manifest_path = (
                Path(job.dataset_manifest_path) if job.dataset_manifest_path else None
            )
            tools_params_path = (
                Path(job.tools_params_path) if job.tools_params_path else None
            )
            output_dir = Path(job.output_dir)
            run_dir_existing = Path(job.run_dir) if job.run_dir else None

        if action == "preflight":
            if dataset_manifest_path is None:
                raise ValueError("Job is missing dataset_manifest_path")
            preflight_report = preflight_infer_network(
                dataset_manifest_path=dataset_manifest_path,
                tools_params_path=tools_params_path,
            )
            preflight_path = Path(job.request_dir) / "preflight_report.json"
            preflight_path.write_text(
                json.dumps(preflight_report, indent=2, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )
            with STATE.lock:
                job = STATE.jobs[job_id]
                job.status = "completed"
                job.stage = "preflight_ok"
                job.preflight_report_path = str(preflight_path)
                job.active_action = None
                job.progress_percent = 100
                job.progress_label = "Preflight completed"
                job.progress_detail = "Dataset inputs and catalog compatibility were validated."
                job.finished_at = _utc_now()
            return

        if action == "plan":
            if dataset_manifest_path is None or tools_params_path is None:
                raise ValueError("Job is missing dataset/tools paths for planning")
            preflight_path = Path(job.request_dir) / "preflight_report.json"
            preflight_report = read_json_if_exists(str(preflight_path))
            with STATE.lock:
                job = STATE.jobs[job_id]
                job.progress_percent = 40
                job.progress_label = "Solving execution plan"
                job.progress_detail = (
                    "ANDREA is selecting tool resources and scheduling execution waves."
                )
            run_dir = plan_infer_network(
                dataset_manifest_path=dataset_manifest_path,
                tools_params_path=tools_params_path,
                output_dir=output_dir,
                max_cores=_safe_int(options.get("max_cores"), default=4),
                max_ram_gb=_safe_float(options.get("max_ram_gb")),
                planner=str(options.get("planner", "auto") or "auto"),
                planner_time_limit_seconds=float(
                    options.get("planner_time_limit_seconds", 100.0)
                ),
                preflight_report=preflight_report,
            )
            run_dir_resolved = run_dir.resolve()
            run_report_path = run_dir_resolved / "run_report.json"
            plan_path = run_dir_resolved / "plan.json"

            with STATE.lock:
                job = STATE.jobs[job_id]
                job.status = "completed"
                job.stage = "planned"
                job.run_dir = str(run_dir_resolved)
                job.run_report_path = (
                    str(run_report_path) if run_report_path.exists() else None
                )
                job.plan_path = str(plan_path) if plan_path.exists() else None
                job.active_action = None
                job.progress_percent = 100
                job.progress_label = "Plan generated"
                job.progress_detail = "The execution plan is ready."
                job.finished_at = _utc_now()
            return

        if action == "run":
            if run_dir_existing is None:
                raise ValueError("Job has no planned run_dir to execute")
            run_dir = run_infer_network_plan(
                run_dir=run_dir_existing,
                progress_poll_seconds=float(options.get("progress_poll_seconds", 0.5)),
            )
            run_dir_resolved = run_dir.resolve()
            run_report_path = run_dir_resolved / "run_report.json"
            plan_path = run_dir_resolved / "plan.json"
            with STATE.lock:
                job = STATE.jobs[job_id]
                job.status = "completed"
                job.stage = "executed"
                job.run_dir = str(run_dir_resolved)
                job.run_report_path = (
                    str(run_report_path) if run_report_path.exists() else None
                )
                job.plan_path = str(plan_path) if plan_path.exists() else None
                job.active_action = None
                job.progress_percent = 100
                job.progress_label = "Execution completed"
                job.progress_detail = "Inference outputs were written."
                job.finished_at = _utc_now()
            return

        raise ValueError(f"Unsupported job action: {action}")
    except Exception as exc:  # noqa: BLE001
        with STATE.lock:
            job = STATE.jobs[job_id]
            job.status = "failed"
            job.error = str(exc)
            job.traceback = traceback.format_exc(limit=30)
            job.active_action = None
            job.progress_label = "Job failed"
            job.progress_detail = str(exc)
            job.finished_at = _utc_now()


def create_app() -> FastAPI:
    app = FastAPI(title="ANDREA GUI - infer-network")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.mount(
        "/static-common", StaticFiles(directory=COMMON_STATIC_DIR), name="static-common"
    )

    bootstrap = _load_tools_bootstrap()

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "index.html",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/infer-network/bootstrap")
    async def api_bootstrap() -> JSONResponse:
        return JSONResponse(bootstrap)

    @app.get("/api/infer-network/jobs")
    async def api_jobs() -> JSONResponse:
        with STATE.lock:
            jobs = [_job_payload(job) for job in STATE.jobs.values()]
        jobs.sort(key=lambda item: item["created_at"], reverse=True)
        return JSONResponse({"jobs": jobs})

    @app.get("/api/infer-network/jobs/{job_id}")
    async def api_job(job_id: str) -> JSONResponse:
        with STATE.lock:
            job = STATE.jobs.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Job not found")
            payload = _job_payload(job)
            reproducibility = _build_reproducibility_payload(job)

        run_dir = Path(payload["run_dir"]) if payload.get("run_dir") else None
        run_report = read_json_if_exists(payload.get("run_report_path"))
        if run_report is None and run_dir is not None:
            run_report = read_json_if_exists(str(run_dir / "run_report.json"))
        preflight_report = read_json_if_exists(payload.get("preflight_report_path"))
        execution_state = _read_execution_state_payload(run_dir=run_dir)
        runtime_progress = (
            _runtime_progress_from_execution_state(execution_state)
            or _collect_runtime_progress(run_dir=run_dir)
        )
        output_readiness = _collect_output_readiness(
            run_dir=run_dir,
            run_report=run_report,
            execution_state=execution_state,
        )

        return JSONResponse(
            {
                "job": payload,
                "run_report": run_report,
                "preflight_report": preflight_report,
                "execution_state": execution_state,
                "runtime_progress": runtime_progress,
                "output_readiness": output_readiness,
                "reproducibility": reproducibility,
            }
        )

    @app.get("/api/infer-network/jobs/{job_id}/plan")
    async def api_job_plan(job_id: str) -> JSONResponse:
        with STATE.lock:
            job = STATE.jobs.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Job not found")
            plan_path = job.plan_path
            status = job.status

        plan = read_json_if_exists(plan_path)
        if plan is None:
            return JSONResponse({"status": status, "plan": None})
        return JSONResponse({"status": status, "plan": plan, "plan_path": plan_path})

    @app.get("/api/infer-network/jobs/{job_id}/bundles")
    async def api_job_bundles(job_id: str) -> JSONResponse:
        with STATE.lock:
            job = STATE.jobs.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Job not found")
            run_dir = Path(job.run_dir) if job.run_dir else None
            status = job.status
            run_report_path = job.run_report_path
        output_ready = bool(run_dir and run_dir.exists() and run_dir.is_dir())
        run_report = read_json_if_exists(run_report_path)
        if run_report is None and run_dir is not None:
            run_report = read_json_if_exists(run_dir / "run_report.json")
        execution_state = _read_execution_state_payload(run_dir=run_dir)
        output_readiness = _collect_output_readiness(
            run_dir=run_dir,
            run_report=run_report,
            execution_state=execution_state,
        )
        bundles = _build_infer_bundle_metadata(
            run_dir=run_dir,
            output_readiness=output_readiness,
        )
        return JSONResponse(
            {
                "status": status,
                "output_ready": output_ready,
                "output_readiness": output_readiness,
                "bundles": bundles,
                "bundle_status": bundle_status_payload(bundles),
            }
        )

    @app.get("/api/infer-network/jobs/{job_id}/files")
    async def api_job_files(
        job_id: str,
        bundle_id: str = "report",
    ) -> JSONResponse:
        with STATE.lock:
            job = STATE.jobs.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Job not found")
            run_dir = Path(job.run_dir) if job.run_dir else None
            status = job.status

        try:
            resolution = _resolve_files_bundle(
                run_dir=run_dir,
                bundle_id=bundle_id,
            )
            _require_bundle_available(resolution)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        sources = resolution.source_tuples
        entries = build_bundle_entries(sources, viewer_for_path=_viewer_for_virtual_path)
        return JSONResponse(
            {
                "status": status,
                "bundle_id": resolution.spec.id,
                "mode": resolution.spec.id,
                "missing_required": list(resolution.missing_required),
                "skipped_optional": list(resolution.skipped_optional),
                "entries": entries,
            }
        )

    @app.get("/api/infer-network/jobs/{job_id}/file-content")
    async def api_job_file_content(
        job_id: str,
        path: str,
        bundle_id: str = "report",
        max_rows: int = MAX_TABLE_PREVIEW_ROWS,
    ) -> JSONResponse:
        requested_path = str(path or "").strip().lstrip("/")
        if not requested_path:
            raise HTTPException(status_code=400, detail="path is required")

        with STATE.lock:
            job = STATE.jobs.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Job not found")
            run_dir = Path(job.run_dir) if job.run_dir else None

        try:
            resolution = _resolve_files_bundle(run_dir=run_dir, bundle_id=bundle_id)
            _require_bundle_available(resolution)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        source = resolve_virtual_source(
            sources=resolution.source_tuples, virtual_path=requested_path
        )
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
                    "viewer": "plan",
                    "note": "Use the dedicated plan viewer panel.",
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

        if viewer in {"network_gexf", "network_graphml", "network_cytoscape_script"}:
            if viewer == "network_cytoscape_script":
                preview = _preview_text(source, MAX_TEXT_PREVIEW_BYTES)
                return JSONResponse(
                    {
                        "path": requested_path,
                        "viewer": viewer,
                        "format": "Cytoscape preset",
                        "title": "Cytoscape Desktop preset script",
                        "summary": (
                            "Helper script that imports the sibling normalized GraphML export into a running "
                            "Cytoscape Desktop instance and applies the ANDREA default style preset."
                        ),
                        "recommended_tools": ["Cytoscape Desktop", "py4cytoscape"],
                        "tips": [
                            "This artifact is specific to Cytoscape Desktop and uses the normalized GraphML export as its data source.",
                            "Default mappings: edge width by score, edge color by tool_id, edge line type by exact context.",
                            "Run it with Cytoscape Desktop open and CyREST enabled on the default localhost port.",
                            "If you prefer manual styling, you can still import the sibling GraphML file directly.",
                        ],
                        "download_hint": (
                            "Typical usage: python merged_network_normalized_cytoscape.py"
                        ),
                        "text": preview["text"],
                        "truncated": preview["truncated"],
                    }
                )

            format_label = "GEXF" if viewer == "network_gexf" else "GraphML"
            recommended_tools = (
                ["Cytoscape Desktop", "Gephi", "Python / NetworkX / igraph"]
                if viewer == "network_graphml"
                else ["Gephi"]
            )
            tip_lines = (
                [
                    "Use this export for desktop-scale network exploration rather than in-browser rendering.",
                    "This file preserves one edge per CSV row, including context and tool_id edge attributes.",
                    "This export carries graph data and edge attributes, not an application-specific visual style preset.",
                    "Gephi is the recommended first choice for interactive layout and filtering.",
                    "If you plan to use Cytoscape Desktop, prefer the GraphML export; GEXF import there depends on an app/plugin rather than the standard importer.",
                ]
                if viewer == "network_gexf"
                else [
                    "Use this export for interoperable graph workflows across desktop and scripting tools.",
                    "This file preserves one edge per CSV row, including context and tool_id edge attributes.",
                    "Use the exact context attribute for filtering or styling global, group or future context-specific edges.",
                    "This export carries graph data and edge attributes, not an application-specific visual style preset.",
                    "GraphML is the best default choice for Cytoscape Desktop and remains directly usable in Gephi.",
                    "If you want the ANDREA default Cytoscape styling automatically, use the sibling merged_network_normalized_cytoscape.py artifact.",
                ]
            )
            return JSONResponse(
                {
                    "path": requested_path,
                    "viewer": viewer,
                    "format": format_label,
                    "title": f"{format_label} network export",
                    "summary": (
                        "External visualization/export artifact generated from the merged network output."
                    ),
                    "recommended_tools": recommended_tools,
                    "tips": tip_lines,
                    "download_hint": "Use Download ZIP or open the file directly from the run directory.",
                }
            )

        if viewer == "table_csv":
            table = _preview_table(
                source=source,
                delimiter=",",
                max_rows=max(1, min(int(max_rows), MAX_TABLE_PREVIEW_ROWS)),
            )
            return JSONResponse(
                {
                    "path": requested_path,
                    "viewer": "table_csv",
                    **table,
                    "guide": guide,
                }
            )

        if viewer == "table_tsv":
            table = _preview_table(
                source=source,
                delimiter="\t",
                max_rows=max(1, min(int(max_rows), MAX_TABLE_PREVIEW_ROWS)),
            )
            return JSONResponse(
                {
                    "path": requested_path,
                    "viewer": "table_tsv",
                    **table,
                    "guide": guide,
                }
            )

        if viewer == "text":
            text_preview = _preview_text(
                source=source, max_bytes=MAX_TEXT_PREVIEW_BYTES
            )
            return JSONResponse(
                {
                    "path": requested_path,
                    "viewer": "text",
                    **text_preview,
                    "guide": guide,
                }
            )

        # Fallback for unknown extensions that are plain text-like files.
        if _is_probably_text(source):
            text_preview = _preview_text(
                source=source, max_bytes=MAX_TEXT_PREVIEW_BYTES
            )
            return JSONResponse(
                {
                    "path": requested_path,
                    "viewer": "text",
                    **text_preview,
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

    @app.get("/api/infer-network/jobs/{job_id}/bundle")
    async def api_job_bundle(
        job_id: str,
        bundle_id: str = "full",
    ) -> FileResponse:
        with STATE.lock:
            job = STATE.jobs.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Job not found")
            request_dir = Path(job.request_dir)
            run_dir = Path(job.run_dir) if job.run_dir else None
            run_report_path = job.run_report_path
        run_report = read_json_if_exists(run_report_path)
        if run_report is None and run_dir is not None:
            run_report = read_json_if_exists(run_dir / "run_report.json")
        output_readiness = _collect_output_readiness(
            run_dir=run_dir,
            run_report=run_report,
            execution_state=_read_execution_state_payload(run_dir=run_dir),
        )

        try:
            resolution = _resolve_bundle(run_dir=run_dir, bundle_id=bundle_id)
            runtime_missing = _infer_bundle_runtime_missing(
                bundle_id=bundle_id,
                output_readiness=output_readiness,
            )
            if runtime_missing:
                raise ValueError(", ".join(runtime_missing))
            _require_bundle_available(resolution)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        zip_path = request_dir / f"{job_id}_bundle_{resolution.spec.id}.zip"
        build_zip_bundle(zip_path=zip_path, sources=resolution.source_tuples)
        return FileResponse(
            zip_path,
            media_type="application/zip",
            filename=f"andrea_{job_id}_{resolution.spec.id}.zip",
        )

    @app.post("/api/infer-network/preflight")
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
                detail=(
                    "config JSON is malformed at line "
                    f"{exc.lineno}, column {exc.colno}: {exc.msg}"
                ),
            ) from exc
        if not isinstance(config, dict):
            raise HTTPException(status_code=400, detail="config must be a JSON object")

        options = config.get("options")
        if options is None:
            options = {}
        if not isinstance(options, dict):
            raise HTTPException(
                status_code=400, detail="config.options must be an object"
            )

        output_dir_raw = str(options.get("output_dir", "./inferred_networks")).strip()
        if not output_dir_raw:
            output_dir_raw = "./inferred_networks"
        output_dir = Path(output_dir_raw).resolve()

        job_id = uuid.uuid4().hex[:12]
        request_dir = (GUI_TMP_ROOT / job_id).resolve()
        request_dir.mkdir(parents=True, exist_ok=True)

        try:
            dataset_manifest_path, _expression_path, options_cfg = (
                _build_dataset_manifest_file(
                    config=config,
                    form=form,
                    request_dir=request_dir,
                    bootstrap=bootstrap,
                )
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        job = GuiJob(
            job_id=job_id,
            created_at=_utc_now(),
            status="queued",
            stage="draft",
            request_dir=str(request_dir),
            output_dir=str(output_dir),
            dataset_manifest_path=str(dataset_manifest_path),
            tools_params_path=None,
            preflight_report_path=None,
        )
        with STATE.lock:
            STATE.jobs[job_id] = job

        worker = threading.Thread(
            target=_run_job,
            kwargs={
                "job_id": job_id,
                "action": "preflight",
                "options": options_cfg,
            },
            daemon=True,
        )
        worker.start()
        return JSONResponse(
            {
                "job_id": job_id,
                "status": "queued",
                "stage": "draft",
                "request_dir": str(request_dir),
                "dataset_manifest_path": str(dataset_manifest_path),
            }
        )

    @app.post("/api/infer-network/plan")
    async def api_plan(request: Request) -> JSONResponse:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=400, detail="Request body must be a JSON object"
            )
        job_id = str(payload.get("job_id", "")).strip()
        if not job_id:
            raise HTTPException(status_code=400, detail="job_id is required")
        runs_raw = payload.get("runs")
        options = payload.get("options")
        if options is None:
            options = {}
        if not isinstance(options, dict):
            raise HTTPException(status_code=400, detail="options must be an object")

        with STATE.lock:
            job = STATE.jobs.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Job not found")
            if job.status == "running":
                raise HTTPException(status_code=409, detail="Job is already running")
            if job.dataset_manifest_path is None:
                raise HTTPException(
                    status_code=400, detail="Job has no dataset manifest"
                )
            if job.stage not in {"preflight_ok", "planned", "executed"}:
                raise HTTPException(
                    status_code=400,
                    detail=f"Job is not ready for planning (stage={job.stage})",
                )
            request_dir = Path(job.request_dir)
            output_dir = Path(job.output_dir)

        try:
            tools_params_path = _write_tools_params_file(
                request_dir=request_dir,
                runs_raw=runs_raw,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        options_cfg = dict(options)
        options_cfg.setdefault("output_dir", str(output_dir))
        planner_time_limit_seconds = float(
            options_cfg.get("planner_time_limit_seconds", 100.0)
        )
        with STATE.lock:
            job = STATE.jobs[job_id]
            job.status = "queued"
            job.stage = "preflight_ok"
            job.active_action = "plan"
            job.progress_percent = 5
            job.progress_label = "Planning queued"
            job.progress_detail = "Waiting for the planner worker to start."
            job.planner = str(options_cfg.get("planner", "auto") or "auto")
            job.planner_time_limit_seconds = planner_time_limit_seconds
            job.tools_params_path = str(tools_params_path)
            job.error = None
            job.traceback = None
            job.run_dir = None
            job.plan_path = None
            job.run_report_path = None

        worker = threading.Thread(
            target=_run_job,
            kwargs={
                "job_id": job_id,
                "action": "plan",
                "options": options_cfg,
            },
            daemon=True,
        )
        worker.start()
        return JSONResponse(
            {
                "job_id": job_id,
                "status": "queued",
                "stage": "preflight_ok",
                "tools_params_path": str(tools_params_path),
            }
        )

    @app.post("/api/infer-network/run")
    async def api_run(request: Request) -> JSONResponse:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=400, detail="Request body must be a JSON object"
            )
        job_id = str(payload.get("job_id", "")).strip()
        if not job_id:
            raise HTTPException(status_code=400, detail="job_id is required")
        options = payload.get("options")
        if options is None:
            options = {}
        if not isinstance(options, dict):
            raise HTTPException(status_code=400, detail="options must be an object")

        with STATE.lock:
            job = STATE.jobs.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Job not found")
            if job.status == "running":
                raise HTTPException(status_code=409, detail="Job is already running")
            if not job.run_dir or not job.plan_path:
                raise HTTPException(
                    status_code=400, detail="No planned run found for this job"
                )
            if job.stage not in {"planned", "executed"}:
                raise HTTPException(
                    status_code=400,
                    detail=f"Job is not in planned state (stage={job.stage})",
                )
            job.status = "queued"
            job.error = None
            job.traceback = None

        worker = threading.Thread(
            target=_run_job,
            kwargs={
                "job_id": job_id,
                "action": "run",
                "options": options,
            },
            daemon=True,
        )
        worker.start()
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
