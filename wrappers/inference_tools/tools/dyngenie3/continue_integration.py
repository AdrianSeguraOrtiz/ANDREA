#!/usr/bin/env python3
"""Resume the reviewed dynGENIE3 integration after an external experiment ends.

Use a dedicated PM2 process with autorestart disabled. No work runs on import.
--plan-only freezes a reviewable source/command plan without invoking Docker.
Successful measurement does not imply temporal coverage or predictive validation.
Nothing here publishes an image/package, commits, merges or changes a branch.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from typing import Any, Callable


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
COMPLETED = {"completed", "complete", "succeeded"}
POLL_SECONDS = 30
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
# These files determine the installed application and its generated entrypoint.
# Documentation/verification repairs may reuse an image only when every one of
# these subjects and the scientific ToolSpec remain exactly equal.
IMAGE_SUBJECTS = (
    "wrappers/inference_tools/tools/dyngenie3/Dockerfile",
    "wrappers/inference_tools/tools/dyngenie3/.dockerignore",
    "wrappers/inference_tools/tools/dyngenie3/requirements.lock",
    "wrappers/inference_tools/tools/dyngenie3/patch_upstream.py",
    "wrappers/inference_tools/tools/dyngenie3/run_tool.py",
    "wrappers/inference_tools/tools/dyngenie3/template_map.json",
    "wrappers/inference_tools/tools/dyngenie3/toolspec.draft.json",
    "wrappers/inference_tools/scripts/build_tool_images.py",
    "wrappers/inference_tools/scripts/templates/python/_run_tool_common.py",
)


class IntegrationError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise IntegrationError(f"Expected JSON object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def external_receipt(path: Path) -> dict[str, Any] | None:
    """Only an explicit successful terminal state permits another heavy phase."""
    try:
        raw = path.read_bytes()
        state = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(state, dict)
        or not isinstance(state.get("status"), str)
        or state["status"] not in COMPLETED
    ):
        return None
    if "active" in state and (not isinstance(state["active"], bool) or state["active"]):
        return None
    return {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "status": state["status"],
        "checked_at": utc(),
    }


class Supervisor:
    def __init__(
        self,
        *,
        root: Path,
        evidence: Path,
        external_state: Path,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.root = root.resolve()
        self.evidence = evidence.resolve()
        self.external_state = external_state.resolve()
        self.tool = self.root / "wrappers/inference_tools/tools/dyngenie3"
        self.scripts = self.root / "wrappers/inference_tools/scripts"
        self.central_map = self.scripts / "template_map.json"
        self.catalog = self.root / "andrea/catalog_inference_tools/tools"
        self.calibration_catalog = self.evidence / "calibration-catalog"
        venv_python = self.root / ".venv/bin/python"
        self.python = str(venv_python) if venv_python.is_file() else sys.executable
        self.sleep = sleep
        self.freeze: dict[str, Any] = {}
        self.state: dict[str, Any] = {}
        if self.evidence.is_relative_to(self.tool):
            raise IntegrationError(
                "Keep evidence outside the frozen tool source directory."
            )
        if self.external_state == self.evidence / "state.json":
            raise IntegrationError(
                "External state must differ from supervisor state.json."
            )

    def log(self, message: str) -> None:
        line = f"{utc()} {message}"
        print(line, flush=True)
        with (self.evidence / "supervisor.log").open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def save(self, **fields: Any) -> None:
        self.state.update(fields)
        self.state["updated_at"] = utc()
        write_json(self.evidence / "state.json", self.state)

    @contextmanager
    def locked(self):
        self.evidence.mkdir(parents=True, exist_ok=True)
        with (self.evidence / "supervisor.lock").open("a+") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise IntegrationError(
                    "Another supervisor owns this evidence directory."
                ) from exc
            handle.seek(0)
            handle.truncate()
            handle.write(str(os.getpid()) + "\n")
            handle.flush()
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def source_inventory(self) -> dict[str, str]:
        required = [
            self.tool / name
            for name in (
                "continue_integration.py",
                "verify_runtime.py",
                "run_tool.py",
                "patch_upstream.py",
                "Dockerfile",
                "requirements.lock",
                "toolspec.draft.json",
                "smoketest.config.draft.json",
                "template_map.json",
            )
        ]
        required += [
            self.root / "wrappers/inference_tools/cost_profiles/dyngenie3.json",
            self.central_map,
        ]
        for path in required:
            if not path.is_file():
                raise IntegrationError(f"Frozen source is missing: {path}")
        directories = [
            self.tool,
            self.scripts,
            self.root / "wrappers/inference_tools/tests/fixtures/dyngenie3",
            self.root / "wrappers/inference_tools/tests/schemas",
            self.root / "andrea/catalog_inference_tools/schemas",
            self.root / "andrea/catalog_inference_tools/input_specs",
            self.root / "andrea/core/shared",
            self.root / "andrea/core/commands/infer_network",
            self.root / "andrea/core/commands/generate_data",
            self.root / "andrea/catalog_simulation_data_tools/schemas",
            self.root / "andrea/gui/infer_network",
            self.root / "andrea/cli",
            self.root / "wrappers/integration",
            self.root / "tests/core/shared",
            self.root / "tests/core/commands/infer_network",
            self.root / "tests/core/commands/generate_data",
            self.root / "tests/gui",
            self.root / "tests/cli",
            self.root / "tests/wrappers/inference_tools",
        ]
        paths = set(required)
        ignored = {"repo", "papers", "__pycache__", ".pytest_cache", "additional_files"}
        for directory in directories:
            if directory.is_dir():
                paths.update(
                    path
                    for path in directory.rglob("*")
                    if path.is_file()
                    and not ignored.intersection(path.relative_to(directory).parts)
                    and path.suffix not in {".pyc", ".lockfile"}
                )
        for relative in (
            "wrappers/benchmark_support.py",
            "wrappers/calibration_compatibility.py",
            "wrappers/validate_cost_predictions.py",
            "wrappers/integration/validate_integration.py",
            "scripts/render_doc_assets.py",
            "scripts/doc_assets/build_inference_tool_contract_map.py",
            "andrea/__main__.py",
            "andrea/__init__.py",
            "andrea/config.py",
            "tests/wrappers/test_benchmark_support.py",
            "tests/wrappers/test_cost_predictions.py",
            "tests/test_bulk_time_series_contract.py",
            "tests/test_catalog_figures.py",
        ):
            path = self.root / relative
            if path.is_file():
                paths.add(path)
        inventory = {
            str(path.relative_to(self.root)): sha(path)
            for path in sorted(paths)
            if path != self.central_map
        }
        central = read_json(self.central_map)
        # Promotion adds only this tool. Other entries/bundle definitions stay frozen.
        central["tools"] = {
            key: value for key, value in central["tools"].items() if key != "dyngenie3"
        }
        inventory[
            "wrappers/inference_tools/scripts/template_map.json#without-dyngenie3"
        ] = digest(central)
        return inventory

    def initialize(self) -> None:
        inventory = self.source_inventory()
        frozen = self.evidence / "freeze.json"
        identity = {
            "root": str(self.root),
            "external_state": str(self.external_state),
            "sources": inventory,
        }
        if frozen.exists():
            self.freeze = read_json(frozen)
            if self.freeze.get("identity") != identity:
                raise IntegrationError(
                    "Frozen sources/configuration changed; use a new evidence directory after review."
                )
        else:
            self.freeze = {
                "schema_version": 1,
                "tool_id": "dyngenie3",
                "identity": identity,
                "source_sha256": digest(inventory),
                "created_at": utc(),
            }
            write_json(frozen, self.freeze)
        state_path = self.evidence / "state.json"
        self.state = (
            read_json(state_path)
            if state_path.exists()
            else {
                "tool_id": "dyngenie3",
                "status": "planned",
                "phase": "planned",
                "functional": {"status": "pending"},
                "catalog_activation": {"status": "pending"},
                "calibration": {"status": "pending"},
                "coverage": {"status": "pending_extension"},
                "independent_predictions": {
                    "status": "pending_frozen_estimator_and_observations"
                },
                "published": False,
                "commands": [],
            }
        )
        if (
            self.state.get("source_sha256", self.freeze["source_sha256"])
            != self.freeze["source_sha256"]
        ):
            raise IntegrationError("State does not match the frozen source inventory.")
        self.save(source_sha256=self.freeze["source_sha256"])
        write_json(self.evidence / "plan.json", self.make_plan())

    def check_sources(self) -> None:
        if self.source_inventory() != self.freeze["identity"]["sources"]:
            raise IntegrationError(
                "Frozen source/configuration changed while waiting or executing; refusing the next phase."
            )
        activation = self.state.get("catalog_activation", {})
        if activation.get("status") in {"passed", "installed_pending_maps"}:
            for relative, expected in activation["artifacts"].items():
                path = self.root / relative
                if not path.is_file() or sha(path) != expected:
                    raise IntegrationError(
                        f"Activated artifact changed since verified promotion: {relative}"
                    )
            wanted = read_json(self.tool / "template_map.json")["tools"]["dyngenie3"]
            if read_json(self.central_map)["tools"].get("dyngenie3") != wanted:
                raise IntegrationError(
                    "Activated central template entry changed since verified promotion."
                )
        compatibility = self.state.get("calibration_compatibility", {})
        if compatibility.get("status") == "passed":
            receipt = Path(compatibility["receipt"])
            if not receipt.is_file() or sha(receipt) != compatibility["sha256"]:
                raise IntegrationError(
                    "Retained calibration compatibility receipt changed."
                )

    def make_plan(self) -> dict[str, Any]:
        config = read_json(
            self.root / "wrappers/inference_tools/cost_profiles/dyngenie3.json"
        )
        design = config["calibration_design"]
        sizes = sum(len(profile["sizes"]) for profile in config["profiles"])
        strata = len(design["resources"]["threads"]) * len(
            design["resources"]["ram_gb"]
        )
        calibration_runs = sizes * strata * design["repetitions"]
        timeout = design["timeout_seconds_per_run"]
        if (
            design["repetitions"] < 3
            or calibration_runs != design["planned_calibration_runs"]
            or timeout < 1
            or len(set(design["resources"]["threads"]))
            != len(design["resources"]["threads"])
            or len(set(design["resources"]["ram_gb"]))
            != len(design["resources"]["ram_gb"])
        ):
            raise IntegrationError(
                "Reviewed calibration allocation is inconsistent with its profiles/resources."
            )
        physical = self.functional_plan()
        functional_runs = physical["run_count"]
        functional_cap = physical["execution_timeout_cap_seconds"]
        holdouts = config["holdout_reservation"]
        holdout_runs = holdouts["planned_runs"]
        holdout_cap = holdouts["timeout_sum_seconds"]
        total_runs = functional_runs + calibration_runs + holdout_runs + 1
        total_cap = functional_cap + calibration_runs * timeout + holdout_cap + 3600
        return {
            "schema_version": 1,
            "source_sha256": self.freeze["source_sha256"],
            "external_state": str(self.external_state),
            "poll_seconds": POLL_SECONDS,
            "phase_order": [
                "wait_external",
                "build_candidate",
                "verify_functional",
                "activate_local_catalog",
                "calibrate_staging",
                "cost_gates",
                "freeze_and_validate_predictions",
                "register_validated_cost",
                "finalize_acceptance",
            ],
            "build": self.build_command(),
            "verify": self.verify_command(
                "sha256:" + "0" * 64, self.evidence / "functional-001"
            ),
            "verify_image_note": "The zero ID is a plan placeholder; execution uses the retained actual immutable image ID.",
            "calibration": self.benchmark_command(),
            "planned_calibration_runs": calibration_runs,
            "predictions": self.prediction_command("sha256:" + "0" * 64),
            "execution_budget": {
                "functional": {
                    "runs": functional_runs,
                    "timeout_seconds_per_run": physical["cases"][0]["timeout_seconds"],
                    "timeout_cap_seconds": functional_cap,
                    "saved_model_probes": physical.get("diagnostic_probe_count", 0),
                    "probe_timeout_seconds": physical.get(
                        "diagnostic_probe_timeout_seconds"
                    ),
                    "probe_budget": "Included inside the five diagnostic containers and their 3600-second timeout; no additional fit or container.",
                },
                "calibration": {
                    "runs": calibration_runs,
                    "timeout_seconds_per_run": timeout,
                    "timeout_cap_seconds": calibration_runs * timeout,
                },
                "independent_holdouts": {
                    "runs": holdout_runs,
                    "timeout_seconds_per_run": 3600,
                    "timeout_cap_seconds": holdout_cap,
                },
                "core_cli": {
                    "runs": 1,
                    "timeout_seconds_per_run": 3600,
                    "timeout_cap_seconds": 3600,
                },
                "total_container_runs": total_runs,
                "science_timeout_cap_seconds": total_cap,
                "estimated_duration_seconds": None,
                "build_and_validation_overhead_seconds": None,
                "note": "Serial timeout sum only; not a measured ETA. Build/startup/validation overhead remains unknown; failures are retained and never automatically retried.",
            },
            "reserved_holdouts": config["holdout_reservation"],
            "remaining_gates": design.get("acceptance_state", {}),
            "publication": "Never performed by this supervisor.",
            "pm2": {"autorestart": False, "instances": 1, "kill_timeout": 300000},
        }

    def tool_module(self, name: str):
        spec = importlib.util.spec_from_file_location(
            f"_dyngenie3_supervisor_{name}", self.tool / f"{name}.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def functional_plan(self) -> dict[str, Any]:
        return self.tool_module("verify_runtime").make_plan("sha256:" + "0" * 64)

    def compatibility_module(self):
        spec = importlib.util.spec_from_file_location(
            "_dyngenie3_source_compatibility",
            self.root / "wrappers/calibration_compatibility.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def guard(self, phase: str) -> None:
        self.check_sources()
        while True:
            receipt = external_receipt(self.external_state)
            if receipt is not None:
                self.save(status="running", phase=phase, external_state_receipt=receipt)
                self.log(f"External experiment completed; entering {phase}.")
                return
            self.save(
                status="waiting_external", phase=phase, external_state_receipt=None
            )
            self.log(
                f"Waiting for external status completed|complete|succeeded before {phase}; next check in {POLL_SECONDS}s."
            )
            self.sleep(POLL_SECONDS)
            self.check_sources()

    def command(
        self, args: list[str], label: str, *, allow_failure: bool = False
    ) -> subprocess.CompletedProcess[str]:
        index = len(self.state["commands"]) + 1
        log_path = self.evidence / "logs" / f"{index:03d}_{label}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        receipt = {
            "command": args,
            "log": str(log_path),
            "started_at": utc(),
            "status": "running",
        }
        self.state["commands"].append(receipt)
        self.save()
        self.log(f"Starting {label}; retained output: {log_path}")
        process = None
        interrupted = False
        with log_path.open("w", encoding="utf-8") as output:
            try:
                process = subprocess.Popen(
                    args,
                    cwd=self.root,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
                receipt["pid"] = process.pid
                self.save()
                code = process.wait()
            except BaseException as exc:
                interrupted = isinstance(exc, KeyboardInterrupt)
                receipt.update(
                    {
                        "status": (
                            "interrupted"
                            if isinstance(exc, KeyboardInterrupt)
                            else "failed"
                        ),
                        "error": str(exc),
                        "finished_at": utc(),
                        **({"exit_code": 130} if interrupted else {}),
                    }
                )
                self.save()
                raise
            finally:
                if process is not None and process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=15)
                if interrupted and label == "calibration":
                    receipt["container_cleanup"] = self.cleanup_calibration_containers()
                    output.flush()
                    receipt["log_sha256"] = sha(log_path)
                    cost = self.calibration_catalog / "dyngenie3/cost.json"
                    self.save(
                        calibration={
                            **self.state.get("calibration", {}),
                            "status": "interrupted",
                            "exit_code": 130,
                            "cost": str(cost),
                            "cost_sha256": sha(cost) if cost.is_file() else None,
                        }
                    )
        receipt.update(
            {
                "status": "passed" if code == 0 else "failed",
                "exit_code": code,
                "finished_at": utc(),
                "log_sha256": sha(log_path),
            }
        )
        self.save()
        result = subprocess.CompletedProcess(
            args, code, log_path.read_text(encoding="utf-8", errors="replace")
        )
        if code and not allow_failure:
            raise IntegrationError(f"{label} failed with exit {code}; see {log_path}")
        return result

    def cleanup_calibration_containers(self) -> list[dict[str, Any]]:
        """Remove only live benchmark containers bound to this campaign's IO."""
        calibration = (self.evidence / "calibration").resolve()
        reports = []
        try:
            live = subprocess.run(
                [
                    "docker",
                    "ps",
                    "--no-trunc",
                    "--filter",
                    "name=andrea_tool_cost_",
                    "--format",
                    "{{.ID}}",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if live.returncode:
                return [{"status": "inspect_failed", "error": live.stderr[-2000:]}]
            live_ids = set(live.stdout.split())
            seen = set()
            for cidfile in sorted(calibration.rglob(".docker-container.cid")):
                io = cidfile.parent.resolve()
                if not io.is_relative_to(calibration) or io.name != "io":
                    continue
                container_id = cidfile.read_text().strip()
                if (
                    not re.fullmatch(r"[a-f0-9]{64}", container_id)
                    or container_id not in live_ids
                    or container_id in seen
                ):
                    continue
                seen.add(container_id)
                report = {
                    "container_id": container_id,
                    "cidfile": str(cidfile),
                    "status": "ownership_unverified",
                }
                reports.append(report)
                result = subprocess.run(
                    ["docker", "container", "inspect", container_id],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                if result.returncode:
                    report.update(status="inspect_failed", error=result.stderr[-2000:])
                    continue
                objects = json.loads(result.stdout)
                if not isinstance(objects, list) or len(objects) != 1:
                    continue
                info = objects[0]
                if not isinstance(info, dict):
                    continue
                owns_mount = any(
                    mount.get("Type") == "bind"
                    and mount.get("Destination") == "/io"
                    and Path(mount.get("Source", "")).resolve() == io
                    for mount in info.get("Mounts", [])
                )
                if not (
                    info.get("Id") == container_id
                    and info.get("Name", "").startswith("/andrea_tool_cost_")
                    and info.get("Image") == self.state.get("image_id")
                    and owns_mount
                    and info.get("State", {}).get("Running") is True
                ):
                    continue
                removed = subprocess.run(
                    ["docker", "rm", "--force", container_id],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                report.update(
                    status="removed" if removed.returncode == 0 else "remove_failed",
                    exit_code=removed.returncode,
                    error=removed.stderr[-2000:],
                )
        except (OSError, ValueError, TypeError, subprocess.TimeoutExpired) as exc:
            reports.append({"status": "cleanup_failed", "error": str(exc)})
        return reports

    def image(self, reference: str) -> str | None:
        result = self.command(
            ["docker", "image", "inspect", reference],
            "image_inspect",
            allow_failure=True,
        )
        if result.returncode:
            if re.search(r"no such (image|object)", result.stdout, re.IGNORECASE):
                return None
            raise IntegrationError(
                f"Could not inspect local image {reference}; refusing to infer absence."
            )
        rows = json.loads(result.stdout)
        identity = rows[0]["Id"]
        if not IMAGE_ID.fullmatch(identity):
            raise IntegrationError("Docker did not return a full immutable image ID.")
        return identity

    def tag(self) -> str:
        return read_json(self.tool / "toolspec.draft.json")["docker_image"]

    def candidate_tag(self) -> str:
        return self.tag() + "-candidate-" + self.freeze["source_sha256"][:12]

    def build_command(self) -> list[str]:
        return [
            self.python,
            str(self.scripts / "build_tool_images.py"),
            "--tool",
            "dyngenie3",
            "--catalog-tools-root",
            str(self.evidence / "draft-catalog"),
            "--template-map",
            str(self.tool / "template_map.json"),
            "--image-tag",
            "dyngenie3=" + self.candidate_tag(),
        ]

    def verify_command(self, image: str, out: Path) -> list[str]:
        return [
            self.python,
            str(self.tool / "verify_runtime.py"),
            "--image",
            image,
            "--out",
            str(out),
            "--external-state",
            str(self.external_state),
        ]

    def benchmark_command(self) -> list[str]:
        config = read_json(
            self.root / "wrappers/inference_tools/cost_profiles/dyngenie3.json"
        )
        design = config["calibration_design"]
        command = [
            self.python,
            str(self.scripts / "benchmark_costs.py"),
            "--tool",
            "dyngenie3",
            "--catalog-tools-root",
            str(self.calibration_catalog),
            "--catalog-images",
            "--skip-build",
            "--threads",
            ",".join(map(str, design["resources"]["threads"])),
            "--ram-gb",
            ",".join(map(str, design["resources"]["ram_gb"])),
            "--repeats",
            str(design["repetitions"]),
            "--timeout",
            str(design["timeout_seconds_per_run"]),
            "--results-dir",
            str(self.evidence / "calibration"),
            "--fail-fast",
        ]
        if self.state.get("reuse_calibration_from"):
            command += [
                "--reuse-results-dir",
                str(Path(self.state["reuse_calibration_from"]) / "calibration"),
            ]
        return command

    def prediction_command(self, image: str) -> list[str]:
        command = [
            self.python,
            str(self.tool / "validate_runtime_predictions.py"),
            "--calibration-root",
            str(self.evidence / "calibration"),
            "--cost",
            str(self.calibration_catalog / "dyngenie3/cost.json"),
            "--image",
            image,
            "--out",
            str(self.evidence / "predictions"),
            "--external-state",
            str(self.external_state),
        ]
        if self.state.get("calibration_compatibility", {}).get("status") == "passed":
            command += [
                "--compatibility-receipt",
                self.state["calibration_compatibility"]["receipt"],
            ]
        return command

    def build(self) -> None:
        self.guard("build_candidate")
        retained = self.state.get("image_id")
        for reference in (self.tag(), self.candidate_tag()):
            current = self.image(reference)
            if current is not None and current != retained:
                raise IntegrationError(
                    f"Refusing to overwrite existing unrecognized image tag: {reference}"
                )
        if retained is not None:
            if self.image(retained) != retained:
                raise IntegrationError("Retained immutable image is missing.")
            self.log("Reusing the matching retained immutable candidate image.")
            return
        draft = self.evidence / "draft-catalog/dyngenie3/toolspec.json"
        draft.parent.mkdir(parents=True, exist_ok=True)
        draft.write_bytes((self.tool / "toolspec.draft.json").read_bytes())
        self.command(self.build_command(), "build_candidate")
        identity = self.image(self.candidate_tag())
        if identity is None:
            raise IntegrationError(
                "Candidate build did not leave an inspectable image."
            )
        self.save(
            image_id=identity,
            candidate_tag=self.candidate_tag(),
            image_tag=self.tag(),
            build={"status": "passed", "source_sha256": self.freeze["source_sha256"]},
        )

    def reuse_image(self, previous: Path) -> None:
        """Import a retained build, never its acceptance status or measurements.

        The failed campaign remains immutable. Verification runs again against
        the new frozen sources, including any strengthened acceptance checks.
        """
        previous = previous.resolve()
        if previous == self.evidence:
            raise IntegrationError(
                "Image reuse requires a separate evidence directory."
            )
        old_state_path, old_freeze_path = (
            previous / "state.json",
            previous / "freeze.json",
        )
        old = read_json(old_state_path)
        frozen = read_json(old_freeze_path)
        old_sources = frozen.get("identity", {}).get("sources", {})
        if (
            not old_sources
            or frozen.get("source_sha256") != digest(old_sources)
            or old.get("source_sha256") != frozen["source_sha256"]
        ):
            raise IntegrationError(
                "Previous source inventory or state hash is inconsistent."
            )
        image = old.get("image_id", "")
        if (
            not IMAGE_ID.fullmatch(image)
            or old.get("build", {}).get("status") != "passed"
        ):
            raise IntegrationError(
                "Previous campaign lacks a successful immutable build."
            )
        changed = [
            name
            for name in IMAGE_SUBJECTS
            if not old_sources.get(name)
            or old_sources[name] != self.freeze["identity"]["sources"].get(name)
        ]
        if changed:
            raise IntegrationError(
                f"Installed image subjects changed; rebuild instead of reuse: {changed}"
            )
        builds = [
            row
            for row in old.get("commands", [])
            if row.get("status") == "passed"
            and row.get("exit_code") == 0
            and any(
                str(arg).endswith("/build_tool_images.py")
                for arg in row.get("command", [])
            )
        ]
        if len(builds) != 1:
            raise IntegrationError(
                "Previous campaign must retain one successful build receipt."
            )
        log = Path(builds[0]["log"])
        if not log.is_file() or sha(log) != builds[0].get("log_sha256"):
            raise IntegrationError(
                "Previous build output evidence is missing or changed."
            )
        retained = self.state.get("image_id")
        if retained is not None and retained != image:
            raise IntegrationError(
                "Current campaign already identifies a different image."
            )
        self.guard("reuse_verified_build")
        if self.image(image) != image:
            raise IntegrationError("Retained immutable image is unavailable.")
        proof = {
            "status": "passed",
            "image_id": image,
            "previous_campaign": str(previous),
            "previous_state_sha256": sha(old_state_path),
            "previous_freeze_sha256": sha(old_freeze_path),
            "build_log": str(log),
            "build_log_sha256": sha(log),
            "unchanged_image_subjects": {
                name: old_sources[name] for name in IMAGE_SUBJECTS
            },
            "new_source_sha256": self.freeze["source_sha256"],
            "scope": "Immutable build reuse only. No functional, cost or prediction gate is imported.",
        }
        write_json(self.evidence / "image-reuse.json", proof)
        self.save(
            image_id=image,
            image_tag=self.tag(),
            build={
                "status": "reused_immutable_image",
                "receipt": str(self.evidence / "image-reuse.json"),
                "sha256": sha(self.evidence / "image-reuse.json"),
            },
        )
        self.log(
            "Reused the prior immutable build after exact installed-source comparison; all acceptance checks remain required."
        )

    def verify(self) -> None:
        self.guard("verify_functional")
        retained = self.state.get("functional", {})
        if retained.get("status") == "passed":
            path = Path(retained["receipt"])
            if sha(path) != retained["sha256"]:
                raise IntegrationError(
                    "Retained functional verification receipt changed."
                )
            return
        attempts = list(self.evidence.glob("functional-[0-9][0-9][0-9]"))
        out = self.evidence / f"functional-{len(attempts) + 1:03d}"
        self.save(functional={"status": "running", "evidence": str(out)})
        result = self.command(
            self.verify_command(self.state["image_id"], out),
            "verify_functional",
            allow_failure=True,
        )
        path = out / "verification.json"
        receipt = read_json(path) if path.is_file() else {}
        if (
            result.returncode
            or receipt.get("status") != "passed"
            or receipt.get("image_id") != self.state["image_id"]
        ):
            self.save(
                functional={
                    "status": "failed",
                    "evidence": str(out),
                    "reported_status": receipt.get("status"),
                    "exit_code": result.returncode,
                }
            )
            raise IntegrationError(
                "Functional verification did not pass for the retained immutable image; catalog remains inactive."
            )
        self.save(
            functional={
                "status": "passed",
                "receipt": str(path),
                "sha256": sha(path),
                "image_id": self.state["image_id"],
            }
        )

    def reuse_calibration(self, previous: Path) -> None:
        """Import receipts only after proving the measured task did not change."""
        self.guard("reuse_calibration")
        previous = previous.resolve()
        retained = self.state.get("calibration_compatibility", {})
        if retained.get("status") == "passed":
            if self.state.get("reuse_calibration_from") != str(previous):
                raise IntegrationError(
                    "Another calibration source is already retained."
                )
            receipt_path = Path(retained["receipt"])
            if sha(receipt_path) != retained["sha256"]:
                raise IntegrationError(
                    "Retained calibration compatibility receipt changed."
                )
            return
        old_state_path, old_freeze_path = (
            previous / "state.json",
            previous / "freeze.json",
        )
        old_state, old_freeze = read_json(old_state_path), read_json(old_freeze_path)
        if old_state.get("status") not in {
            "interrupted",
            "failed",
            "calibrated_within_declared_domain",
        }:
            raise IntegrationError(
                "Stop and retain the original campaign before importing calibration."
            )
        if old_state.get("image_id") != self.state.get("image_id") or old_state.get(
            "source_sha256"
        ) != old_freeze.get("source_sha256"):
            raise IntegrationError(
                "Original calibration campaign image/freeze differs."
            )
        old_cost = previous / "calibration-catalog/dyngenie3/cost.json"
        if not old_cost.is_file() or sha(old_cost) != old_state.get(
            "calibration", {}
        ).get("cost_sha256"):
            raise IntegrationError(
                "Original calibration checkpoint is missing, unsealed or changed."
            )
        compatibility = self.compatibility_module()
        subjects = {}
        for name in compatibility.MEASUREMENT_SUBJECTS:
            path = self.root / name
            if not path.is_file() or sha(path) != old_freeze["identity"]["sources"].get(
                name
            ):
                raise IntegrationError(
                    f"Cannot reuse changed measurement/execution subject: {name}"
                )
            subjects[name] = {"path": str(path), "sha256": sha(path)}
        support = self.tool_module("validate_runtime_predictions")
        _, backend, _ = support._support()
        payload = read_json(old_cost)
        seed = payload["profiles"][0]["benchmark_config"]["provenance"]["seed"]
        observed = backend.collect_provenance(
            root=self.root,
            spec_path=self.catalog / "dyngenie3/toolspec.json",
            image=self.state["image_id"],
            seed=seed,
            source_dir=self.tool,
        )
        profiles = {
            profile.profile_id: profile
            for profile in backend.resolve_benchmark_profiles(
                tool_id="dyngenie3", catalog_tools_root=self.catalog
            )
        }
        directory = self.evidence / "calibration-compatibility"
        if directory.exists():
            raise IntegrationError(
                "Retained compatibility attempt exists; review before retry."
            )
        directory.mkdir(parents=True)
        for source, name in (
            (old_state_path, "original-state.json"),
            (old_freeze_path, "original-freeze.json"),
            (old_cost, "original-cost.json"),
        ):
            (directory / name).write_bytes(source.read_bytes())
        write_json(directory / "observed-provenance.json", observed)
        ref = lambda path, **fields: {
            "path": str(path.resolve()),
            "sha256": sha(path),
            **fields,
        }
        originals, captures, generated = [], {}, {}
        for raw_path in sorted((previous / "calibration").rglob("measurements.json")):
            name = raw_path.parent.name
            if name not in profiles:
                raise IntegrationError(
                    f"Original measured profile is not registered in the expanded recipe: {name}"
                )
            profile = profiles[name]
            config_path = raw_path.with_name("benchmark_config.json")
            config = read_json(config_path)
            current = {
                "provenance": observed,
                "params_profile": profile.params_profile,
                "execution_profile": profile.execution_profile,
                "input_profile": profile.input_profile,
            }
            if compatibility.context(config) != compatibility.context(current):
                raise IntegrationError(
                    f"Original measured task context differs: {name}"
                )
            originals.append(
                {"measurements": ref(raw_path), "config": ref(config_path)}
            )
            for index, row in enumerate(json.loads(raw_path.read_text())):
                if row.get("status") != "ok":
                    continue
                run_id = row.get("run_id")
                if not isinstance(run_id, str) or not run_id or run_id in captures:
                    raise IntegrationError(
                        "Original campaign has missing or duplicate actual execution IDs; review before migration."
                    )
                key = (name, row["genes"], row["columns"])
                if key not in generated:
                    io = directory / "inputs" / name / f"g{key[1]}_c{key[2]}" / "io"
                    backend.prepare_io_dir(
                        io,
                        backend.SizePoint(key[1], key[2]),
                        resolved_params=profile.params,
                        execution=profile.execution,
                        seed=seed,
                        input_profile=profile.input_profile,
                    )
                    generated[key] = io
                if compatibility.inputs(Path(row["io_dir"])) != row.get(
                    "input_sha256"
                ) or compatibility.inputs(generated[key]) != row.get("input_sha256"):
                    raise IntegrationError(
                        "Original and expanded generated input bytes differ."
                    )
                provenance = row.get("measurement_provenance", config["provenance"])
                captures[run_id] = {
                    "raw_receipt": ref(raw_path, pointer=f"/{index}"),
                    "config": ref(config_path),
                    "raw_row_sha256": compatibility.digest(row),
                    "execution_sha256": compatibility.execution_digest(row),
                    "original_source_sha256": provenance["source_sha256"],
                    "context_sha256": compatibility.digest(
                        compatibility.context(config)
                    ),
                    "generated_io": str(generated[key]),
                }
        proof = {
            "schema_version": 1,
            "kind": "dyngenie3_calibration_compatibility",
            "status": "passed",
            "created_at": utc(),
            "image_id": self.state["image_id"],
            "current_source_sha256": observed["source_sha256"],
            "current_freeze_source_sha256": self.freeze["source_sha256"],
            "original_campaign": str(previous),
            "original_state": ref(directory / "original-state.json"),
            "original_freeze": ref(directory / "original-freeze.json"),
            "original_cost": ref(directory / "original-cost.json"),
            "observed_provenance": ref(directory / "observed-provenance.json"),
            "measurement_subjects": subjects,
            "original_measurements": originals,
            "executions": captures,
            "scope": "Unchanged immutable image, closed execution/timing/fixture implementation, hardware/context and byte-identical inputs only. Original observations/provenance remain unchanged. No earlier acceptance or statistical decision is imported.",
        }
        proof_path = directory / "compatibility.json"
        write_json(proof_path, proof)
        compatibility.verify(
            proof_path,
            image_id=self.state["image_id"],
            current_source_sha256=observed["source_sha256"],
        )
        cost = self.calibration_catalog / "dyngenie3/cost.json"
        if cost.exists():
            raise IntegrationError(
                "Refusing to replace an existing staging calibration during migration."
            )
        cost.parent.mkdir(parents=True, exist_ok=True)
        cost.write_bytes(old_cost.read_bytes())
        self.save(
            reuse_calibration_from=str(previous),
            calibration_compatibility={
                "status": "passed",
                "receipt": str(proof_path),
                "sha256": sha(proof_path),
                "captured_original_executions": len(captures),
            },
            calibration={
                "status": "imported_checkpoint",
                "cost": str(cost),
                "cost_sha256": sha(cost),
            },
        )
        write_json(self.evidence / "plan.json", self.make_plan())

    def promote(self) -> None:
        self.guard("activate_local_catalog")
        if self.state["functional"].get("status") != "passed":
            raise IntegrationError(
                "Catalog activation requires passed functional verification."
            )
        identity = self.state["image_id"]
        current = self.image(self.tag())
        if current is not None and current != identity:
            raise IntegrationError(
                "Catalog image tag changed; refusing to overwrite it."
            )
        if current is None:
            self.command(
                ["docker", "image", "tag", identity, self.tag()],
                "tag_verified_local_image",
            )
        if self.image(self.tag()) != identity:
            raise IntegrationError(
                "Local catalog image tag does not identify the verified image."
            )
        candidate_map = read_json(self.tool / "template_map.json")
        central = read_json(self.central_map)
        for bundle, templates in candidate_map["bundle_templates"].items():
            if central["bundle_templates"].get(bundle) != templates:
                raise IntegrationError(
                    f"Promotion would modify shared template bundle {bundle}."
                )
        wanted = candidate_map["tools"]["dyngenie3"]
        if "dyngenie3" in central["tools"] and central["tools"]["dyngenie3"] != wanted:
            raise IntegrationError(
                "Refusing to replace a different central dynGENIE3 template entry."
            )
        targets = [
            (
                self.tool / "toolspec.draft.json",
                self.catalog / "dyngenie3/toolspec.json",
            ),
            (
                self.tool / "smoketest.config.draft.json",
                self.root
                / "wrappers/inference_tools/tests/smoketest_configs/dyngenie3.json",
            ),
        ]
        for source, target in targets:
            if target.exists() and target.read_bytes() != source.read_bytes():
                raise IntegrationError(
                    f"Refusing to replace a different activated artifact: {target}"
                )
        for source, target in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(target.suffix + ".tmp")
            temporary.write_bytes(source.read_bytes())
            temporary.replace(target)
        central["tools"]["dyngenie3"] = wanted
        write_json(self.central_map, central)
        self.save(
            catalog_activation={
                "status": "installed_pending_maps",
                "artifacts": {
                    str(target.relative_to(self.root)): sha(target)
                    for _, target in targets
                },
                "image_id": identity,
                "published": False,
            }
        )
        self.command(
            [
                self.python,
                str(self.root / "scripts/render_doc_assets.py"),
                "--catalogs-only",
            ],
            "render_catalogs",
        )
        self.save(
            catalog_activation={
                "status": "passed",
                "artifacts": {
                    str(target.relative_to(self.root)): sha(target)
                    for _, target in targets
                },
                "image_id": identity,
                "published": False,
            }
        )

    def calibrate(self) -> None:
        self.guard("calibrate")
        if self.image(self.tag()) != self.state["image_id"]:
            raise IntegrationError(
                "Catalog image tag differs from the verified immutable image."
            )
        retained = self.state["calibration"]
        cost = self.calibration_catalog / "dyngenie3/cost.json"
        stage_spec = cost.with_name("toolspec.json")
        stage_spec.parent.mkdir(parents=True, exist_ok=True)
        reviewed_spec = (self.tool / "toolspec.draft.json").read_bytes()
        if stage_spec.exists() and stage_spec.read_bytes() != reviewed_spec:
            raise IntegrationError(
                "Staging calibration ToolSpec differs from the reviewed draft."
            )
        stage_spec.write_bytes(reviewed_spec)
        if cost.exists() and (
            retained.get("cost_sha256") is None or sha(cost) != retained["cost_sha256"]
        ):
            raise IntegrationError(
                "Refusing to replace an unrecognized or changed cost.json."
            )
        if retained.get("status") == "measured":
            if not cost.is_file():
                raise IntegrationError("Retained measured cost.json is missing.")
            return
        planned_runs = self.make_plan()["planned_calibration_runs"]
        self.save(calibration={"status": "running", "planned_runs": planned_runs})
        result = self.command(
            self.benchmark_command(), "calibration", allow_failure=True
        )
        measured = result.returncode == 0 and cost.is_file()
        self.save(
            calibration={
                "status": "measured" if measured else "failed",
                "exit_code": result.returncode,
                "planned_runs": planned_runs,
                "cost": str(cost),
                "cost_sha256": sha(cost) if cost.is_file() else None,
            }
        )
        if not measured:
            raise IntegrationError(
                "Calibration failed; retained raw attempts and partial costs require review before resuming."
            )

    def gates(self) -> None:
        self.guard("report_gates")
        base = [
            self.python,
            str(self.scripts / "validate_tool_costs.py"),
            "--tool",
            "dyngenie3",
            "--catalog-tools-root",
            str(self.calibration_catalog),
            "--require",
            "--require-provenance",
        ]
        structural = self.command(
            base, "cost_structural_provenance", allow_failure=True
        )
        domain = self.command(
            [
                *base,
                "--require-coverage",
                "--coverage-report",
                str(self.evidence / "coverage.json"),
            ],
            "cost_domain",
            allow_failure=True,
        )
        self.save(
            coverage={
                "status": (
                    "passed"
                    if domain.returncode == 0
                    else "pending_extension_or_evidence"
                ),
                "exit_code": domain.returncode,
                "report": str(self.evidence / "coverage.json"),
                "structural_provenance_exit_code": structural.returncode,
            },
            status=(
                "pending_independent_validation"
                if structural.returncode == domain.returncode == 0
                else "pending_cost_gate_review"
            ),
            phase="awaiting_remaining_gates",
            published=False,
        )
        if structural.returncode or domain.returncode:
            self.log(
                "Cost gate failed; measured staging evidence is preserved and no cost was registered in the active catalog."
            )
            return
        self.guard("freeze_and_validate_predictions")
        report_path = self.evidence / "predictions/prediction-report.json"
        retained = self.state["independent_predictions"]
        if retained.get("status") == "passed":
            if not report_path.is_file() or sha(report_path) != retained.get("sha256"):
                raise IntegrationError(
                    "Retained independent prediction report changed."
                )
        else:
            result = self.command(
                self.prediction_command(self.state["image_id"]),
                "independent_predictions",
                allow_failure=True,
            )
            report = read_json(report_path) if report_path.is_file() else {}
            passed = (
                result.returncode == 0
                and report.get("status") == "passed"
                and report.get("image_id") == self.state["image_id"]
                and report.get("observed_run_count")
                == self.make_plan()["execution_budget"]["independent_holdouts"]["runs"]
            )
            self.save(
                independent_predictions={
                    "status": (
                        "passed" if passed else "pending_failed_or_blocked_validation"
                    ),
                    "exit_code": result.returncode,
                    "report": str(report_path),
                    "sha256": sha(report_path) if report_path.is_file() else None,
                    "observed_run_count": report.get("observed_run_count"),
                    "measured": bool(report.get("observed_run_count")),
                }
            )
            if not passed:
                self.save(
                    status="pending_prediction_review", phase="awaiting_remaining_gates"
                )
                self.log(
                    "Independent prediction gate did not pass; raw attempts remain retained and the active catalog has no new cost file."
                )
                return
        self.guard("register_validated_cost")
        source = self.calibration_catalog / "dyngenie3/cost.json"
        target = self.catalog / "dyngenie3/cost.json"
        if target.exists() and target.read_bytes() != source.read_bytes():
            raise IntegrationError("Refusing to replace a different active cost file.")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".json.tmp")
        temporary.write_bytes(source.read_bytes())
        temporary.replace(target)
        self.save(
            cost_catalog_registration={
                "status": "passed",
                "path": str(target),
                "sha256": sha(target),
            },
            status="pending_acceptance_dossier",
            phase="finalize_acceptance",
        )
        self.finalize_acceptance()

    def finalize_acceptance(self) -> None:
        self.guard("finalize_acceptance")
        helper = self.tool / "finalize_evidence.py"
        if not helper.is_file():
            self.save(
                acceptance={"status": "pending_helper"},
                status="pending_acceptance_dossier",
            )
            return
        result = self.command(
            [
                self.python,
                str(helper),
                "--campaign",
                str(self.evidence),
                "--image",
                self.state["image_id"],
                "--external-state",
                str(self.external_state),
            ],
            "finalize_acceptance",
            allow_failure=True,
        )
        report_path = self.evidence / "acceptance/acceptance.json"
        report = read_json(report_path) if report_path.is_file() else {}
        passed = (
            result.returncode == 0
            and report.get("status") == "passed"
            and report.get("image_id") == self.state["image_id"]
        )
        self.save(
            acceptance={
                "status": "passed" if passed else "pending_acceptance_review",
                "exit_code": result.returncode,
                "path": str(report_path),
                "sha256": sha(report_path) if report_path.is_file() else None,
            },
            status=(
                "calibrated_within_declared_domain"
                if passed
                else "pending_acceptance_review"
            ),
            published=False,
        )

    def run(
        self,
        *,
        plan_only: bool = False,
        reuse_image_from: Path | None = None,
        reuse_calibration_from: Path | None = None,
    ) -> int:
        with self.locked():
            self.initialize()
            if plan_only:
                self.log(
                    "Plan only: frozen sources and commands saved; no Docker, builds or fits invoked."
                )
                return 0
            try:
                if reuse_image_from is not None:
                    self.reuse_image(reuse_image_from)
                self.build()
                self.verify()
                self.promote()
                if reuse_calibration_from is not None:
                    self.reuse_calibration(reuse_calibration_from)
                self.calibrate()
                self.gates()
                if self.state.get("status") != "calibrated_within_declared_domain":
                    self.log(
                        "Campaign stopped with acceptance gates still open; this is not a successful integration exit."
                    )
                    return 2
                return 0
            except KeyboardInterrupt:
                self.save(
                    status="interrupted", phase=self.state.get("phase"), published=False
                )
                self.log(
                    "Supervisor interrupted; retained evidence is preserved and no automatic restart is requested."
                )
                return 130
            except Exception as exc:
                self.save(
                    status="failed",
                    error=str(exc),
                    failed_phase=self.state.get("phase"),
                    published=False,
                )
                self.log(f"Stopped: {exc}")
                return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--external-state", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument(
        "--reuse-image-from",
        type=Path,
        help="Reuse only a proven immutable build from a prior campaign after exact installed-source comparison; rerun acceptance.",
    )
    parser.add_argument(
        "--reuse-calibration-from",
        type=Path,
        help="Import a stopped campaign checkpoint only after closed implementation, immutable-image, hardware/context and input-byte compatibility checks; retain original provenance.",
    )
    args = parser.parse_args(argv)

    def stop(_number, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        return Supervisor(
            root=ROOT, evidence=args.evidence, external_state=args.external_state
        ).run(
            plan_only=args.plan_only,
            reuse_image_from=args.reuse_image_from,
            reuse_calibration_from=args.reuse_calibration_from,
        )
    except (IntegrationError, OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
