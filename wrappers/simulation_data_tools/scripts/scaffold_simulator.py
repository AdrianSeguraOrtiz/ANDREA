"""Scaffold files for a new simulation-data simulator integration."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Sequence

from shared.catalog_simulators import (
    DEFAULT_CATALOG_SIMULATORS_ROOT,
    DEFAULT_SMOKETEST_CONFIGS_ROOT,
    DEFAULT_WRAPPERS_ROOT,
    REPO_ROOT,
    expected_docker_image,
)

sys.path.insert(0, str(REPO_ROOT / "wrappers/integration"))
from scaffold_support import reviewed_draft

SIMULATOR_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
WRAPPER_RE = re.compile(r"^[a-z0-9][a-z0-9_.+-]*$")
WRAPPER_EXT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]*$")

WRAPPER_EXTENSION_MAP = {
    "python": "py",
    "r": "R",
    "bash": "sh",
    "sh": "sh",
    "shell": "sh",
}

RUNTIME_ENTRYPOINTS = {
    "python": ("python3", "/app/run_simulator.py"),
    "r": ("Rscript", "/app/run_simulator.R"),
    "bash": ("bash", "/app/run_simulator.sh"),
    "sh": ("bash", "/app/run_simulator.sh"),
    "shell": ("bash", "/app/run_simulator.sh"),
}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create scaffold files for a new simulator integration."
    )
    parser.add_argument(
        "--simulator",
        required=True,
        help="Stable simulator id (lowercase, digits, underscore, hyphen).",
    )
    parser.add_argument(
        "--wrapper",
        default="python",
        help="Initial wrapper language scaffold. Known templates: python, r, bash.",
    )
    parser.add_argument(
        "--wrapper-ext",
        default=None,
        help="Override wrapper script extension.",
    )
    parser.add_argument(
        "--wrappers-root",
        type=Path,
        default=DEFAULT_WRAPPERS_ROOT,
        help=f"Path to simulator wrapper directories. Default: {DEFAULT_WRAPPERS_ROOT}",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        help="Untracked candidate files; default: release-logs/simulation-<simulator>.",
    )
    parser.add_argument(
        "--catalog-simulators-root",
        type=Path,
        default=DEFAULT_CATALOG_SIMULATORS_ROOT,
        help=f"Path to catalog simulators directory. Default: {DEFAULT_CATALOG_SIMULATORS_ROOT}",
    )
    parser.add_argument(
        "--smoketest-configs-root",
        type=Path,
        default=DEFAULT_SMOKETEST_CONFIGS_ROOT,
        help=f"Path to smoketest config directory. Default: {DEFAULT_SMOKETEST_CONFIGS_ROOT}",
    )
    parser.add_argument(
        "--activate-catalog",
        action="store_true",
        help="Promote an existing reviewed SimulatorSpec draft after schema and identity checks.",
    )
    parser.add_argument(
        "--activate-smoketest",
        action="store_true",
        help="Promote an existing reviewed smoketest draft after schema and identity checks.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite files created by this scaffold.",
    )
    return parser.parse_args(argv)


def write_file(path: Path, content: str, *, force: bool) -> str:
    if path.exists() and not force:
        return "exists"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return "written"


def write_json(path: Path, payload: dict[str, object], *, force: bool) -> str:
    return write_file(
        path,
        json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
        force=force,
    )


def dockerfile_template(wrapper: str, script_name: str) -> str:
    runtime_bin, _app_script = RUNTIME_ENTRYPOINTS.get(
        wrapper,
        ("bash", f"/app/{script_name}"),
    )
    app_script = f"/app/{script_name}"
    return f"""FROM ubuntu:24.04

RUN apt-get update \\
    && apt-get install -y --no-install-recommends ca-certificates python3 r-base \\
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# TODO: verify preferred installation; pin base digest, public upstream and dependencies.
# The local repo/ and papers/ are evidence only and must not be copied or mounted.
COPY wrappers/simulation_data_tools/simulators/{{simulator_id}}/{script_name} /app/{script_name}

ENTRYPOINT ["{runtime_bin}", "{app_script}", "--request", "/work/request/simulator-run-request.json", "--output-dir", "/work/out"]
"""


def wrapper_template(wrapper: str) -> str:
    if wrapper == "python":
        return '''"""TODO: implement simulator wrapper.

The wrapper must read --request and --output-dir, then write:
- expression.tsv
- extras/tf_list.txt
- truth/networks.csv
- truth/gene_universe.txt
- simulator-output-manifest.json
- progress.json

Every accepted request must include tf_list in effective_extras. The wrapper
must reject the request before starting the simulator when that invariant is
not satisfied.
"""

from __future__ import annotations

import argparse


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--output-dir", required=True)
    _args = parser.parse_args()
    raise NotImplementedError("Implement simulator wrapper")


if __name__ == "__main__":
    raise SystemExit(main())
'''
    if wrapper == "r":
        return """#!/usr/bin/env Rscript

args <- commandArgs(trailingOnly = TRUE)
stop("TODO: implement simulator wrapper")
"""
    return """#!/usr/bin/env bash
set -euo pipefail
echo "TODO: implement simulator wrapper" >&2
exit 1
"""


def spec_payload(simulator_id: str) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "id": simulator_id,
        "name": simulator_id,
        "publication": ["TODO_PUBLICATION_URL"],
        "first_author": "TODO",
        "year": None,
        "simulation_summary": "TODO: describe simulator scope and assumptions.",
        "simulation_keywords": ["todo"],
        "implementation_url": "TODO_IMPLEMENTATION_URL",
        "docker_image": expected_docker_image(simulator_id),
        "extra_inputs": {
            "required": [],
            "optional": [],
            "conditional_required": [],
        },
        "compatibility_rules": [],
        "runtime_resources": {
            "threading": {
                "supported": False,
                "default_threads": 1,
                "max_threads": 1,
                "upstream_mapping": "No upstream threading control is declared.",
            }
        },
        "capabilities": [
            {
                "data_axes": {
                    "measurement": "rna_expression",
                    "resolution": "single_cell",
                    "column_kind": "cells",
                    "experimental_design": "observational",
                },
                "truth_requirements": {
                    "contexts": ["global"],
                },
                "parameter_bindings": [],
                "native_extras": ["tf_list"],
                "derivable_extras": [],
                "truth_outputs": [
                    {
                        "context": "global",
                        "status": "native",
                    }
                ],
                "truth_contexts": [
                    {
                        "context": "global",
                        "status": "native",
                        "source_artifacts": ["TODO_NATIVE_GRN"],
                        "upstream_configuration": ["TODO_UPSTREAM_SWITCH"],
                        "generation": "TODO: describe how global truth rows are produced.",
                        "score_semantics": "TODO: describe score and sign semantics.",
                        "limitations": ["TODO: describe truth limitations."],
                    },
                    {
                        "context": "group",
                        "status": "none",
                        "explanation": "TODO: explain why group truth is unavailable.",
                    },
                    {
                        "context": "column",
                        "status": "none",
                        "explanation": "TODO: explain why column-level truth is unavailable.",
                    },
                ],
                "truth_parameter_requirements": [],
                "derivations": [],
                "native_outputs": [],
                "artifacts_aux": [],
            }
        ],
        "params": {},
    }


def smoketest_payload(simulator_id: str) -> dict[str, object]:
    return {
        "simulator_id": simulator_id,
        "request": {
            "data_axes": {
                "measurement": "rna_expression",
                "resolution": "single_cell",
                "column_kind": "cells",
                "experimental_design": "observational",
            },
            "truth_requirements": {
                "contexts": ["global"],
            },
            "seed": 1,
            "effective_extras": ["tf_list"],
            "inputs": {},
            "params": {},
            "runtime_resources": {"threads": 1},
        },
        "expect_progress": True,
        "required_files": [
            "expression.tsv",
            "extras/tf_list.txt",
            "truth/networks.csv",
            "truth/gene_universe.txt",
            "simulator-output-manifest.json",
            "progress.json",
        ],
        "required_truth_context_prefixes": ["global"],
    }


def integration_decisions_template(simulator_id: str) -> str:
    template = (REPO_ROOT / "wrappers/integration/decisions.template.md").read_text(
        encoding="utf-8"
    )
    values = {
        "id": simulator_id,
        "kind": "simulation",
        "evidence_path": f"wrappers/simulation_data_tools/simulators/{simulator_id}",
        "contract_name": "simulatorspec.json",
        "specific_decisions": """Audit supported `data_axes` / `truth_requirements`
combinations and excluded combinations with evidence. Record native versus
derivable extras and truth, derivation assumptions, simulator-side input specs
and conditional rules, parameter bindings and progress strategy. Every accepted
request/capability must support `tf_list` and always emit `extras/tf_list.txt`.
Preserve gene, expression-column and group identifiers; export truth through
`truth/networks.csv` with `global`, `group:<id>` and `column:<id>` contexts only
when justified. `column` follows `data_axes.column_kind`, not necessarily cells.
Document native source artifacts, upstream configurations, score/direction/sign
semantics and limitations for every truth context. Record the actual mapping
of request `runtime_resources.threads` to public upstream controls.""",
    }
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", value)
    return template


def draft_source(staged: Path, legacy: Path) -> Path:
    if (
        staged.is_file()
        and legacy.is_file()
        and staged.read_bytes() != legacy.read_bytes()
    ):
        raise RuntimeError(f"Conflicting candidate copies: {staged} and {legacy}")
    return staged if staged.is_file() or not legacy.is_file() else legacy


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    simulator_id = args.simulator.strip()
    wrapper = args.wrapper.strip().lower()
    wrapper_ext = args.wrapper_ext or WRAPPER_EXTENSION_MAP.get(wrapper, wrapper)

    if not SIMULATOR_ID_RE.fullmatch(simulator_id):
        print(f"ERROR: invalid simulator id: {simulator_id}", file=sys.stderr)
        return 2
    if not WRAPPER_RE.fullmatch(wrapper):
        print(f"ERROR: invalid wrapper name: {wrapper}", file=sys.stderr)
        return 2
    if not WRAPPER_EXT_RE.fullmatch(wrapper_ext):
        print(f"ERROR: invalid wrapper extension: {wrapper_ext}", file=sys.stderr)
        return 2

    wrapper_dir = args.wrappers_root / simulator_id
    script_name = f"run_simulator.{wrapper_ext}"
    staging = (
        args.results_dir or REPO_ROOT / "release-logs" / f"simulation-{simulator_id}"
    )
    if any(
        staging.resolve().is_relative_to(root.resolve())
        for root in (
            args.wrappers_root,
            args.catalog_simulators_root,
            args.smoketest_configs_root,
        )
    ):
        print(
            "ERROR: --results-dir must be outside wrapper and active catalog/config directories.",
            file=sys.stderr,
        )
        return 2
    draft_spec = staging / "simulatorspec.draft.json"
    draft_smoke = staging / "smoketest.config.draft.json"
    if args.activate_catalog or args.activate_smoketest:
        promotions = []
        try:
            if args.activate_catalog:
                promotions.append(
                    (
                        args.catalog_simulators_root
                        / simulator_id
                        / "simulatorspec.json",
                        draft_source(
                            draft_spec, wrapper_dir / "simulatorspec.draft.json"
                        ),
                        reviewed_draft(
                            draft_source(
                                draft_spec, wrapper_dir / "simulatorspec.draft.json"
                            ),
                            schema_path=REPO_ROOT
                            / "andrea/catalog_simulation_data_tools/schemas/simulatorspec.schema.json",
                            expected_id=simulator_id,
                        ),
                    )
                )
            if args.activate_smoketest:
                promotions.append(
                    (
                        args.smoketest_configs_root / f"{simulator_id}_basic.json",
                        draft_source(
                            draft_smoke, wrapper_dir / "smoketest.config.draft.json"
                        ),
                        reviewed_draft(
                            draft_source(
                                draft_smoke, wrapper_dir / "smoketest.config.draft.json"
                            ),
                            schema_path=REPO_ROOT
                            / "wrappers/simulation_data_tools/tests/schemas/smoketest.config.schema.json",
                            expected_id=simulator_id,
                            id_field="simulator_id",
                        ),
                    )
                )
        except (RuntimeError, OSError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        for path, source, content in promotions:
            print(f"[{write_file(path, content, force=args.force)}] {path}")
            for candidate in (source, wrapper_dir / source.name):
                if (
                    candidate.is_file()
                    and path.is_file()
                    and candidate.read_bytes() == path.read_bytes()
                ):
                    candidate.unlink()
        print(
            "Reviewed drafts promoted; functional verification, calibration and publication remain separate gates."
        )
        return 0
    # Evidence directories are never installation/runtime dependencies.
    (wrapper_dir / "papers").mkdir(parents=True, exist_ok=True)
    (wrapper_dir / "repo").mkdir(parents=True, exist_ok=True)

    outputs: list[tuple[Path, str]] = []
    dockerfile = dockerfile_template(wrapper, script_name).replace(
        "{simulator_id}", simulator_id
    )
    outputs.append(
        (
            wrapper_dir / "Dockerfile",
            write_file(wrapper_dir / "Dockerfile", dockerfile, force=args.force),
        )
    )
    outputs.append(
        (
            wrapper_dir / script_name,
            write_file(
                wrapper_dir / script_name, wrapper_template(wrapper), force=args.force
            ),
        )
    )
    outputs.append(
        (
            wrapper_dir / "integration_decisions.md",
            write_file(
                wrapper_dir / "integration_decisions.md",
                integration_decisions_template(simulator_id),
                force=args.force,
            ),
        )
    )

    if (
        args.force
        or not (
            args.catalog_simulators_root / simulator_id / "simulatorspec.json"
        ).is_file()
    ):
        outputs.append(
            (
                draft_spec,
                write_json(
                    draft_spec,
                    spec_payload(simulator_id),
                    force=args.force,
                ),
            )
        )

    if (
        args.force
        or not (args.smoketest_configs_root / f"{simulator_id}_basic.json").is_file()
    ):
        outputs.append(
            (
                draft_smoke,
                write_json(
                    draft_smoke,
                    smoketest_payload(simulator_id),
                    force=args.force,
                ),
            )
        )

    for path, status in outputs:
        print(f"[{status}] {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
