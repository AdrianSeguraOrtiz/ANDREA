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
        if activation.get("status") == "passed":
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

    def make_plan(self) -> dict[str, Any]:
        config = read_json(
            self.root / "wrappers/inference_tools/cost_profiles/dyngenie3.json"
        )
        design = config["calibration_design"]
        sizes = sum(len(profile["sizes"]) for profile in config["profiles"])
        if (
            design["planned_calibration_runs"],
            design["repetitions"],
            design["timeout_seconds_per_run"],
            sizes,
            design["resources"]["threads"],
            design["resources"]["ram_gb"],
        ) != (408, 3, 3600, 34, [1, 2], [4, 8]):
            raise IntegrationError(
                "Reviewed calibration allocation changed; update/review supervisor commands first."
            )
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
            "planned_calibration_runs": 408,
            "predictions": self.prediction_command("sha256:" + "0" * 64),
            "execution_budget": {
                "functional": {
                    "runs": 12,
                    "timeout_seconds_per_run": 3600,
                    "timeout_cap_seconds": 43200,
                },
                "calibration": {
                    "runs": 408,
                    "timeout_seconds_per_run": 3600,
                    "timeout_cap_seconds": 1468800,
                },
                "independent_holdouts": {
                    "runs": 24,
                    "timeout_seconds_per_run": 3600,
                    "timeout_cap_seconds": 86400,
                },
                "core_cli": {
                    "runs": 1,
                    "timeout_seconds_per_run": 3600,
                    "timeout_cap_seconds": 3600,
                },
                "total_container_runs": 445,
                "science_timeout_cap_seconds": 1602000,
                "estimated_duration_seconds": None,
                "build_and_validation_overhead_seconds": None,
                "note": "Serial timeout sum only, about 18.54 days; not a measured ETA. Build/startup/validation overhead remains unknown; failures are retained and never automatically retried.",
            },
            "reserved_holdouts": config["holdout_reservation"],
            "remaining_gates": design.get("acceptance_state", {}),
            "publication": "Never performed by this supervisor.",
            "pm2": {"autorestart": False, "instances": 1, "kill_timeout": 60000},
        }

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
                receipt.update(
                    {
                        "status": (
                            "interrupted"
                            if isinstance(exc, KeyboardInterrupt)
                            else "failed"
                        ),
                        "error": str(exc),
                        "finished_at": utc(),
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
        return [
            self.python,
            str(self.scripts / "benchmark_costs.py"),
            "--tool",
            "dyngenie3",
            "--catalog-tools-root",
            str(self.calibration_catalog),
            "--catalog-images",
            "--skip-build",
            "--threads",
            "1,2",
            "--ram-gb",
            "4,8",
            "--repeats",
            "3",
            "--timeout",
            "3600",
            "--results-dir",
            str(self.evidence / "calibration"),
            "--fail-fast",
        ]

    def prediction_command(self, image: str) -> list[str]:
        return [
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
        self.save(calibration={"status": "running", "planned_runs": 408})
        result = self.command(
            self.benchmark_command(), "calibration", allow_failure=True
        )
        measured = result.returncode == 0 and cost.is_file()
        self.save(
            calibration={
                "status": "measured" if measured else "failed",
                "exit_code": result.returncode,
                "planned_runs": 408,
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
                and report.get("observed_run_count") == 24
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

    def run(self, *, plan_only: bool = False) -> int:
        with self.locked():
            self.initialize()
            if plan_only:
                self.log(
                    "Plan only: frozen sources and commands saved; no Docker, builds or fits invoked."
                )
                return 0
            try:
                self.build()
                self.verify()
                self.promote()
                self.calibrate()
                self.gates()
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
    args = parser.parse_args(argv)

    def stop(_number, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        return Supervisor(
            root=ROOT, evidence=args.evidence, external_state=args.external_state
        ).run(plan_only=args.plan_only)
    except (IntegrationError, OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
