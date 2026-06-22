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
    expected_docker_image,
)

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
    "python": ("python", "/app/run_simulator.py"),
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
        help="Write an active catalog simulatorspec.json. By default only a draft is created in the wrapper dir.",
    )
    parser.add_argument(
        "--activate-smoketest",
        action="store_true",
        help="Write an active smoketest config. By default only a draft is created in the wrapper dir.",
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
    runtime_bin, app_script = RUNTIME_ENTRYPOINTS.get(
        wrapper,
        ("bash", f"/app/{script_name}"),
    )
    return f"""FROM ubuntu:24.04

RUN apt-get update \\
    && apt-get install -y --no-install-recommends ca-certificates python3 r-base \\
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY wrappers/simulation_data_tools/simulators/{{simulator_id}}/{script_name} /app/{script_name}

ENTRYPOINT ["{runtime_bin}", "{app_script}", "--request", "/work/request/simulator-run-request.json", "--output-dir", "/work/out"]
"""


def wrapper_template(wrapper: str) -> str:
    if wrapper == "python":
        return '''"""TODO: implement simulator wrapper.

The wrapper must read --request and --output-dir, then write:
- expression.tsv
- truth/networks.csv
- truth/gene_universe.txt
- simulator-output-manifest.json
- progress.json
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
        "year": 2026,
        "simulation_summary": "TODO: describe simulator scope and assumptions.",
        "simulation_keywords": ["todo"],
        "implementation_url": "TODO_IMPLEMENTATION_URL",
        "docker_image": expected_docker_image(simulator_id),
        "extra_inputs": {
            "required": [],
            "optional": [],
            "conditional_required": [],
        },
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
                "native_extras": [],
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
            "effective_extras": [],
            "inputs": {},
            "params": {},
            "runtime_resources": {"threads": 1},
        },
        "expect_progress": True,
        "required_files": [
            "expression.tsv",
            "truth/networks.csv",
            "truth/gene_universe.txt",
            "simulator-output-manifest.json",
            "progress.json",
        ],
        "required_truth_context_prefixes": ["global"],
    }


def wrapper_readme_template(simulator_id: str) -> str:
    return f"""# {simulator_id}

TODO: document installation, wrapper behavior and smoke tests.

## Runtime Contract

- The container reads `/work/request/simulator-run-request.json`.
- The container writes normalized outputs directly under `/work/out/`.
- The wrapper must write `progress.json`.
- All public truth networks are exported through `truth/networks.csv`; the
  `context` column determines whether an edge belongs to global, group or
  column-level truth.
- `group:<id>` groups expression columns and `column:<id>` refers to the
  expression column semantics declared by `data_axes.column_kind`; only
  `column_kind=cells` makes it cell-level truth.
- `simulator-output-manifest.json` must report `truth.gene_universe` and
  `truth.networks`.
- Simulators may preserve native outputs under provenance, but public consumers
  must not depend on those native files.
- The wrapper must map `runtime_resources.threads` to the upstream public
  thread/worker control declared in `simulatorspec.json`.
- Do not expose thread counts as simulator params.

## Simulator Inputs

Document every simulator-side input file declared in `simulatorspec.extra_inputs`.
File semantics, format, examples and basic validation live in
`andrea/catalog_simulation_data_tools/input_specs/<input_id>.json`.

- required inputs
- optional inputs
- conditional inputs and the params/extras/data-axis values that require them

Generated extras are not simulator-side inputs. Do not put formats or examples
inline in `extra_inputs`; create or reuse an input spec instead.

## Cost Profiles

After smoke tests pass, add a bounded benchmark matrix under
`wrappers/simulation_data_tools/cost_profiles/{simulator_id}.json` and generate
the catalog `cost.json` with:

```bash
make benchmark-simulator-costs ARGS="--simulator {simulator_id}"
make validate-simulator-costs ARGS="--simulator {simulator_id} --require"
```
"""


def integration_decisions_template(simulator_id: str) -> str:
    return f"""# {simulator_id} Integration Decisions

TODO: record paper/repo review and wrapper decisions.

## Required Decisions

- upstream evidence reviewed
- selected installation route and version/tag/commit
- selected public API/CLI entrypoint
- supported semantic capabilities and intentionally unsupported
  `data_axes` / `truth_requirements` combinations
- native and derivable extras
- simulator-side input specs and `extra_inputs` usage/conditional rules
- parameter mapping and unsupported function-valued hooks
- `runtime_resources.threading` contract and request `runtime_resources.threads`
  mapping to upstream controls
- output mapping, including `global`, `group:<id>` and `column:<id>` truth
  contexts when supported
- progress strategy
- smoke-test matrix by data axes, truth contexts, extras and outcome
- cost profile status and ETA fallback/cost behavior
"""


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    simulator_id = args.simulator.strip()
    wrapper = args.wrapper.strip().lower()
    wrapper_ext = args.wrapper_ext or WRAPPER_EXTENSION_MAP.get(wrapper, wrapper)

    if not SIMULATOR_ID_RE.match(simulator_id):
        print(f"ERROR: invalid simulator id: {simulator_id}", file=sys.stderr)
        return 2
    if not WRAPPER_RE.match(wrapper):
        print(f"ERROR: invalid wrapper name: {wrapper}", file=sys.stderr)
        return 2
    if not WRAPPER_EXT_RE.match(wrapper_ext):
        print(f"ERROR: invalid wrapper extension: {wrapper_ext}", file=sys.stderr)
        return 2

    wrapper_dir = args.wrappers_root / simulator_id
    script_name = f"run_simulator.{wrapper_ext}"

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
            wrapper_dir / "README.md",
            write_file(
                wrapper_dir / "README.md",
                wrapper_readme_template(simulator_id),
                force=args.force,
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

    draft_spec = wrapper_dir / "simulatorspec.draft.json"
    active_spec = args.catalog_simulators_root / simulator_id / "simulatorspec.json"
    outputs.append(
        (
            active_spec if args.activate_catalog else draft_spec,
            write_json(
                active_spec if args.activate_catalog else draft_spec,
                spec_payload(simulator_id),
                force=args.force,
            ),
        )
    )

    draft_smoke = wrapper_dir / "smoketest.config.draft.json"
    active_smoke = args.smoketest_configs_root / f"{simulator_id}_basic.json"
    outputs.append(
        (
            active_smoke if args.activate_smoketest else draft_smoke,
            write_json(
                active_smoke if args.activate_smoketest else draft_smoke,
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
