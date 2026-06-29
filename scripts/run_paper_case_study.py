#!/usr/bin/env python3
"""Run a reproducible ANDREA case study for the manuscript.

The default case study focuses on group-level GRN inference from a synthetic
single-cell differentiation dataset generated with scMultiSim. It intentionally
mixes group-native inferencers, global tools executed per group, and one
cell-level method aggregated to groups.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shlex
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from andrea.core.commands.compare_networks import compare_networks
from andrea.core.commands.evaluate_inference import evaluate_inference
from andrea.core.commands.generate_data import (
    plan_generate_data_request,
    preflight_generate_data_scenario,
    run_generate_data,
)
from andrea.core.commands.infer_network import (
    plan_infer_network,
    preflight_infer_network,
    run_infer_network_plan,
)


CASE_ID = "andrea_group_grn_case_study"
DEFAULT_TOOLS = (
    "scmtni",
    "simic",
    "scregulate",
    "inferelator3",
    "kscreni",
    "genie3",
    "grnboost2",
    "clr",
)
REQUESTED_EXTRAS = [
    "groups",
    "column_phenotypes",
    "cluster_identities",
    "enrichment_background",
    "lineage_tree",
    "prior_grn",
    "prior_grn_by_group",
    "pseudotime",
    "tf_list",
]


@dataclass(frozen=True)
class CasePreset:
    genes: int
    cells: int
    base_seed: int


PRESETS = {
    "smoke": CasePreset(genes=101, cells=40, base_seed=1729),
    "paper": CasePreset(genes=150, cells=60, base_seed=1729),
}


TOOL_RUNS: dict[str, dict[str, Any]] = {
    "scmtni": {
        "run_id": "scmtni_group_native",
        "tool_id": "scmtni",
        "mode": "group_native",
        "params": {"q": 2, "indep": False, "split_genes": True},
    },
    "simic": {
        "run_id": "simic_group_native",
        "tool_id": "simic",
        "mode": "group_native",
        "params": {"random_seed": 1729},
    },
    "scregulate": {
        "run_id": "scregulate_group_native",
        "tool_id": "scregulate",
        "mode": "group_native",
        "params": {
            "prior_source": "provided_prior",
            "epochs": 2,
            "freeze_epochs": 20,
            "batch_size": 8,
            "learning_rate": 0.001,
            "early_stopping_patience": 5,
            "min_targets": 1,
            "min_TFs": 1,
            "fine_tune_epochs": 2,
            "fine_tune_batch_size": 8,
            "fine_tune_min_epochs": 1,
            "fine_tune_early_stopping_patience": 5,
            "random_state": 1729,
        },
    },
    "inferelator3": {
        "run_id": "inferelator3_group_native",
        "tool_id": "inferelator3",
        "mode": "group_native",
        "params": {
            "num_bootstraps": 2,
            "random_seed": 1729,
            "bsr_feature_num": 10,
            "prior_weight": 1.0,
            "no_prior_weight": 1.0,
            "clr_only": False,
        },
    },
    "kscreni": {
        "run_id": "kscreni_group_aggregated",
        "tool_id": "kscreni",
        "mode": "group_aggregated",
        "params": {"nfeatures": 15, "knn": 2},
    },
    "genie3": {
        "run_id": "genie3_group_emulated",
        "tool_id": "genie3",
        "mode": "group_emulated",
        "params": {
            "seed": 1729,
            "regressor_kwargs": {"n_estimators": 100, "max_features": "sqrt"},
        },
    },
    "grnboost2": {
        "run_id": "grnboost2_group_emulated",
        "tool_id": "grnboost2",
        "mode": "group_emulated",
        "params": {
            "seed": 1729,
            "regressor_kwargs": {
                "learning_rate": 0.01,
                "n_estimators": 500,
                "max_features": 0.1,
                "subsample": 0.9,
            },
        },
    },
    "clr": {
        "run_id": "clr_group_emulated",
        "tool_id": "clr",
        "mode": "group_emulated",
        "params": {"estimator": "spearman", "disc": "none", "nbins": None},
    },
    "tigress": {
        "run_id": "tigress_group_emulated",
        "tool_id": "tigress",
        "mode": "group_emulated",
        "params": {
            "seed": 1729,
            "nstepsLARS": 5,
            "nsplit": 100,
            "scoring": "area",
        },
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the reproducible ANDREA manuscript case study."
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("case_studies") / CASE_ID,
        help="Case-study output directory.",
    )
    parser.add_argument(
        "--preset",
        choices=sorted(PRESETS),
        default="paper",
        help="Dataset size preset.",
    )
    parser.add_argument(
        "--tools",
        default=",".join(DEFAULT_TOOLS),
        help=(
            "Comma-separated tool keys to run. Available keys: "
            + ", ".join(sorted(TOOL_RUNS))
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Remove an existing output directory before running.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write input JSON files and commands.sh but do not execute Docker tasks.",
    )
    parser.add_argument("--max-cores", type=int, default=8)
    parser.add_argument("--max-ram-gb", type=float, default=32.0)
    parser.add_argument("--max-parallel-tasks", type=int, default=1)
    parser.add_argument(
        "--planner",
        choices=["auto", "heuristic", "cp_sat"],
        default="auto",
        help="infer-network planner mode.",
    )
    parser.add_argument("--planner-time-limit-seconds", type=float, default=100.0)
    parser.add_argument("--progress-poll-seconds", type=float, default=0.5)
    parser.add_argument(
        "--resource-poll-seconds",
        type=float,
        default=1.0,
        help="Sampling interval for resource monitoring during executed stages.",
    )
    parser.add_argument(
        "--disable-resource-monitor",
        action="store_true",
        help="Disable process/Docker memory sampling.",
    )
    return parser.parse_args()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n")


def copy_report(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def sanitize_versioned_case_files(case_root: Path) -> None:
    """Remove machine-local absolute paths from lightweight case-study files."""
    root_texts = {
        str(case_root),
        str(case_root.resolve(strict=False)),
    }
    for path in case_root.rglob("*"):
        if not path.is_file() or "outputs" in path.parts:
            continue
        if path.suffix.lower() not in {".json", ".tsv"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        sanitized = text
        for root_text in sorted(root_texts, key=len, reverse=True):
            sanitized = sanitized.replace(root_text, "<case_root>")
        if sanitized != text:
            path.write_text(sanitized, encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def as_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, ensure_ascii=True)
    return str(value)


def write_tsv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: as_cell(row.get(column)) for column in columns})


@contextmanager
def timed_stage(
    name: str,
    rows: list[dict[str, Any]],
    *,
    resource_monitor: "ResourceMonitor | None" = None,
) -> Iterator[None]:
    started = time.perf_counter()
    status = "ok"
    if resource_monitor is not None:
        resource_monitor.start_stage(name)
    try:
        yield
    except Exception:
        status = "failed"
        raise
    finally:
        elapsed_seconds = round(time.perf_counter() - started, 3)
        if resource_monitor is not None:
            resource_monitor.finish_stage(name, status=status)
        rows.append(
            {
                "stage": name,
                "status": status,
                "elapsed_seconds": elapsed_seconds,
            }
        )


def _parse_size_to_mb(value: str) -> float | None:
    """Parse Docker size strings such as '12.3MiB' or '1.2GiB' to MB."""
    text = value.strip()
    if not text:
        return None
    number = []
    unit = []
    for char in text:
        if char.isdigit() or char in ".-":
            number.append(char)
        elif not char.isspace():
            unit.append(char)
    try:
        raw = float("".join(number))
    except ValueError:
        return None
    suffix = "".join(unit).lower()
    factors = {
        "b": 1 / 1_000_000,
        "kb": 1 / 1_000,
        "kib": 1024 / 1_000_000,
        "mb": 1,
        "mib": 1024 * 1024 / 1_000_000,
        "gb": 1000,
        "gib": 1024 * 1024 * 1024 / 1_000_000,
    }
    return raw * factors.get(suffix, 1)


def _process_rss_mb(pid: int) -> float | None:
    statm = Path("/proc") / str(pid) / "statm"
    try:
        pages = int(statm.read_text(encoding="utf-8").split()[1])
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (FileNotFoundError, IndexError, OSError, ValueError):
        return None
    return pages * page_size / 1_000_000


def _process_tree_rss_mb(root_pid: int) -> float | None:
    proc = Path("/proc")
    if not proc.exists():
        return _process_rss_mb(root_pid)
    children_by_parent: dict[int, list[int]] = {}
    rss_by_pid: dict[int, float] = {}
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            stat_text = (entry / "stat").read_text(encoding="utf-8")
            # The command name is wrapped in parentheses and may contain spaces.
            after_command = stat_text[stat_text.rfind(")") + 2 :]
            stat_parts = after_command.split()
            ppid = int(stat_parts[1])
            rss_pages = int(stat_parts[21])
            page_size = os.sysconf("SC_PAGE_SIZE")
        except (FileNotFoundError, IndexError, OSError, ValueError):
            continue
        children_by_parent.setdefault(ppid, []).append(pid)
        rss_by_pid[pid] = rss_pages * page_size / 1_000_000

    stack = [root_pid]
    seen: set[int] = set()
    total = 0.0
    found = False
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        if pid in rss_by_pid:
            total += rss_by_pid[pid]
            found = True
        stack.extend(children_by_parent.get(pid, []))
    return total if found else None


def _docker_stats() -> tuple[list[dict[str, Any]], str | None]:
    try:
        result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
            text=True,
            capture_output=True,
            check=False,
            timeout=8,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return [], str(exc)
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        return [], message or f"docker stats exited with {result.returncode}"

    rows: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        mem_usage = str(payload.get("MemUsage", "")).split("/", 1)[0].strip()
        mem_mb = _parse_size_to_mb(mem_usage)
        rows.append(
            {
                "container_id": payload.get("ID"),
                "container_name": payload.get("Name"),
                "image": payload.get("Image"),
                "docker_mem_mb": round(mem_mb, 3) if mem_mb is not None else None,
                "docker_cpu_percent": payload.get("CPUPerc"),
            }
        )
    return rows, None


def _docker_container_context(container_id: str) -> dict[str, str]:
    """Infer the ANDREA task directory mounted into a running container."""
    empty = {"mount_source": "", "mount_destination": "", "task_id": "", "image": ""}
    if not container_id:
        return empty
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{json .}}", container_id],
            text=True,
            capture_output=True,
            check=False,
            timeout=8,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return empty
    if result.returncode != 0:
        return empty
    try:
        payload = json.loads((result.stdout or "").strip() or "{}")
    except json.JSONDecodeError:
        return empty
    if not isinstance(payload, dict):
        return empty
    config = payload.get("Config", {})
    image = ""
    if isinstance(config, dict):
        image = str(config.get("Image") or "")
    mounts = payload.get("Mounts", [])
    if not isinstance(mounts, list):
        return {**empty, "image": image}

    for mount in mounts:
        if not isinstance(mount, dict):
            continue
        destination = str(mount.get("Destination") or "")
        if destination not in {"/io", "/work/out"}:
            continue
        source = str(mount.get("Source") or "")
        task_id = ""
        try:
            source_path = Path(source)
            if destination == "/io" and source_path.name == "io":
                parts = source_path.parts
                if "tools" in parts:
                    tool_idx = parts.index("tools")
                    tool_parts = parts[tool_idx + 1 : -1]
                    if len(tool_parts) >= 3 and tool_parts[1] == "subruns":
                        task_id = f"{tool_parts[0]}__{tool_parts[2]}"
                    elif len(tool_parts) >= 2:
                        task_id = f"{tool_parts[0]}__{tool_parts[1]}"
                    elif tool_parts:
                        task_id = tool_parts[0]
                if not task_id:
                    task_id = source_path.parent.name
            elif destination == "/work/out":
                task_id = source_path.name
        except (OSError, ValueError):
            task_id = ""
        return {
            "mount_source": source,
            "mount_destination": destination,
            "task_id": task_id,
            "image": image,
        }
    return {**empty, "image": image}


def _path_under_any(path: str, roots: tuple[Path, ...]) -> bool:
    if not path:
        return False
    try:
        resolved = Path(path).resolve(strict=False)
    except (OSError, ValueError):
        return False
    for root in roots:
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        return True
    return False


class ResourceMonitor:
    """Background sampler for host-process and Docker memory during stages."""

    def __init__(
        self,
        *,
        poll_seconds: float,
        root_pid: int | None = None,
        docker_mount_roots: tuple[Path, ...] = (),
    ) -> None:
        self.poll_seconds = max(0.2, float(poll_seconds))
        self.root_pid = root_pid if root_pid is not None else os.getpid()
        self.docker_mount_roots = tuple(
            root.resolve(strict=False) for root in docker_mount_roots
        )
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._active_stage: str | None = None
        self._stage_started_at: float | None = None
        self._stage_token = 0
        self.samples: list[dict[str, Any]] = []
        self.container_samples: list[dict[str, Any]] = []
        self.stage_events: list[dict[str, Any]] = []
        self._container_context_cache: dict[str, dict[str, str]] = {}

    def __enter__(self) -> "ResourceMonitor":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="case-study-resources", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=max(2.0, self.poll_seconds * 2))

    def start_stage(self, stage: str) -> None:
        now = time.perf_counter()
        with self._lock:
            self._stage_token += 1
            self._active_stage = stage
            self._stage_started_at = now
            self.stage_events.append(
                {
                    "stage": stage,
                    "event": "start",
                    "token": self._stage_token,
                    "monotonic_seconds": round(now, 6),
                }
            )

    def finish_stage(self, stage: str, *, status: str) -> None:
        self._sample_once()
        now = time.perf_counter()
        with self._lock:
            self.stage_events.append(
                {
                    "stage": stage,
                    "event": "finish",
                    "token": self._stage_token,
                    "status": status,
                    "monotonic_seconds": round(now, 6),
                }
            )
            if self._active_stage == stage:
                self._active_stage = None
                self._stage_started_at = None

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._sample_once()
            self._stop_event.wait(self.poll_seconds)

    def _sample_once(self) -> None:
        with self._lock:
            stage = self._active_stage
            started_at = self._stage_started_at
            stage_token = self._stage_token
        if not stage:
            return

        now = time.perf_counter()
        docker_rows, docker_error = _docker_stats()
        enriched_docker_rows: list[dict[str, Any]] = []
        for row in docker_rows:
            container_id = str(row.get("container_id") or "")
            context = self._container_context_cache.get(container_id)
            if context is None:
                context = _docker_container_context(container_id)
                self._container_context_cache[container_id] = context
            if self.docker_mount_roots and not _path_under_any(
                context.get("mount_source", ""), self.docker_mount_roots
            ):
                continue
            context = {
                **context,
                "mount_source": self._display_mount_source(
                    str(context.get("mount_source") or "")
                ),
            }
            merged = {**row, **context}
            if not merged.get("image") and row.get("image"):
                merged["image"] = row["image"]
            enriched_docker_rows.append(merged)
        docker_total = sum(
            float(row["docker_mem_mb"])
            for row in enriched_docker_rows
            if isinstance(row.get("docker_mem_mb"), (int, float))
        )
        process_rss = _process_rss_mb(self.root_pid)
        process_tree_rss = _process_tree_rss_mb(self.root_pid)
        sample = {
            "stage": stage,
            "elapsed_in_stage_seconds": round(now - started_at, 3) if started_at else "",
            "process_rss_mb": round(process_rss, 3) if process_rss is not None else "",
            "process_tree_rss_mb": round(process_tree_rss, 3)
            if process_tree_rss is not None
            else "",
            "docker_container_count": len(enriched_docker_rows),
            "docker_total_mem_mb": round(docker_total, 3),
            "observed_total_mem_mb": round(
                (process_tree_rss or 0.0) + docker_total, 3
            ),
            "docker_error": docker_error or "",
        }
        container_samples = [
            {
                "stage": stage,
                "elapsed_in_stage_seconds": sample["elapsed_in_stage_seconds"],
                **row,
            }
            for row in enriched_docker_rows
        ]
        with self._lock:
            if self._active_stage != stage or self._stage_token != stage_token:
                return
            self.samples.append(sample)
            self.container_samples.extend(container_samples)

    def _display_mount_source(self, source: str) -> str:
        if not source:
            return ""
        try:
            source_path = Path(source).resolve(strict=False)
        except (OSError, ValueError):
            return source
        for root in self.docker_mount_roots:
            try:
                relative = source_path.relative_to(root)
            except ValueError:
                continue
            return str(Path("<case_root>") / relative)
        return source

    def stage_summary_rows(self) -> list[dict[str, Any]]:
        with self._lock:
            samples = list(self.samples)
        by_stage: dict[str, list[dict[str, Any]]] = {}
        for row in samples:
            by_stage.setdefault(str(row["stage"]), []).append(row)
        rows: list[dict[str, Any]] = []
        for stage, stage_samples in sorted(by_stage.items()):
            rows.append(
                {
                    "stage": stage,
                    "sample_count": len(stage_samples),
                    "peak_process_rss_mb": _max_numeric(stage_samples, "process_rss_mb"),
                    "peak_process_tree_rss_mb": _max_numeric(
                        stage_samples, "process_tree_rss_mb"
                    ),
                    "peak_docker_total_mem_mb": _max_numeric(
                        stage_samples, "docker_total_mem_mb"
                    ),
                    "peak_observed_total_mem_mb": _max_numeric(
                        stage_samples, "observed_total_mem_mb"
                    ),
                    "max_docker_containers": _max_numeric(
                        stage_samples, "docker_container_count"
                    ),
                    "docker_error_count": sum(
                        1 for item in stage_samples if item.get("docker_error")
                    ),
                }
            )
        return rows

    def sample_rows(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.samples)

    def container_summary_rows(self) -> list[dict[str, Any]]:
        with self._lock:
            samples = list(self.container_samples)
        by_key: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
        for row in samples:
            key = (
                str(row.get("stage") or ""),
                str(row.get("task_id") or ""),
                str(row.get("container_id") or ""),
                str(row.get("container_name") or ""),
            )
            by_key.setdefault(key, []).append(row)
        rows: list[dict[str, Any]] = []
        for (stage, task_id, container_id, container_name), items in sorted(
            by_key.items()
        ):
            image = next((item.get("image") for item in items if item.get("image")), "")
            mount_source = next(
                (item.get("mount_source") for item in items if item.get("mount_source")),
                "",
            )
            mount_destination = next(
                (
                    item.get("mount_destination")
                    for item in items
                    if item.get("mount_destination")
                ),
                "",
            )
            rows.append(
                {
                    "stage": stage,
                    "task_id": task_id,
                    "container_id": container_id,
                    "container_name": container_name,
                    "image": image,
                    "mount_source": mount_source,
                    "mount_destination": mount_destination,
                    "sample_count": len(items),
                    "peak_docker_mem_mb": _max_numeric(items, "docker_mem_mb"),
                }
            )
        return rows

    def task_summary_rows(self) -> list[dict[str, Any]]:
        with self._lock:
            samples = list(self.container_samples)
        by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in samples:
            task_id = str(row.get("task_id") or "")
            if not task_id:
                continue
            key = (str(row.get("stage") or ""), task_id)
            by_key.setdefault(key, []).append(row)

        rows: list[dict[str, Any]] = []
        for (stage, task_id), items in sorted(by_key.items()):
            containers = sorted(
                {
                    str(item.get("container_id") or "")
                    for item in items
                    if item.get("container_id")
                }
            )
            images = sorted(
                {str(item.get("image") or "") for item in items if item.get("image")}
            )
            mount_sources = sorted(
                {
                    str(item.get("mount_source") or "")
                    for item in items
                    if item.get("mount_source")
                }
            )
            rows.append(
                {
                    "stage": stage,
                    "task_id": task_id,
                    "sample_count": len(items),
                    "containers_observed": len(containers),
                    "peak_docker_mem_mb": _max_numeric(items, "docker_mem_mb"),
                    "images": images,
                    "mount_sources": mount_sources,
                }
            )
        return rows


def _max_numeric(rows: list[dict[str, Any]], key: str) -> float | int | str:
    values: list[float] = []
    for row in rows:
        value = row.get(key)
        if isinstance(value, (int, float)):
            values.append(float(value))
        elif isinstance(value, str) and value.strip():
            try:
                values.append(float(value))
            except ValueError:
                continue
    if not values:
        return ""
    value = max(values)
    return round(value, 3)


def selected_tool_configs(tool_keys: str) -> list[dict[str, Any]]:
    keys = [item.strip().lower() for item in tool_keys.split(",") if item.strip()]
    if not keys:
        raise ValueError("At least one tool must be selected")
    unknown = sorted(set(keys).difference(TOOL_RUNS))
    if unknown:
        raise ValueError(
            "Unknown tool key(s): "
            + ", ".join(unknown)
            + ". Available keys: "
            + ", ".join(sorted(TOOL_RUNS))
        )
    runs: list[dict[str, Any]] = []
    for key in keys:
        config = TOOL_RUNS[key]
        runs.append(
            {
                "run_id": config["run_id"],
                "tool_id": config["tool_id"],
                "execution": {"mode": config["mode"]},
                "params": dict(config["params"]),
            }
        )
    return runs


def build_scenario(preset: CasePreset) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "id": CASE_ID,
        "data_axes": {
            "measurement": "rna_expression",
            "resolution": "single_cell",
            "column_kind": "cells",
            "experimental_design": "differentiation",
        },
        "truth_requirements": {"contexts": ["global", "group"]},
        "organism": {"taxonomic_group": "synthetic", "ncbi_taxon_id": None},
        "requested_extras": REQUESTED_EXTRAS,
        "base_seed": preset.base_seed,
    }


def build_simulator_runs(preset: CasePreset) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "runs": [
            {
                "run_id": "scmultisim_group_dynamic",
                "simulator_id": "scmultisim",
                "replicates": 1,
                "base_seed": preset.base_seed,
                "native_outputs": [],
                "params": {
                    "num_genes": preset.genes,
                    "num_cells": preset.cells,
                    "grn_source": "builtin_100",
                    "tree_preset": "phyla3",
                    "num_cifs": 20,
                    "velocity": {"enabled": False},
                    "dynamic_grn": {
                        "enabled": True,
                        "num_steps": 3,
                        "cell_per_step": 1,
                        "num_changing_edges": 2,
                        "weight_mean": 0,
                        "weight_sd": 1,
                    },
                    "technical_noise": {"enabled": False},
                },
            }
        ],
    }


def find_single(path: Path, pattern: str) -> Path:
    matches = sorted(path.glob(pattern))
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one match for {pattern!r} under {path}, found {len(matches)}"
        )
    return matches[0]


def flatten_runtime_profile(profile: Any) -> float | None:
    if not isinstance(profile, list):
        return None
    total = 0.0
    found = False
    for item in profile:
        if not isinstance(item, dict):
            continue
        value = item.get("elapsed_seconds")
        if isinstance(value, (int, float)):
            total += float(value)
            found = True
    return round(total, 3) if found else None


def preflight_generate_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for status in ("eligible", "warning", "blocked"):
        for item in report.get(status, []):
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "command": "generate-data",
                    "item_id": item.get("simulator_id"),
                    "status": status,
                    "issue_count": len(item.get("issues", []) or []),
                    "truth_outputs": item.get("truth_outputs"),
                    "native_extras_used": item.get("native_extras_used"),
                    "derived_extras_used": item.get("derived_extras_used"),
                }
            )
    return rows


def preflight_infer_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    catalog = report.get("catalog", {})
    if isinstance(catalog, dict):
        for status in ("eligible", "warning", "blocked"):
            for item in catalog.get(status, []) or []:
                if not isinstance(item, dict):
                    continue
                rows.append(
                    {
                        "command": "infer-network",
                        "item_id": item.get("tool_id"),
                        "status": status,
                        "issue_count": len(item.get("issues", []) or []),
                        "execution_capabilities": item.get("execution_capabilities"),
                    }
                )
    return rows


def selected_run_rows(
    *,
    tools_params: dict[str, Any],
    preflight: dict[str, Any],
    run_report: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    runs_by_id = {
        str(run["run_id"]): run
        for run in tools_params.get("runs", [])
        if isinstance(run, dict) and run.get("run_id")
    }
    preflight_runs = preflight.get("runs", {})
    catalog_ids = preflight_runs.get("catalog_tool_ids", {})
    issues = preflight_runs.get("issues", {})
    resolved_execution = preflight_runs.get("resolved_execution", {})
    status_by_tool: dict[str, Any] = {}
    per_tool_rows: dict[str, Any] = {}
    if run_report:
        tools = run_report.get("tools", {})
        if isinstance(tools, dict):
            status_by_tool = tools.get("status_by_tool", {}) or {}
        outputs = run_report.get("outputs", {})
        if isinstance(outputs, dict):
            per_tool_rows = outputs.get("rows_per_tool", {}) or {}

    rows: list[dict[str, Any]] = []
    for run_id, run in sorted(runs_by_id.items()):
        execution = run.get("execution", {})
        rows.append(
            {
                "run_id": run_id,
                "tool_id": run.get("tool_id"),
                "catalog_tool_id": catalog_ids.get(run_id),
                "requested_mode": execution.get("mode") if isinstance(execution, dict) else None,
                "resolved_execution": resolved_execution.get(run_id),
                "issue_count": len(issues.get(run_id, []) or []),
                "runtime_status": status_by_tool.get(run_id),
                "merged_rows": per_tool_rows.get(run_id),
                "params": run.get("params"),
            }
        )
    return rows


def evaluation_summary_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = [item for item in report.get("metrics", []) if isinstance(item, dict)]
    rows: list[dict[str, Any]] = []
    for item in sorted(
        metrics,
        key=lambda x: (
            str(x.get("level")),
            str(x.get("context")),
            str(x.get("tool_id")),
        ),
    ):
        rows.append(
            {
                "tool_id": item.get("tool_id"),
                "catalog_tool_id": item.get("catalog_tool_id"),
                "context": item.get("context"),
                "level": item.get("level"),
                "status": item.get("status"),
                "auroc": item.get("auroc"),
                "aupr": item.get("aupr"),
                "f1_at_truth_count": item.get("f1_at_truth_count"),
                "epr_at_truth_count": item.get("epr_at_truth_count"),
                "n_truth_edges": item.get("n_truth_edges"),
                "n_predicted_edges": item.get("n_predicted_edges"),
                "reason": item.get("reason"),
            }
        )
    return rows


def comparison_summary_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    summary = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}
    outputs = report.get("outputs", {}) if isinstance(report.get("outputs"), dict) else {}
    return [
        {
            "sources": summary.get("sources"),
            "network_instances": summary.get("network_instances"),
            "edge_score_rows": summary.get("edge_score_rows"),
            "distance_rows": summary.get("distance_rows"),
            "coordinate_rows": summary.get("coordinate_rows"),
            "warnings": summary.get("warnings"),
            "runtime_profile_seconds": flatten_runtime_profile(
                report.get("runtime_profile")
            ),
            "comparison_sqlite": outputs.get("comparison_sqlite"),
            "comparison_view": outputs.get("comparison_view"),
        }
    ]


def plan_summary_rows(*, simulation_plan_path: Path, infer_run_dir: Path) -> list[dict[str, Any]]:
    simulation_plan = read_json(simulation_plan_path)
    infer_plan = read_json(infer_run_dir / "plan.json")
    simulation_execution = simulation_plan.get("execution", {})
    infer_totals = infer_plan.get("totals", {})
    infer_planner = infer_plan.get("planner", {})
    if not isinstance(simulation_execution, dict):
        simulation_execution = {}
    if not isinstance(infer_totals, dict):
        infer_totals = {}
    if not isinstance(infer_planner, dict):
        infer_planner = {}
    return [
        {
            "stage": "generate-data",
            "planned_eta_seconds": simulation_execution.get("eta_total_seconds"),
            "waves_total": len(simulation_execution.get("waves", []) or []),
            "tasks_total": len(simulation_plan.get("tasks", []) or []),
            "logical_runs_total": len(simulation_plan.get("runs", []) or []),
            "planner_requested": "",
            "planner_used": "",
            "threads_peak": "",
            "ram_peak_gb": "",
        },
        {
            "stage": "infer-network",
            "planned_eta_seconds": infer_plan.get("eta_total_seconds"),
            "waves_total": infer_totals.get("waves_total"),
            "tasks_total": infer_totals.get("tasks_total"),
            "logical_runs_total": infer_totals.get("logical_runs_total"),
            "planner_requested": infer_planner.get("requested"),
            "planner_used": infer_planner.get("used"),
            "threads_peak": infer_totals.get("threads_peak"),
            "ram_peak_gb": infer_totals.get("ram_peak_gb"),
        },
    ]


def write_commands(
    *,
    path: Path,
    case_root: Path,
    args: argparse.Namespace,
) -> None:
    try:
        case_root_display = os.path.relpath(case_root, Path.cwd())
    except ValueError:
        case_root_display = str(case_root)
    if case_root_display.startswith(".."):
        case_root_display = str(case_root)
    case_root_default = shlex.quote(case_root_display)
    text = f"""#!/usr/bin/env bash
set -euo pipefail

CASE_ROOT="${{CASE_ROOT:-{case_root_default}}}"

andrea generate-data preflight \\
  --scenario "$CASE_ROOT/inputs/scenario.json" \\
  --output-json "$CASE_ROOT/reports/generate_data_preflight.json"

andrea generate-data plan \\
  --scenario "$CASE_ROOT/inputs/scenario.json" \\
  --simulator-runs "$CASE_ROOT/inputs/simulator_runs.json" \\
  --out "$CASE_ROOT/plans/simulation-plan.json" \\
  --max-parallel-tasks {args.max_parallel_tasks} \\
  --max-cores {args.max_cores} \\
  --max-ram-gb {args.max_ram_gb}

andrea generate-data run \\
  --plan "$CASE_ROOT/plans/simulation-plan.json" \\
  --output-dir "$CASE_ROOT/outputs/01_generate_data" \\
  --max-parallel-tasks {args.max_parallel_tasks}

BENCHMARK_DIR="$(find "$CASE_ROOT/outputs/01_generate_data" -mindepth 1 -maxdepth 1 -type d | sort | tail -n1)"
DATASET_MANIFEST="$(find "$BENCHMARK_DIR/datasets" -name dataset-manifest.json | sort | head -n1)"
GROUND_TRUTH_MANIFEST="$(find "$BENCHMARK_DIR/datasets" -name ground-truth-manifest.json | sort | head -n1)"

andrea infer-network preflight \\
  --dataset-manifest "$DATASET_MANIFEST" \\
  --tools-params "$CASE_ROOT/inputs/tools_params.json" \\
  --output-json "$CASE_ROOT/reports/infer_network_preflight.json"

andrea infer-network plan \\
  --dataset-manifest "$DATASET_MANIFEST" \\
  --tools-params "$CASE_ROOT/inputs/tools_params.json" \\
  --output-dir "$CASE_ROOT/outputs/02_infer_network" \\
  --max-cores {args.max_cores} \\
  --max-ram-gb {args.max_ram_gb} \\
  --planner {args.planner} \\
  --planner-time-limit-seconds {args.planner_time_limit_seconds}

INFER_RUN_DIR="$(find "$CASE_ROOT/outputs/02_infer_network" -mindepth 1 -maxdepth 1 -type d | sort | tail -n1)"

andrea infer-network run --run-dir "$INFER_RUN_DIR"

andrea evaluate-inference \\
  --run-report "$INFER_RUN_DIR/run_report.json" \\
  --ground-truth-manifest "$GROUND_TRUTH_MANIFEST" \\
  --output-dir "$CASE_ROOT/outputs/03_evaluate_inference" \\
  --view

EVALUATION_REPORT="$(find "$CASE_ROOT/outputs/03_evaluate_inference" -name evaluation_report.json | sort | tail -n1)"
cat > "$CASE_ROOT/inputs/comparison_request.json" <<JSON
{{
  "schema_version": "1.0",
  "id": "{CASE_ID}",
  "sources": [
    {{
      "source_id": "case_study_inference",
      "label": "Case-study inferred networks",
      "run_report": "$INFER_RUN_DIR/run_report.json",
      "evaluation_report": "$EVALUATION_REPORT"
    }}
  ]
}}
JSON

andrea compare-networks \\
  --request "$CASE_ROOT/inputs/comparison_request.json" \\
  --output-dir "$CASE_ROOT/outputs/04_compare_networks"
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def main() -> int:
    args = parse_args()
    case_root = args.out.resolve()
    preset = PRESETS[args.preset]
    tools_params = {"runs": selected_tool_configs(args.tools)}

    if case_root.exists():
        if not args.force:
            raise SystemExit(
                f"Output directory already exists: {case_root}. Use --force to replace it."
            )
        shutil.rmtree(case_root)
    (case_root / "inputs").mkdir(parents=True)
    (case_root / "plans").mkdir()
    (case_root / "reports").mkdir()
    (case_root / "summaries").mkdir()
    (case_root / "outputs").mkdir()

    scenario_path = case_root / "inputs" / "scenario.json"
    simulator_runs_path = case_root / "inputs" / "simulator_runs.json"
    tools_params_path = case_root / "inputs" / "tools_params.json"
    write_json(scenario_path, build_scenario(preset))
    write_json(simulator_runs_path, build_simulator_runs(preset))
    write_json(tools_params_path, tools_params)
    write_commands(path=case_root / "commands.sh", case_root=case_root, args=args)

    stage_rows: list[dict[str, Any]] = []
    if args.dry_run:
        print(f"Wrote case-study inputs to {case_root}")
        print("Dry run requested; execution was not started.")
        return 0

    resource_monitor: ResourceMonitor | None = None
    if not args.disable_resource_monitor:
        resource_monitor = ResourceMonitor(
            poll_seconds=args.resource_poll_seconds,
            docker_mount_roots=(case_root,),
        )
        resource_monitor.start()

    generate_preflight_path = case_root / "reports" / "generate_data_preflight.json"
    simulation_plan_path = case_root / "plans" / "simulation-plan.json"
    with timed_stage(
        "generate-data preflight", stage_rows, resource_monitor=resource_monitor
    ):
        generate_preflight = preflight_generate_data_scenario(scenario_path)
        write_json(generate_preflight_path, generate_preflight)
    with timed_stage("generate-data plan", stage_rows, resource_monitor=resource_monitor):
        plan_generate_data_request(
            scenario_request_path=scenario_path,
            simulator_runs_path=simulator_runs_path,
            output_path=simulation_plan_path,
            max_parallel_tasks=args.max_parallel_tasks,
            max_cores=args.max_cores,
            max_ram_gb=args.max_ram_gb,
        )
    with timed_stage("generate-data run", stage_rows, resource_monitor=resource_monitor):
        benchmark_root = run_generate_data(
            plan_path=simulation_plan_path,
            output_dir=case_root / "outputs" / "01_generate_data",
            max_parallel_tasks=args.max_parallel_tasks,
            progress_poll_seconds=args.progress_poll_seconds,
            show_progress=True,
        )

    dataset_manifest_path = find_single(benchmark_root / "datasets", "*/dataset-manifest.json")
    ground_truth_manifest_path = find_single(
        benchmark_root / "datasets", "*/ground-truth-manifest.json"
    )

    infer_preflight_path = case_root / "reports" / "infer_network_preflight.json"
    with timed_stage(
        "infer-network preflight", stage_rows, resource_monitor=resource_monitor
    ):
        infer_preflight = preflight_infer_network(
            dataset_manifest_path=dataset_manifest_path,
            tools_params_path=tools_params_path,
        )
        write_json(infer_preflight_path, infer_preflight)
    with timed_stage("infer-network plan", stage_rows, resource_monitor=resource_monitor):
        infer_run_dir = plan_infer_network(
            dataset_manifest_path=dataset_manifest_path,
            tools_params_path=tools_params_path,
            output_dir=case_root / "outputs" / "02_infer_network",
            max_cores=args.max_cores,
            max_ram_gb=args.max_ram_gb,
            planner=args.planner,
            planner_time_limit_seconds=args.planner_time_limit_seconds,
            preflight_report=infer_preflight,
        )
    infer_plan_path = infer_run_dir / "plan.json"
    copy_report(infer_plan_path, case_root / "reports" / "infer_network_plan.json")
    with timed_stage("infer-network run", stage_rows, resource_monitor=resource_monitor):
        run_infer_network_plan(
            run_dir=infer_run_dir,
            progress_poll_seconds=args.progress_poll_seconds,
        )
    run_report_path = infer_run_dir / "run_report.json"
    copy_report(
        run_report_path,
        case_root / "reports" / "infer_network_run_report.json",
    )
    run_report = read_json(run_report_path)

    with timed_stage("evaluate-inference", stage_rows, resource_monitor=resource_monitor):
        evaluation_report = evaluate_inference(
            run_report_path=run_report_path,
            ground_truth_manifest_path=ground_truth_manifest_path,
            output_dir=case_root / "outputs" / "03_evaluate_inference",
            generate_view=True,
        )
    evaluation_report_path = (
        (case_root / "outputs" / "03_evaluate_inference")
        / str(evaluation_report["outputs"]["evaluation_report"])
    ).resolve()
    copy_report(
        evaluation_report_path,
        case_root / "reports" / "evaluation_report.json",
    )

    comparison_request_path = case_root / "inputs" / "comparison_request.json"
    write_json(
        comparison_request_path,
        {
            "schema_version": "1.0",
            "id": CASE_ID,
            "sources": [
                {
                    "source_id": "case_study_inference",
                    "label": "Case-study inferred networks",
                    "run_report": str(run_report_path),
                    "evaluation_report": str(evaluation_report_path),
                }
            ],
        },
    )
    with timed_stage("compare-networks", stage_rows, resource_monitor=resource_monitor):
        comparison_report = compare_networks(
            request_path=comparison_request_path,
            output_dir=case_root / "outputs" / "04_compare_networks",
            write_edge_scores_csv=True,
        )
    comparison_report_path = (
        (case_root / "outputs" / "04_compare_networks")
        / str(comparison_report["outputs"]["comparison_report"])
    ).resolve()
    copy_report(
        comparison_report_path,
        case_root / "reports" / "comparison_report.json",
    )

    resource_stage_rows: list[dict[str, Any]] = []
    resource_task_rows: list[dict[str, Any]] = []
    resource_container_rows: list[dict[str, Any]] = []
    resource_sample_rows: list[dict[str, Any]] = []
    if resource_monitor is not None:
        resource_monitor.stop()
        resource_stage_rows = resource_monitor.stage_summary_rows()
        resource_task_rows = resource_monitor.task_summary_rows()
        resource_container_rows = resource_monitor.container_summary_rows()
        resource_sample_rows = resource_monitor.sample_rows()

    write_tsv(
        case_root / "summaries" / "stage_timings.tsv",
        stage_rows,
        ["stage", "status", "elapsed_seconds"],
    )
    if resource_monitor is not None:
        write_tsv(
            case_root / "summaries" / "resource_usage_by_stage.tsv",
            resource_stage_rows,
            [
                "stage",
                "sample_count",
                "peak_process_rss_mb",
                "peak_process_tree_rss_mb",
                "peak_docker_total_mem_mb",
                "peak_observed_total_mem_mb",
                "max_docker_containers",
                "docker_error_count",
            ],
        )
        write_tsv(
            case_root / "summaries" / "resource_usage_by_task.tsv",
            resource_task_rows,
            [
                "stage",
                "task_id",
                "sample_count",
                "containers_observed",
                "peak_docker_mem_mb",
                "images",
                "mount_sources",
            ],
        )
        write_tsv(
            case_root / "summaries" / "resource_usage_by_container.tsv",
            resource_container_rows,
            [
                "stage",
                "task_id",
                "container_id",
                "container_name",
                "image",
                "mount_source",
                "mount_destination",
                "sample_count",
                "peak_docker_mem_mb",
            ],
        )
        write_tsv(
            case_root / "summaries" / "resource_usage_samples.tsv",
            resource_sample_rows,
            [
                "stage",
                "elapsed_in_stage_seconds",
                "process_rss_mb",
                "process_tree_rss_mb",
                "docker_container_count",
                "docker_total_mem_mb",
                "observed_total_mem_mb",
                "docker_error",
            ],
        )
    write_tsv(
        case_root / "summaries" / "plan_summary.tsv",
        plan_summary_rows(
            simulation_plan_path=simulation_plan_path,
            infer_run_dir=infer_run_dir,
        ),
        [
            "stage",
            "planned_eta_seconds",
            "waves_total",
            "tasks_total",
            "logical_runs_total",
            "planner_requested",
            "planner_used",
            "threads_peak",
            "ram_peak_gb",
        ],
    )
    write_tsv(
        case_root / "summaries" / "generate_data_preflight.tsv",
        preflight_generate_rows(generate_preflight),
        [
            "command",
            "item_id",
            "status",
            "issue_count",
            "truth_outputs",
            "native_extras_used",
            "derived_extras_used",
        ],
    )
    write_tsv(
        case_root / "summaries" / "infer_network_catalog_preflight.tsv",
        preflight_infer_rows(infer_preflight),
        ["command", "item_id", "status", "issue_count", "execution_capabilities"],
    )
    write_tsv(
        case_root / "summaries" / "selected_inference_runs.tsv",
        selected_run_rows(
            tools_params=tools_params,
            preflight=infer_preflight,
            run_report=run_report,
        ),
        [
            "run_id",
            "tool_id",
            "catalog_tool_id",
            "requested_mode",
            "resolved_execution",
            "issue_count",
            "runtime_status",
            "merged_rows",
            "params",
        ],
    )
    write_tsv(
        case_root / "summaries" / "evaluation_metrics.tsv",
        evaluation_summary_rows(evaluation_report),
        [
            "tool_id",
            "catalog_tool_id",
            "context",
            "level",
            "status",
            "auroc",
            "aupr",
            "f1_at_truth_count",
            "epr_at_truth_count",
            "n_truth_edges",
            "n_predicted_edges",
            "reason",
        ],
    )
    write_tsv(
        case_root / "summaries" / "comparison_summary.tsv",
        comparison_summary_rows(comparison_report),
        [
            "sources",
            "network_instances",
            "edge_score_rows",
            "distance_rows",
            "coordinate_rows",
            "warnings",
            "runtime_profile_seconds",
            "comparison_sqlite",
            "comparison_view",
        ],
    )

    write_json(
        case_root / "case_study_summary.json",
        {
            "case_id": CASE_ID,
            "preset": args.preset,
            "genes": preset.genes,
            "cells": preset.cells,
            "selected_tools": tools_params["runs"],
            "paths": {
                "case_root": str(case_root),
                "scenario": str(scenario_path),
                "simulator_runs": str(simulator_runs_path),
                "tools_params": str(tools_params_path),
                "benchmark_root": str(benchmark_root),
                "dataset_manifest": str(dataset_manifest_path),
                "ground_truth_manifest": str(ground_truth_manifest_path),
                "infer_run_dir": str(infer_run_dir),
                "run_report": str(run_report_path),
                "evaluation_report": str(evaluation_report_path),
                "comparison_report": str(comparison_report_path),
                "infer_network_plan_copy": str(
                    case_root / "reports" / "infer_network_plan.json"
                ),
                "infer_network_run_report_copy": str(
                    case_root / "reports" / "infer_network_run_report.json"
                ),
                "evaluation_report_copy": str(
                    case_root / "reports" / "evaluation_report.json"
                ),
                "comparison_report_copy": str(
                    case_root / "reports" / "comparison_report.json"
                ),
                "comparison_request": str(comparison_request_path),
                "resource_usage_by_stage": str(
                    case_root / "summaries" / "resource_usage_by_stage.tsv"
                )
                if resource_monitor is not None
                else None,
                "resource_usage_by_task": str(
                    case_root / "summaries" / "resource_usage_by_task.tsv"
                )
                if resource_monitor is not None
                else None,
                "resource_usage_by_container": str(
                    case_root / "summaries" / "resource_usage_by_container.tsv"
                )
                if resource_monitor is not None
                else None,
                "resource_usage_samples": str(
                    case_root / "summaries" / "resource_usage_samples.tsv"
                )
                if resource_monitor is not None
                else None,
            },
            "stage_timings": stage_rows,
            "resource_monitoring": {
                "enabled": resource_monitor is not None,
                "poll_seconds": args.resource_poll_seconds,
                "stage_summary": resource_stage_rows,
                "task_summary": resource_task_rows,
                "container_summary": resource_container_rows,
            },
            "inference_execution": run_report.get("execution"),
            "comparison_summary": comparison_report.get("summary"),
        },
    )
    sanitize_versioned_case_files(case_root)

    print(f"Case study completed: {case_root}")
    print(f"Summary: {case_root / 'case_study_summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
