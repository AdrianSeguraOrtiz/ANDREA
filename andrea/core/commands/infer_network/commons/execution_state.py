"""Incremental infer-network execution state contract.

The execution state is a runtime-only JSON document written under
``runtime/execution_state.json`` while ``infer-network`` runs. It is intentionally
separate from the final ``run_report.json``:

* ``execution_state.json`` is safe for GUI polling during a running job.
* ``run_report.json`` remains the consolidated final report.

Schema version stays at ``1.0`` while ANDREA is still pre-release.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from andrea.core.shared.json_io import load_json_object

from .shared import PlanWave

EXECUTION_STATE_SCHEMA_VERSION = "1.0"
EXECUTION_STATE_RELATIVE_PATH = Path("runtime") / "execution_state.json"

TOP_LEVEL_STATUSES = {
    "queued",
    "running",
    "completed",
    "completed_with_failures",
    "failed",
}

TOOL_STATUSES = {
    "queued",
    "running",
    "completed",
    "completed_with_warnings",
    "failed",
}

EXECUTION_PHASES = {
    "planned",
    "verifying_inputs",
    "preparing_runtime",
    "running_tools",
    "collecting_results",
    "finalizing_grouped",
    "finalizing_group_aggregated",
    "merging_raw_networks",
    "normalizing_scores",
    "exporting_artifacts",
    "writing_report",
    "completed",
    "completed_with_failures",
    "failed",
}

FINAL_TOOL_STATUSES = {"completed", "completed_with_warnings", "failed"}
ISSUE_PREFIX_RE = re.compile(r"^\[([^\]]+)\]\s*(.*)$")


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def execution_state_path(run_dir: Path) -> Path:
    return Path(run_dir) / EXECUTION_STATE_RELATIVE_PATH


def build_initial_execution_state(
    *,
    run_id: str,
    waves: list[PlanWave],
    logical_runs: Optional[dict[str, dict[str, Any]]] = None,
    message: str = "Execution is queued.",
) -> dict[str, Any]:
    """Build the initial execution state from the already-planned waves."""

    wave_entries: list[dict[str, Any]] = []
    tool_entries: dict[str, dict[str, Any]] = {}
    logical_entries: dict[str, dict[str, Any]] = {}

    for wave in waves:
        task_ids: list[str] = []
        for task in wave.tasks:
            task_id = str(task.tool_id)
            task_ids.append(task_id)
            tool_entries[task_id] = {
                "tool_id": task_id,
                "run_id": str(task.run_id),
                "status": "queued",
                "phase": "planned",
                "percent": 0,
                "message": "Queued",
                "wave": int(wave.index),
                "threads": int(task.threads),
                "ram_gb": round(float(task.ram_gb), 3),
                "eta_seconds": round(float(task.eta_seconds), 3),
                "errors": [],
                "warnings": [],
            }

        wave_entries.append(
            {
                "index": int(wave.index),
                "status": "queued",
                "percent": 0,
                "threads_used": int(wave.threads_used),
                "ram_gb_used": round(float(wave.ram_gb_used), 3),
                "eta_seconds": round(float(wave.eta_seconds), 3),
                "tools": task_ids,
            }
        )

    if logical_runs:
        for run_id_key, logical in logical_runs.items():
            physical_tasks = logical.get("physical_tasks", [])
            physical_ids = [
                str(item.get("task_id", "")).strip()
                for item in physical_tasks
                if isinstance(item, dict) and str(item.get("task_id", "")).strip()
            ]
            wave_indices = sorted(
                {
                    int(tool_entries[task_id]["wave"])
                    for task_id in physical_ids
                    if task_id in tool_entries
                }
            )
            execution = logical.get("execution", {})
            execution_mode = (
                str(execution.get("mode", "")).strip()
                if isinstance(execution, dict)
                else ""
            )
            logical_entries[str(run_id_key)] = {
                "run_id": str(run_id_key),
                "tool_id": str(logical.get("tool_id", "")),
                "execution_mode": execution_mode,
                "status": "queued",
                "phase": "planned",
                "percent": 0,
                "message": "Queued",
                "waves": wave_indices,
                "physical_tasks": physical_ids,
                "errors": [],
                "warnings": [],
            }

    summary_entries = (
        list(logical_entries.values()) if logical_entries else list(tool_entries.values())
    )
    now = utc_timestamp()
    payload = {
        "schema_version": EXECUTION_STATE_SCHEMA_VERSION,
        "run_id": str(run_id),
        "status": "queued",
        "phase": "planned",
        "percent": 0,
        "message": str(message),
        "current_wave": None,
        "waves_total": len(wave_entries),
        "summary": {
            "total": len(summary_entries),
            "queued": len(summary_entries),
            "running": 0,
            "completed": 0,
            "failed": 0,
            "warnings": 0,
        },
        "waves": wave_entries,
        "tools": tool_entries,
        "logical_runs": logical_entries,
        "phase_history": [
            {
                "status": "queued",
                "phase": "planned",
                "percent": 0,
                "message": str(message),
                "current_wave": None,
                "updated_at": now,
            }
        ],
        "updated_at": now,
    }
    validate_execution_state(payload)
    return payload


def validate_execution_state(payload: dict[str, Any]) -> None:
    """Validate the strict runtime-state shape used by GUI polling."""

    if not isinstance(payload, dict):
        raise ValueError("execution_state must be a JSON object")
    if payload.get("schema_version") != EXECUTION_STATE_SCHEMA_VERSION:
        raise ValueError(
            "execution_state.schema_version must be "
            f"{EXECUTION_STATE_SCHEMA_VERSION!r}"
        )
    _require_non_empty_string(payload, "run_id")
    _require_enum(payload, "status", TOP_LEVEL_STATUSES)
    _require_enum(payload, "phase", EXECUTION_PHASES)
    _require_percent(payload, "percent")
    _require_string(payload, "message")

    current_wave = payload.get("current_wave")
    if current_wave is not None and not isinstance(current_wave, int):
        raise ValueError("execution_state.current_wave must be an integer or null")
    waves_total = payload.get("waves_total")
    if not isinstance(waves_total, int) or waves_total < 0:
        raise ValueError("execution_state.waves_total must be a non-negative integer")

    summary = payload.get("summary")
    if not isinstance(summary, dict):
        raise ValueError("execution_state.summary must be an object")
    for key in ("total", "queued", "running", "completed", "failed", "warnings"):
        value = summary.get(key)
        if not isinstance(value, int) or value < 0:
            raise ValueError(
                f"execution_state.summary.{key} must be a non-negative integer"
            )

    waves = payload.get("waves")
    if not isinstance(waves, list):
        raise ValueError("execution_state.waves must be a list")
    for idx, wave in enumerate(waves):
        _validate_wave(wave, idx=idx)

    tools = payload.get("tools")
    if not isinstance(tools, dict):
        raise ValueError("execution_state.tools must be an object")
    for tool_id, tool in tools.items():
        if not isinstance(tool_id, str) or not tool_id.strip():
            raise ValueError("execution_state.tools keys must be non-empty strings")
        _validate_tool(tool, expected_tool_id=tool_id)

    logical_runs = payload.get("logical_runs", {})
    if not isinstance(logical_runs, dict):
        raise ValueError("execution_state.logical_runs must be an object")
    for run_id, logical in logical_runs.items():
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError(
                "execution_state.logical_runs keys must be non-empty strings"
            )
        _validate_logical_run(logical, expected_run_id=run_id)

    phase_history = payload.get("phase_history")
    if not isinstance(phase_history, list):
        raise ValueError("execution_state.phase_history must be a list")
    for idx, event in enumerate(phase_history):
        _validate_phase_history_event(event, idx=idx)

    _require_non_empty_string(payload, "updated_at")


def write_execution_state(path: Path, payload: dict[str, Any]) -> None:
    """Write execution state atomically in the target directory."""

    validate_execution_state(payload)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def read_execution_state(path: Path) -> dict[str, Any]:
    payload = load_json_object(Path(path), "execution_state")
    validate_execution_state(payload)
    return payload


def read_execution_state_if_exists(path: Path) -> Optional[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return None
    return read_execution_state(path)


def _validate_wave(wave: Any, *, idx: int) -> None:
    if not isinstance(wave, dict):
        raise ValueError(f"execution_state.waves[{idx}] must be an object")
    index = wave.get("index")
    if not isinstance(index, int) or index < 1:
        raise ValueError(
            f"execution_state.waves[{idx}].index must be a positive integer"
        )
    _require_enum(wave, "status", TOP_LEVEL_STATUSES, prefix=f"waves[{idx}]")
    _require_percent(wave, "percent", prefix=f"waves[{idx}]")
    for key in ("threads_used",):
        value = wave.get(key)
        if not isinstance(value, int) or value < 0:
            raise ValueError(
                f"execution_state.waves[{idx}].{key} must be a non-negative integer"
            )
    for key in ("ram_gb_used", "eta_seconds"):
        value = wave.get(key)
        if not isinstance(value, (int, float)) or float(value) < 0:
            raise ValueError(
                f"execution_state.waves[{idx}].{key} must be a non-negative number"
            )
    tools = wave.get("tools")
    if not isinstance(tools, list) or not all(
        isinstance(item, str) and item.strip() for item in tools
    ):
        raise ValueError(
            f"execution_state.waves[{idx}].tools must be a list of non-empty strings"
        )


def _validate_tool(tool: Any, *, expected_tool_id: str) -> None:
    if not isinstance(tool, dict):
        raise ValueError(
            f"execution_state.tools.{expected_tool_id} must be an object"
        )
    if tool.get("tool_id") != expected_tool_id:
        raise ValueError(
            f"execution_state.tools.{expected_tool_id}.tool_id must match its key"
        )
    _require_non_empty_string(tool, "run_id", prefix=f"tools.{expected_tool_id}")
    _require_enum(tool, "status", TOOL_STATUSES, prefix=f"tools.{expected_tool_id}")
    _require_string(tool, "phase", prefix=f"tools.{expected_tool_id}")
    _require_percent(tool, "percent", prefix=f"tools.{expected_tool_id}")
    _require_string(tool, "message", prefix=f"tools.{expected_tool_id}")
    wave = tool.get("wave")
    if not isinstance(wave, int) or wave < 1:
        raise ValueError(
            f"execution_state.tools.{expected_tool_id}.wave must be a positive integer"
        )
    for key in ("threads",):
        value = tool.get(key)
        if not isinstance(value, int) or value < 0:
            raise ValueError(
                f"execution_state.tools.{expected_tool_id}.{key} must be a non-negative integer"
            )
    for key in ("ram_gb", "eta_seconds"):
        value = tool.get(key)
        if not isinstance(value, (int, float)) or float(value) < 0:
            raise ValueError(
                f"execution_state.tools.{expected_tool_id}.{key} must be a non-negative number"
            )
    for key in ("errors", "warnings"):
        value = tool.get(key)
        if not isinstance(value, list):
            raise ValueError(
                f"execution_state.tools.{expected_tool_id}.{key} must be a list"
            )


def _validate_logical_run(logical: Any, *, expected_run_id: str) -> None:
    if not isinstance(logical, dict):
        raise ValueError(
            f"execution_state.logical_runs.{expected_run_id} must be an object"
        )
    if logical.get("run_id") != expected_run_id:
        raise ValueError(
            f"execution_state.logical_runs.{expected_run_id}.run_id must match its key"
        )
    _require_non_empty_string(
        logical, "tool_id", prefix=f"logical_runs.{expected_run_id}"
    )
    _require_string(
        logical, "execution_mode", prefix=f"logical_runs.{expected_run_id}"
    )
    _require_enum(
        logical, "status", TOOL_STATUSES, prefix=f"logical_runs.{expected_run_id}"
    )
    _require_string(logical, "phase", prefix=f"logical_runs.{expected_run_id}")
    _require_percent(logical, "percent", prefix=f"logical_runs.{expected_run_id}")
    _require_string(logical, "message", prefix=f"logical_runs.{expected_run_id}")
    for key in ("waves", "physical_tasks", "errors", "warnings"):
        value = logical.get(key)
        if not isinstance(value, list):
            raise ValueError(
                f"execution_state.logical_runs.{expected_run_id}.{key} must be a list"
            )


def _validate_phase_history_event(event: Any, *, idx: int) -> None:
    if not isinstance(event, dict):
        raise ValueError(f"execution_state.phase_history[{idx}] must be an object")
    _require_enum(event, "status", TOP_LEVEL_STATUSES, prefix=f"phase_history[{idx}]")
    _require_enum(event, "phase", EXECUTION_PHASES, prefix=f"phase_history[{idx}]")
    _require_percent(event, "percent", prefix=f"phase_history[{idx}]")
    _require_string(event, "message", prefix=f"phase_history[{idx}]")
    current_wave = event.get("current_wave")
    if current_wave is not None and not isinstance(current_wave, int):
        raise ValueError(
            f"execution_state.phase_history[{idx}].current_wave must be an integer or null"
        )
    _require_non_empty_string(event, "updated_at", prefix=f"phase_history[{idx}]")


class ExecutionStateWriter:
    """Mutable helper that keeps the persisted execution state coherent."""

    def __init__(self, *, path: Path, payload: dict[str, Any]) -> None:
        self.path = Path(path)
        self.payload = payload
        write_execution_state(self.path, self.payload)

    @classmethod
    def initialize(
        cls,
        *,
        run_dir: Path,
        run_id: str,
        waves: list[PlanWave],
        logical_runs: Optional[dict[str, dict[str, Any]]] = None,
        message: str = "Execution is queued.",
    ) -> "ExecutionStateWriter":
        payload = build_initial_execution_state(
            run_id=run_id,
            waves=waves,
            logical_runs=logical_runs,
            message=message,
        )
        return cls(path=execution_state_path(run_dir), payload=payload)

    def update_global(
        self,
        *,
        status: Optional[str] = None,
        phase: Optional[str] = None,
        percent: Optional[int] = None,
        message: Optional[str] = None,
        current_wave: Optional[int] = None,
    ) -> None:
        if status is not None:
            self.payload["status"] = status
        if phase is not None:
            self.payload["phase"] = phase
        if percent is not None:
            self.payload["percent"] = max(0, min(100, int(percent)))
        if message is not None:
            self.payload["message"] = str(message)
        self.payload["current_wave"] = current_wave
        self._append_phase_event()
        self._persist()

    def start_wave(self, wave_index: int) -> None:
        wave = self._wave_by_index(wave_index)
        waves_total = max(1, int(self.payload.get("waves_total", 0) or 0))
        wave["status"] = "running"
        wave["percent"] = max(1, int(wave.get("percent", 0) or 0))
        for tool_id in wave["tools"]:
            tool = self.payload["tools"].get(tool_id)
            if isinstance(tool, dict) and tool.get("status") == "queued":
                tool["status"] = "running"
                tool["phase"] = "queued"
                tool["percent"] = max(1, int(tool.get("percent", 0) or 0))
                tool["message"] = "Queued in running wave"
        self.payload["status"] = "running"
        self.payload["phase"] = "running_tools"
        self.payload["percent"] = max(
            int(self.payload.get("percent", 0) or 0),
            int(round(5 + ((int(wave_index) - 1) / waves_total) * 65)),
        )
        self.payload["current_wave"] = int(wave_index)
        self.payload["message"] = (
            f"Running wave {wave_index} of {self.payload.get('waves_total', 0)}."
        )
        self._sync_logical_runs()
        self._recompute_summary()
        self._append_phase_event()
        self._persist()

    def complete_wave(self, wave_index: int) -> None:
        wave = self._wave_by_index(wave_index)
        waves_total = max(1, int(self.payload.get("waves_total", 0) or 0))
        statuses = [
            str(self.payload["tools"].get(tool_id, {}).get("status", "queued"))
            for tool_id in wave["tools"]
        ]
        wave["percent"] = 100
        if statuses and all(status == "failed" for status in statuses):
            wave["status"] = "failed"
        elif any(status == "failed" for status in statuses):
            wave["status"] = "completed_with_failures"
        else:
            wave["status"] = "completed"
        self.payload["current_wave"] = None
        self.payload["percent"] = max(
            int(self.payload.get("percent", 0) or 0),
            int(round(5 + (int(wave_index) / waves_total) * 65)),
        )
        self.payload["phase"] = "running_tools"
        self.payload["message"] = (
            f"Completed wave {wave_index} of {self.payload.get('waves_total', 0)}."
        )
        self._sync_logical_runs()
        self._recompute_summary()
        self._append_phase_event()
        self._persist()

    def update_tool(
        self,
        tool_id: str,
        *,
        status: Optional[str] = None,
        phase: Optional[str] = None,
        percent: Optional[int] = None,
        message: Optional[str] = None,
        error: Optional[str] = None,
        warning: Optional[str] = None,
    ) -> None:
        tool = self._tool(tool_id)
        if status is not None:
            tool["status"] = status
        if phase is not None:
            tool["phase"] = str(phase)
        if percent is not None:
            tool["percent"] = max(0, min(100, int(percent)))
        if message is not None:
            tool["message"] = str(message)
        if error:
            errors = tool.setdefault("errors", [])
            if error not in errors:
                errors.append(str(error))
        if warning:
            warnings = tool.setdefault("warnings", [])
            if warning not in warnings:
                warnings.append(str(warning))
        if tool.get("status") == "completed" and tool.get("warnings"):
            tool["status"] = "completed_with_warnings"
        self._sync_wave_for_tool(tool_id)
        self._sync_logical_runs()
        self._recompute_summary()
        self._persist()

    def mark_tool_result(self, result: Any) -> None:
        tool_id = str(getattr(result, "tool_id", "")).strip()
        if not tool_id or tool_id not in self.payload["tools"]:
            return
        status = str(getattr(result, "status", "failed") or "failed")
        error = getattr(result, "error", None)
        self.update_tool(
            tool_id,
            status=status if status in TOOL_STATUSES else "failed",
            phase="done" if status == "completed" else "failed",
            percent=100,
            message=(
                "Completed"
                if status == "completed"
                else str(error or "Execution failed")
            ),
            error=str(error) if error else None,
        )

    def mark_logical_result(self, result: Any) -> None:
        run_id = str(getattr(result, "tool_id", "")).strip()
        logical = self.payload.get("logical_runs", {}).get(run_id)
        if not isinstance(logical, dict):
            return
        status = str(getattr(result, "status", "failed") or "failed")
        error = getattr(result, "error", None)
        logical["status"] = status if status in TOOL_STATUSES else "failed"
        logical["phase"] = "done" if status == "completed" else "failed"
        logical["percent"] = 100
        logical["message"] = (
            "Completed" if status == "completed" else str(error or "Execution failed")
        )
        if error:
            errors = logical.setdefault("errors", [])
            if str(error) not in errors:
                errors.append(str(error))
        self._recompute_summary()
        self._persist()

    def record_warning_message(self, message: str) -> None:
        text = str(message).strip()
        if not text:
            return
        match = ISSUE_PREFIX_RE.match(text)
        if match is None:
            return
        identifier = match.group(1).strip()
        if not identifier:
            return
        if identifier in self.payload.get("tools", {}):
            self._add_tool_warning(identifier, text)
            tool = self.payload["tools"].get(identifier)
            logical_run_id = str(tool.get("run_id", "")).strip() if isinstance(tool, dict) else ""
            if logical_run_id:
                self._add_logical_warning(logical_run_id, text)
        elif identifier in self.payload.get("logical_runs", {}):
            self._add_logical_warning(identifier, text)
        else:
            return
        self._recompute_summary()
        self._persist()

    def _persist(self) -> None:
        self.payload["updated_at"] = utc_timestamp()
        write_execution_state(self.path, self.payload)

    def _append_phase_event(self) -> None:
        event = {
            "status": str(self.payload.get("status", "")),
            "phase": str(self.payload.get("phase", "")),
            "percent": int(self.payload.get("percent", 0) or 0),
            "message": str(self.payload.get("message", "")),
            "current_wave": self.payload.get("current_wave"),
            "updated_at": utc_timestamp(),
        }
        history = self.payload.setdefault("phase_history", [])
        if not isinstance(history, list):
            self.payload["phase_history"] = [event]
            return
        if history:
            previous = history[-1]
            if (
                isinstance(previous, dict)
                and previous.get("status") == event["status"]
                and previous.get("phase") == event["phase"]
                and previous.get("percent") == event["percent"]
                and previous.get("message") == event["message"]
                and previous.get("current_wave") == event["current_wave"]
            ):
                return
        history.append(event)

    def _tool(self, tool_id: str) -> dict[str, Any]:
        tool = self.payload.get("tools", {}).get(tool_id)
        if not isinstance(tool, dict):
            raise KeyError(f"Unknown execution_state tool: {tool_id}")
        return tool

    def _add_tool_warning(self, tool_id: str, warning: str) -> None:
        tool = self._tool(tool_id)
        warnings = tool.setdefault("warnings", [])
        if warning not in warnings:
            warnings.append(warning)
        if tool.get("status") == "completed":
            tool["status"] = "completed_with_warnings"

    def _add_logical_warning(self, run_id: str, warning: str) -> None:
        logical = self.payload.get("logical_runs", {}).get(run_id)
        if not isinstance(logical, dict):
            return
        warnings = logical.setdefault("warnings", [])
        if warning not in warnings:
            warnings.append(warning)
        if logical.get("status") == "completed":
            logical["status"] = "completed_with_warnings"

    def _wave_by_index(self, wave_index: int) -> dict[str, Any]:
        for wave in self.payload.get("waves", []):
            if isinstance(wave, dict) and wave.get("index") == int(wave_index):
                return wave
        raise KeyError(f"Unknown execution_state wave: {wave_index}")

    def _sync_wave_for_tool(self, tool_id: str) -> None:
        tool = self._tool(tool_id)
        wave = self._wave_by_index(int(tool["wave"]))
        statuses = [
            str(self.payload["tools"].get(candidate, {}).get("status", "queued"))
            for candidate in wave["tools"]
        ]
        percents = [
            int(self.payload["tools"].get(candidate, {}).get("percent", 0) or 0)
            for candidate in wave["tools"]
        ]
        wave["percent"] = int(round(sum(percents) / max(1, len(percents))))
        if any(status == "running" for status in statuses):
            wave["status"] = "running"
        elif statuses and all(status in FINAL_TOOL_STATUSES for status in statuses):
            if all(status == "failed" for status in statuses):
                wave["status"] = "failed"
            elif any(status == "failed" for status in statuses):
                wave["status"] = "completed_with_failures"
            else:
                wave["status"] = "completed"
        elif any(status in FINAL_TOOL_STATUSES for status in statuses):
            wave["status"] = "running"
        else:
            wave["status"] = "queued"

    def _sync_logical_runs(self) -> None:
        logical_runs = self.payload.get("logical_runs", {})
        if not isinstance(logical_runs, dict):
            return
        for logical in logical_runs.values():
            if not isinstance(logical, dict):
                continue
            physical_ids = [
                str(tool_id)
                for tool_id in logical.get("physical_tasks", [])
                if str(tool_id) in self.payload.get("tools", {})
            ]
            if not physical_ids:
                continue
            statuses = [
                str(self.payload["tools"][tool_id].get("status", "queued"))
                for tool_id in physical_ids
            ]
            percents = [
                int(self.payload["tools"][tool_id].get("percent", 0) or 0)
                for tool_id in physical_ids
            ]
            logical["percent"] = int(round(sum(percents) / max(1, len(percents))))
            if any(status == "running" for status in statuses):
                logical["status"] = "running"
                logical["phase"] = "running_tools"
                logical["message"] = "Running"
            elif statuses and all(status in FINAL_TOOL_STATUSES for status in statuses):
                if all(status == "failed" for status in statuses):
                    logical["status"] = "failed"
                    logical["phase"] = "failed"
                    logical["message"] = "Failed"
                elif any(status == "failed" for status in statuses):
                    logical["status"] = "completed_with_warnings"
                    logical["phase"] = "done"
                    logical["message"] = "Completed with failed child task(s)"
                elif any(status == "completed_with_warnings" for status in statuses) or logical.get("warnings"):
                    logical["status"] = "completed_with_warnings"
                    logical["phase"] = "done"
                    logical["message"] = "Completed with warning(s)"
                else:
                    logical["status"] = "completed"
                    logical["phase"] = "done"
                    logical["message"] = "Completed"
            elif any(status in FINAL_TOOL_STATUSES for status in statuses):
                logical["status"] = "running"
                logical["phase"] = "running_tools"
                logical["message"] = "Partially completed"
            else:
                logical["status"] = "queued"
                logical["phase"] = "planned"
                logical["message"] = "Queued"

    def _recompute_summary(self) -> None:
        logical_runs = [
            logical
            for logical in self.payload.get("logical_runs", {}).values()
            if isinstance(logical, dict)
        ]
        tools = [
            tool
            for tool in self.payload.get("tools", {}).values()
            if isinstance(tool, dict)
        ]
        entries = logical_runs or tools
        summary = {
            "total": len(entries),
            "queued": sum(1 for item in entries if item.get("status") == "queued"),
            "running": sum(1 for item in entries if item.get("status") == "running"),
            "completed": sum(
                1
                for item in entries
                if item.get("status")
                in {"completed", "completed_with_warnings"}
            ),
            "failed": sum(1 for item in entries if item.get("status") == "failed"),
            "warnings": sum(
                len(item.get("warnings", []))
                for item in entries
                if isinstance(item.get("warnings", []), list)
            ),
        }
        self.payload["summary"] = summary


def _require_enum(
    payload: dict[str, Any],
    key: str,
    allowed: set[str],
    *,
    prefix: str = "",
) -> None:
    value = payload.get(key)
    if value not in allowed:
        location = f"{prefix}.{key}" if prefix else key
        raise ValueError(
            f"execution_state.{location} must be one of {sorted(allowed)}"
        )


def _require_percent(
    payload: dict[str, Any],
    key: str,
    *,
    prefix: str = "",
) -> None:
    value = payload.get(key)
    if not isinstance(value, int) or value < 0 or value > 100:
        location = f"{prefix}.{key}" if prefix else key
        raise ValueError(f"execution_state.{location} must be an integer in [0, 100]")


def _require_string(
    payload: dict[str, Any],
    key: str,
    *,
    prefix: str = "",
) -> None:
    if not isinstance(payload.get(key), str):
        location = f"{prefix}.{key}" if prefix else key
        raise ValueError(f"execution_state.{location} must be a string")


def _require_non_empty_string(
    payload: dict[str, Any],
    key: str,
    *,
    prefix: str = "",
) -> None:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        location = f"{prefix}.{key}" if prefix else key
        raise ValueError(f"execution_state.{location} must be a non-empty string")
