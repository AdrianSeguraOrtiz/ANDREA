"""Scaffold files for a new infer-network tool integration.

Usage examples:
1) Create a Python-wrapper scaffold:
   python scaffold_tool.py --tool mytool

2) Create an R-wrapper scaffold:
   python scaffold_tool.py --tool mytool --wrapper r

3) Create a generic scaffold for another language:
   python scaffold_tool.py --tool mytool --wrapper matlab

4) Override the file extension for a generic scaffold:
   python scaffold_tool.py --tool mytool --wrapper matlab --wrapper-ext m

5) Overwrite existing scaffold files:
   python scaffold_tool.py --tool mytool --force
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "wrappers/integration"))
from scaffold_support import reviewed_draft

INFERENCE_TOOLS_ROOT = REPO_ROOT / "wrappers" / "inference_tools"
TOOLS_ROOT = INFERENCE_TOOLS_ROOT / "tools"
CATALOG_TOOLS_ROOT = REPO_ROOT / "andrea" / "catalog_inference_tools" / "tools"
SMOKETEST_CONFIGS_ROOT = INFERENCE_TOOLS_ROOT / "tests" / "smoketest_configs"

TOOL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
WRAPPER_RE = re.compile(r"^[a-z0-9][a-z0-9_.+-]*$")
WRAPPER_EXT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]*$")

WRAPPER_EXTENSION_MAP = {
    "python": "py",
    "r": "R",
    "java": "java",
    "julia": "jl",
    "matlab": "m",
    "octave": "m",
    "bash": "sh",
    "sh": "sh",
    "shell": "sh",
}

WRAPPER_COMMENT_PREFIX_MAP = {
    "python": "#",
    "r": "#",
    "java": "//",
    "julia": "#",
    "matlab": "%",
    "octave": "%",
    "bash": "#",
    "sh": "#",
    "shell": "#",
}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create scaffold files for a new inference tool integration."
    )
    parser.add_argument(
        "--tool",
        required=True,
        help="Stable tool identifier (lowercase, digits, underscore, hyphen).",
    )
    parser.add_argument(
        "--wrapper",
        default="python",
        help=(
            "Initial wrapper language scaffold. "
            "Known tailored templates: python, r. "
            "Any other value creates a generic placeholder scaffold."
        ),
    )
    parser.add_argument(
        "--wrapper-ext",
        help=(
            "Optional file extension override for generic wrapper languages. "
            "Example: --wrapper matlab --wrapper-ext m"
        ),
    )
    parser.add_argument(
        "--tool-sources-root",
        type=Path,
        default=TOOLS_ROOT,
        help="Path to local wrapper/evidence directories.",
    )
    parser.add_argument(
        "--catalog-tools-root",
        type=Path,
        default=CATALOG_TOOLS_ROOT,
        help="Path to active ToolSpec catalog directories.",
    )
    parser.add_argument(
        "--smoketest-configs-root",
        type=Path,
        default=SMOKETEST_CONFIGS_ROOT,
        help="Path to active inference smoketest configurations.",
    )
    parser.add_argument(
        "--activate-catalog",
        action="store_true",
        help="Promote an existing reviewed ToolSpec draft after schema and identity checks.",
    )
    parser.add_argument(
        "--activate-smoketest",
        action="store_true",
        help="Promote an existing reviewed smoketest draft after schema checks.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing scaffold files.",
    )
    return parser.parse_args(argv)


def ensure_tool_id(tool_id: str) -> str:
    normalized = tool_id.strip()
    if not normalized or TOOL_ID_RE.fullmatch(normalized) is None:
        raise RuntimeError(
            "Invalid --tool value. Expected pattern: ^[a-z0-9][a-z0-9_-]*$"
        )
    return normalized


def ensure_wrapper(wrapper: str) -> str:
    normalized = wrapper.strip().lower()
    if not normalized or WRAPPER_RE.fullmatch(normalized) is None:
        raise RuntimeError(
            "Invalid --wrapper value. Expected pattern: ^[a-z0-9][a-z0-9_.+-]*$"
        )
    return normalized


def resolve_wrapper_extension(wrapper: str, wrapper_ext: str | None) -> str:
    if wrapper_ext is not None:
        normalized = wrapper_ext.strip().lstrip(".")
        if not normalized or WRAPPER_EXT_RE.fullmatch(normalized) is None:
            raise RuntimeError(
                "Invalid --wrapper-ext value. Expected pattern: "
                "^[A-Za-z0-9][A-Za-z0-9_.+-]*$"
            )
        return normalized

    mapped = WRAPPER_EXTENSION_MAP.get(wrapper)
    if mapped is not None:
        return mapped

    fallback = re.sub(r"[^A-Za-z0-9]+", "_", wrapper).strip("_")
    return fallback or "txt"


def _write_text_file(path: Path, content: str, *, force: bool) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    existed_before = path.exists()
    if existed_before and not force:
        return "skipped"
    path.write_text(content, encoding="utf-8")
    return "updated" if existed_before else "created"


def toolspec_template(tool_id: str) -> str:
    payload = {
        "schema_version": "1.0",
        "id": tool_id,
        "name": tool_id,
        "publication": ["https://doi.org/TODO"],
        "first_author": "TODO",
        "year": None,
        "method_summary": "TODO: replace with a short 1-2 sentence summary of the method core.",
        "method_family": "TODO",
        "method_keywords": ["todo_keyword"],
        "implementation_url": "https://example.org/TODO",
        "docker_image": f"TODO/{tool_id}:TODO_PINNED_VERSION",
        "execution_capabilities": ["global"],
        "runtime_resources": {
            "threading": {
                "supported": False,
                "default_threads": 1,
                "max_threads": 1,
                "upstream_mapping": "TODO: document how wrapper --threads maps to upstream parallelism, or state that upstream exposes no parallel runtime control.",
            }
        },
        "taxonomic_scope": {
            "allowed_groups": [
                "animal",
                "plant",
                "fungi",
                "bacteria",
                "archaea",
                "protist",
                "viral",
                "synthetic",
                "unknown",
            ],
            "supported_species": [],
        },
        "compatibility_rules": [],
        "accepts": ["samples"],
        "assumes": "generic",
        "extra_inputs": {
            "required": [],
            "optional": [],
            "conditional_required": [],
        },
        "outputs": {
            "directed": False,
            "sign": "none",
            "evidence": "association",
        },
        "progress": {
            "kind": "none",
            "note": "TODO: replace with actual progress semantics if the wrapper exposes progress.json updates.",
        },
        "params": {},
        "artifacts_aux": [],
    }
    return json.dumps(payload, indent=2, ensure_ascii=True) + "\n"


def integration_decisions_template(tool_id: str) -> str:
    template = (REPO_ROOT / "wrappers/integration/decisions.template.md").read_text(
        encoding="utf-8"
    )
    values = {
        "id": tool_id,
        "kind": "inference",
        "evidence_path": f"wrappers/inference_tools/tools/{tool_id}",
        "contract_name": "toolspec.json",
        "specific_decisions": """Audit `execution_capabilities` (`global`, `group_native`,
`group_emulated`, `column_native`, `group_aggregated`) against the actual
upstream entrypoints. Do not claim column-specific inference by rerunning a
global method per column unless upstream defines it as scientifically valid.
Record `accepts`, `assumes`, taxonomic restrictions, compatibility rules,
required/optional/conditional inputs, output semantics and progress evidence.
Reuse normalized input specs. Preserve public gene, expression-column and group
identifiers in `network.csv`. Document `global`, `group:<id>`, `column:<id>` or
other documented non-empty context families and their scientific meaning.
Record exact `--threads` behavior including native libraries or documented
wrapper sharding; unsupported threading remains explicitly unsupported.""",
    }
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", value)
    return template


def integration_payload(tool_id: str) -> dict[str, object]:
    template_path = REPO_ROOT / "wrappers/integration/integration.template.json"
    payload = json.loads(template_path.read_text(encoding="utf-8"))
    payload.update(kind="inference", id=tool_id)
    return payload


def smoketest_config_template() -> str:
    payload = {
        "execution": {"mode": "global"},
        "extra_files": [],
        "require_progress": True,
        "checks": {},
    }
    return json.dumps(payload, indent=2, ensure_ascii=True) + "\n"


def run_tool_python_template(tool_id: str) -> str:
    return f'''"""
Scaffold wrapper for {tool_id}.

TODO:
- resolve and validate runtime params
- load normalized inputs from /io/expression.tsv and /io/extra/*
- run the upstream method
- write /io/out/network.csv and /io/out/progress.json
"""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--params", required=True, type=Path)
    parser.add_argument("--extra", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--threads", required=True, type=int)
    _args = parser.parse_args()
    raise NotImplementedError("TODO: implement the wrapper for {tool_id}")


if __name__ == "__main__":
    main()
'''


def run_tool_r_template(tool_id: str) -> str:
    return f"""# Scaffold wrapper for {tool_id}
#
# TODO:
# - parse commandArgs()
# - load normalized inputs from /io/expression.tsv and /io/extra/*
# - run the upstream method
# - write /io/out/network.csv and /io/out/progress.json

stop(\"TODO: implement the wrapper for {tool_id}\", call. = FALSE)
"""


def run_tool_generic_template(tool_id: str, wrapper: str) -> str:
    comment_prefix = WRAPPER_COMMENT_PREFIX_MAP.get(wrapper, "#")
    lines = [
        f"Scaffold wrapper for {tool_id} ({wrapper}).",
        "",
        "TODO:",
        "- decide the real public installation/runtime strategy",
        "- parse ANDREA runtime arguments",
        "- load normalized inputs from /io/expression.tsv and /io/extra/*",
        "- run the upstream method",
        "- write /io/out/network.csv and /io/out/progress.json",
        "",
        "This generic stub exists because scaffold_tool.py currently ships",
        "language-specific templates only for Python and R.",
    ]
    rendered = []
    for line in lines:
        if line:
            rendered.append(f"{comment_prefix} {line}")
        else:
            rendered.append(comment_prefix)
    return "\n".join(rendered) + "\n"


def dockerfile_template(tool_id: str, wrapper: str, wrapper_name: str) -> str:
    if wrapper == "r":
        return f"""FROM rocker/r-ver:4.4.1

WORKDIR /app

# TODO: verify installation preference, pin base digest and public upstream/dependencies.
# Install {tool_id} from a public immutable source, never COPY repo/ or papers/.
COPY {wrapper_name} /app/{wrapper_name}

ENTRYPOINT ["Rscript", "/app/{wrapper_name}"]
"""

    if wrapper == "python":
        return f"""FROM python:3.13-slim

WORKDIR /app

# TODO: verify installation preference, pin base digest and public upstream/dependencies.
# Install {tool_id} from a public immutable source, never COPY repo/ or papers/.
COPY {wrapper_name} /app/{wrapper_name}

ENTRYPOINT ["python", "/app/{wrapper_name}"]
"""

    return f"""FROM debian:bookworm-slim

WORKDIR /app

# TODO: verify installation preference, pin base digest and public upstream/dependencies.
# Install {tool_id} ({wrapper}) publicly; never COPY repo/ or papers/.
COPY {wrapper_name} /app/{wrapper_name}

CMD ["/bin/sh", "-lc", "echo 'TODO: replace the generic scaffold ENTRYPOINT for {tool_id} ({wrapper})' >&2; exit 1"]
"""


def scaffold(
    tool_id: str,
    wrapper: str,
    wrapper_ext: str,
    *,
    force: bool,
    tool_sources_root: Path = TOOLS_ROOT,
    catalog_tools_root: Path = CATALOG_TOOLS_ROOT,
    smoketest_configs_root: Path = SMOKETEST_CONFIGS_ROOT,
    activate_catalog: bool = False,
    activate_smoketest: bool = False,
) -> list[tuple[Path, str]]:
    tool_root = tool_sources_root / tool_id
    catalog_root = catalog_tools_root / tool_id
    if activate_catalog or activate_smoketest:
        # Validate every selected draft before writing anything. Activation
        # never regenerates wrappers or overwrites evidence, even with --force.
        promotions = []
        if activate_catalog:
            promotions.append(
                (
                    catalog_root / "toolspec.json",
                    reviewed_draft(
                        tool_root / "toolspec.draft.json",
                        schema_path=REPO_ROOT
                        / "andrea/catalog_inference_tools/schemas/toolspec.schema.json",
                        expected_id=tool_id,
                    ),
                )
            )
        if activate_smoketest:
            promotions.append(
                (
                    smoketest_configs_root / f"{tool_id}.json",
                    reviewed_draft(
                        tool_root / "smoketest.config.draft.json",
                        schema_path=INFERENCE_TOOLS_ROOT
                        / "tests/schemas/smoketest.config.schema.json",
                    ),
                )
            )
        return [
            (path, _write_text_file(path, content, force=force))
            for path, content in promotions
        ]
    spec_path = tool_root / "toolspec.draft.json"
    smoketest_path = tool_root / "smoketest.config.draft.json"
    wrapper_name = f"run_tool.{wrapper_ext}"
    if wrapper == "r":
        wrapper_content = run_tool_r_template(tool_id)
    elif wrapper == "python":
        wrapper_content = run_tool_python_template(tool_id)
    else:
        wrapper_content = run_tool_generic_template(tool_id, wrapper)

    created: list[tuple[Path, str]] = []
    for directory in (
        tool_root,
        tool_root / "repo",
        tool_root / "papers",
    ):
        directory.mkdir(parents=True, exist_ok=True)

    file_specs = [
        (
            spec_path,
            toolspec_template(tool_id),
        ),
        (
            tool_root / "integration_decisions.md",
            integration_decisions_template(tool_id),
        ),
        (
            tool_root / "integration.json",
            json.dumps(integration_payload(tool_id), indent=2, ensure_ascii=True)
            + "\n",
        ),
        (
            tool_root / ".dockerignore",
            "**/repo\n**/papers\n**/.git\n**/.venv\n**/__pycache__\n",
        ),
        (
            tool_root / "Dockerfile",
            dockerfile_template(tool_id, wrapper, wrapper_name),
        ),
        (
            tool_root / wrapper_name,
            wrapper_content,
        ),
        (
            smoketest_path,
            smoketest_config_template(),
        ),
    ]

    for path, content in file_specs:
        status = _write_text_file(path, content, force=force)
        created.append((path, status))

    return created


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        tool_id = ensure_tool_id(args.tool)
        wrapper = ensure_wrapper(args.wrapper)
        wrapper_ext = resolve_wrapper_extension(wrapper, args.wrapper_ext)
        created = scaffold(
            tool_id,
            wrapper,
            wrapper_ext,
            force=args.force,
            tool_sources_root=args.tool_sources_root,
            catalog_tools_root=args.catalog_tools_root,
            smoketest_configs_root=args.smoketest_configs_root,
            activate_catalog=args.activate_catalog,
            activate_smoketest=args.activate_smoketest,
        )
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(
        f"[{tool_id}] scaffold ready; scientific contract, implementation and verification pending"
    )
    for path, status in created:
        print(f"  {status.upper():7s} {path}")
    if args.activate_catalog or args.activate_smoketest:
        print(
            "Reviewed draft promotion only; complete the remaining evidence gates independently."
        )
        return 0
    print()
    print("Next steps:")
    print(
        f"  1. Supply article TXT in {args.tool_sources_root / tool_id / 'papers'} and cloned evidence in {args.tool_sources_root / tool_id / 'repo'}"
    )
    print(
        "  2. Record installation preference in integration_decisions.md; PDF is optional."
    )
    print(
        f"  3. Continue autonomously through {REPO_ROOT / 'docs/INTEGRATION_WORKFLOW.md'} and the inference playbook"
    )
    print(
        f"  4. When implementation is ready, verify with: make verify-tool TOOL={tool_id}"
    )
    if wrapper not in {"python", "r"}:
        print()
        print(
            f"Note: {wrapper!r} currently uses a generic scaffold placeholder "
            f"({TOOLS_ROOT / tool_id / f'run_tool.{wrapper_ext}'})."
        )
        print(
            "Replace the generic wrapper and Dockerfile during integration with "
            "the real runtime strategy for that language."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
