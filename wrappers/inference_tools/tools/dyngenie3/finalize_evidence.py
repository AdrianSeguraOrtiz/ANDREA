#!/usr/bin/env python3
"""Seal a separate acceptance dossier from real campaign and interface evidence.

This never edits the frozen source dossier or publishes an image. A successful
report applies only to the finite domain and predictions actually verified.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
from pathlib import Path
import signal
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(HERE))
from continue_integration import (
    Supervisor,
    external_receipt,
    read_json,
    sha,
    write_json,
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def reference(path: Path, **fields):
    return {**fields, "path": str(path.resolve()), "sha256": sha(path)}


def passed(path: Path, image: str | None = None):
    data = read_json(path)
    require(data.get("status") == "passed", f"Evidence has not passed: {path}")
    if image:
        require(data.get("image_id") == image, f"Evidence image differs: {path}")
    return data


def verify_review(path: Path, source_sha256: str):
    review = passed(path)
    require(review.get("exit_code") == 0, "Source review failed")
    require(review.get("source_sha256") == source_sha256, "Source review is stale")
    outputs = review.get("outputs", [])
    require(
        {row.get("role") for row in outputs}
        >= {"lightweight_tests", "gui_browser", "scientific_audit"},
        "Source review lacks tests, browser or scientific evidence",
    )
    for row in outputs:
        target = Path(row["path"])
        require(
            target.is_file() and sha(target) == row.get("sha256"),
            f"Source review output changed: {target}",
        )
    return review


def command(args: list[str], out: Path):
    with out.open("w", encoding="utf-8") as handle:
        process = subprocess.Popen(
            args, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT
        )
        try:
            code = process.wait()
        except BaseException:
            # Ask the owned CLI to unwind its execution before terminating it.
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=15)
            raise
    require(code == 0, f"Command failed ({code}); see {out}")


def inspect_image(tag: str, image: str):
    identity = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", tag], text=True
    ).strip()
    require(identity == image, "Catalog tag differs from the verified image")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--external-state", type=Path, required=True)
    args = parser.parse_args(argv)
    campaign = args.campaign.resolve()
    out = campaign / "acceptance"
    if out.is_dir() and any(out.iterdir()):
        print(
            "Retained acceptance attempt exists; preserve it and review before retry.",
            file=sys.stderr,
        )
        return 2
    out.mkdir(parents=True, exist_ok=True)
    report = {"status": "running", "image_id": args.image, "published": False}
    try:
        require(
            external_receipt(args.external_state) is not None,
            "External experiment has not successfully completed",
        )
        frozen = read_json(campaign / "freeze.json")
        supervisor = Supervisor(
            root=ROOT, evidence=campaign, external_state=args.external_state
        )
        require(
            supervisor.source_inventory() == frozen["identity"]["sources"],
            "Frozen campaign sources changed",
        )
        review_path = campaign / "review.json"
        verify_review(review_path, frozen["source_sha256"])
        state = read_json(campaign / "state.json")
        require(state.get("image_id") == args.image, "Campaign image differs")
        functional_path = Path(state["functional"]["receipt"])
        require(
            sha(functional_path) == state["functional"]["sha256"],
            "Functional receipt changed",
        )
        passed(functional_path, args.image)
        require(state["coverage"]["status"] == "passed", "Cost domain has not passed")
        require(
            state["coverage"].get("structural_provenance_exit_code") == 0,
            "Cost structure/provenance has not passed",
        )
        prediction_dir = campaign / "predictions"
        prediction_path = prediction_dir / "prediction-report.json"
        passed(prediction_path, args.image)
        cost = ROOT / "andrea/catalog_inference_tools/tools/dyngenie3/cost.json"
        require(
            cost.is_file() and sha(cost) == state["calibration"].get("cost_sha256"),
            "Accepted calibration cost changed",
        )

        spec_path = (
            ROOT / "andrea/catalog_inference_tools/tools/dyngenie3/toolspec.json"
        )
        spec = read_json(spec_path)
        require(
            spec_path.read_bytes() == (HERE / "toolspec.draft.json").read_bytes(),
            "Active spec differs from reviewed draft",
        )
        inspect_image(spec["docker_image"], args.image)
        scripts = ROOT / "wrappers/inference_tools/scripts"
        for script in ("validate_toolspecs.py", "validate_smoketest_configs.py"):
            command(
                [sys.executable, str(scripts / script), "--tool", "dyngenie3"],
                out / f"{script}.log",
            )
        command(
            [
                sys.executable,
                str(scripts / "validate_tool_costs.py"),
                "--tool",
                "dyngenie3",
                "--require",
                "--require-provenance",
                "--require-coverage",
                "--coverage-report",
                str(out / "cost-coverage.json"),
            ],
            out / "cost-gates.log",
        )
        command(
            [
                sys.executable,
                str(ROOT / "wrappers/validate_cost_predictions.py"),
                "--plan",
                str(prediction_dir / "prediction-plan.json"),
                "--observations",
                str(prediction_dir / "observations.json"),
                "--report",
                str(out / "prediction-gate.json"),
            ],
            out / "prediction-gate.log",
        )
        require(
            read_json(out / "prediction-gate.json").get("passed") is True,
            "Independent prediction checker did not pass",
        )
        cli_root = out / "cli"
        require(
            not cli_root.exists(), "Retained CLI attempt exists; review before retry"
        )
        fixture = ROOT / "wrappers/inference_tools/tests/fixtures/dyngenie3"
        require(
            external_receipt(args.external_state) is not None,
            "External experiment changed before CLI execution",
        )
        cli_args = [
            sys.executable,
            "-m",
            "andrea",
            "infer-network",
            "execute",
            "--dataset-manifest",
            str(fixture / "dataset-manifest.json"),
            "--tools-params",
            str(fixture / "tools-params.json"),
            "--output-dir",
            str(cli_root),
            "--max-cores",
            "1",
            "--max-ram-gb",
            "4",
            "--planner",
            "heuristic",
            "--output-profile",
            "canonical",
        ]
        command(cli_args, out / "cli.log")
        inspect_image(spec["docker_image"], args.image)
        reports = list(cli_root.rglob("run_report.json"))
        require(len(reports) == 1, "CLI produced an unexpected report set")
        cli = read_json(reports[0])
        require(
            cli.get("status") == "executed"
            and len(cli["tools"]["completed"]) == 1
            and not cli["tools"]["failed"],
            "Core/CLI execution did not complete the tool",
        )
        resolved = list(cli_root.rglob("resolved.json"))
        require(len(resolved) == 1, "Core did not retain native provenance")
        native = read_json(resolved[0])
        require(
            native.get("temporal") == read_json(fixture / "temporal.json"),
            "Core changed the observed-time declaration",
        )
        require(
            len(native.get("steady_state_columns", [])) == 3,
            "Core did not deliver the independent steady-state matrix",
        )

        from fastapi.testclient import TestClient
        from andrea.gui.infer_network.server import create_app

        with TestClient(create_app()) as client:
            response = client.get("/api/infer-network/bootstrap")
            require(response.status_code == 200, "GUI bootstrap failed")
            bootstrap = response.json()
        gui_tool = [row for row in bootstrap["tools"] if row["tool_id"] == "dyngenie3"]
        require(
            len(gui_tool) == 1 and gui_tool[0]["spec"] == spec,
            "GUI did not expose the verified catalog contract",
        )
        write_json(out / "gui-catalog.json", {"status": "passed", "tool": gui_tool[0]})
        catalog_map = ROOT / "docs/assets/inference_tool_contract_map.svg"
        require(
            "dynGENIE3" in catalog_map.read_text(encoding="utf-8"),
            "Regenerated inference catalog map omits dynGENIE3",
        )

        validator_spec = importlib.util.spec_from_file_location(
            "integration_acceptance",
            ROOT / "wrappers/integration/validate_integration.py",
        )
        validator = importlib.util.module_from_spec(validator_spec)
        validator_spec.loader.exec_module(validator)
        dossier = copy.deepcopy(read_json(HERE / "integration.json"))
        for row in dossier["artifacts"] + dossier["evidence"]:
            row["path"] = str((HERE / row["path"]).resolve())
            require(
                sha(Path(row["path"])) == row["sha256"], "Source dossier hash changed"
            )
        dossier["installation"]["image"] = args.image
        dossier["limitations"] = [
            "Acceptance applies to the exact pinned image and finite calibration panel; independent gene-axis prediction evidence covers only default t8/s2 and the measured resource strata.",
            "Cross-temporal-shape prediction, irregular-grid costs, broad branch growth and unmeasured parameter interactions remain unsupported.",
            "Only explicitly declared observed longitudinal bulk series are supported; technical averaging, independent-sample pairing, pseudotime and native group/column networks are excluded.",
            "Directed unsigned target-normalized weights and exploratory quality diagnostics do not establish signed, causal or biologically accurate regulation.",
            "Historical source-audit and preparation evidence describes its pre-execution state; later immutable-image, interface and cost receipts establish the acceptance checks below.",
            "Software redistribution licensing remains unresolved in the pinned Python source; no registry publication is performed or certified.",
        ]
        additional = [
            ("cost", cost),
            ("calibration_plan", HERE / "cost_plan.json"),
            ("prediction_plan", prediction_dir / "prediction-plan.json"),
            ("prediction_report", prediction_path),
            ("estimator", prediction_dir / "estimator.json"),
        ]
        roles = {role for role, _ in additional}
        dossier["artifacts"] = [
            row for row in dossier["artifacts"] if row["role"] not in roles
        ]
        dossier["artifacts"].extend(
            reference(path, role=role) for role, path in additional
        )
        subjects = validator.subject_sets(dossier)
        context = validator.context_sha256(dossier)
        outputs = [
            reference(path)
            for path in (
                review_path,
                functional_path,
                out / "cost-coverage.json",
                prediction_path,
                prediction_dir / "observations.json",
                prediction_dir / "observed-provenance.json",
                out / "prediction-gate.json",
                reports[0],
                resolved[0],
                out / "gui-catalog.json",
                catalog_map,
                out / "validate_toolspecs.py.log",
                out / "validate_smoketest_configs.py.log",
                out / "cost-gates.log",
                out / "prediction-gate.log",
            )
        ]
        checks = {row["name"]: row for row in dossier["checks"]}
        for name in sorted(validator.CALIBRATED):
            check = checks.setdefault(name, {"name": name})
            receipt_path = out / "receipts" / f"{name}.json"
            covers = (
                sorted(spec["params"])
                if name == "parameter_defaults"
                else (
                    [
                        row["name"]
                        for row in dossier["entrypoints"]
                        if row["disposition"] == "included"
                    ]
                    if name == "functional_modes"
                    else []
                )
            )
            check.update(status="passed", covers=covers)
            check.pop("reason", None)
            receipt = {
                "status": "passed",
                "exit_code": 0,
                "kind": "inference",
                "id": "dyngenie3",
                "check": name,
                "covers": covers,
                "image": args.image,
                "context_sha256": context,
                "subjects": subjects[
                    (
                        "calibration_subjects"
                        if name.startswith("cost_")
                        else "functional_subjects"
                    )
                ],
                "command": "Review frozen scientific source and interface tests; verify physical campaign, independent cost report and actual core/CLI/GUI delivery.",
                "outputs": outputs,
            }
            write_json(receipt_path, receipt)
            check["receipt"] = reference(receipt_path)
        dossier["checks"] = list(checks.values())
        dossier_path = out / "integration.json"
        write_json(dossier_path, dossier)
        result = validator.validate(dossier_path, "calibrated")
        write_json(out / "gate.json", result)
        require(
            result["status"] == "passed",
            f"Acceptance dossier failed: {result['errors']}",
        )
        report.update(
            status="passed",
            scope="Declared finite calibration panel; gene-axis prediction evidence only at default t8/s2, separately by measured resource stratum.",
            dossier=reference(dossier_path),
            gate=reference(out / "gate.json"),
        )
        return_code = 0
    except Exception as exc:
        report.update(status="failed", error=str(exc))
        return_code = 2
    write_json(out / "acceptance.json", report)
    print(json.dumps(report, indent=2))
    return return_code


def stop(_signum, _frame):
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, stop)
    raise SystemExit(main())
