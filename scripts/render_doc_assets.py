#!/usr/bin/env python3
"""Render documentation assets from ANDREA catalog and documentation sources.

This script intentionally depends only on tracked project files.  It must keep
working if git-ignored workspaces are removed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs/assets"
SIMULATOR_DIR = ROOT / "andrea/catalog_simulation_data_tools/simulators"
TOOL_DIR = ROOT / "andrea/catalog_inference_tools/tools"
DOC_FIGURE_DIR = ROOT / "scripts/doc_assets"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_simulators() -> list[dict]:
    specs = []
    for path in sorted(SIMULATOR_DIR.glob("*/simulatorspec.json")):
        spec = _read_json(path)
        spec["_path_id"] = path.parent.name
        specs.append(spec)
    return specs


def _load_tools() -> list[dict]:
    specs = []
    for path in sorted(TOOL_DIR.glob("*/toolspec.json")):
        spec = _read_json(path)
        spec["_path_id"] = path.parent.name
        specs.append(spec)
    return specs


def _run(command: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=cwd or ROOT, check=True)


def render_overview_png() -> None:
    """Render Figure 1 as a README-friendly static PNG."""
    tikz = (DOC_FIGURE_DIR / "andrea_overview.tex").read_text(encoding="utf-8")
    tikz = tikz.replace("__ANDREA_SIMULATOR_COUNT__", str(len(_load_simulators())))
    tikz = tikz.replace("__ANDREA_INFERENCE_TOOL_COUNT__", str(len(_load_tools())))

    with tempfile.TemporaryDirectory(prefix="andrea-doc-fig1-") as tmp:
        tmp_dir = Path(tmp)
        tex_path = tmp_dir / "andrea_overview.tex"
        tex_path.write_text(
            "\n".join(
                [
                    r"\documentclass[tikz,border=4pt]{standalone}",
                    r"\usepackage[T1]{fontenc}",
                    r"\usepackage{tikz}",
                    r"\usetikzlibrary{arrows.meta,backgrounds,calc,fit}",
                    r"\begin{document}",
                    tikz,
                    r"\end{document}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        _run(
            [
                "pdflatex",
                "-interaction=nonstopmode",
                "-halt-on-error",
                tex_path.name,
            ],
            cwd=tmp_dir,
        )
        _run(
            [
                "pdftoppm",
                "-png",
                "-singlefile",
                "-r",
                "180",
                "andrea_overview.pdf",
                "andrea_overview",
            ],
            cwd=tmp_dir,
        )
        shutil.copyfile(
            tmp_dir / "andrea_overview.png", OUT_DIR / "andrea_overview.png"
        )


def render_catalog_figures(output_dir: Path | None = None) -> None:
    """Regenerate catalog figures from tracked documentation figure scripts."""
    _run([sys.executable, str(DOC_FIGURE_DIR / "build_simulator_semantic_alluvial.py")])
    _run([sys.executable, str(DOC_FIGURE_DIR / "build_inference_tool_contract_map.py")])
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        artifacts = {}
        for source_stem in (
            "simulator_semantic_coverage",
            "inference_tool_contract_map",
        ):
            for suffix in (".svg", ".pdf"):
                source = OUT_DIR / (source_stem + suffix)
                target = output_dir / (source_stem + suffix)
                shutil.copyfile(source, target)
                artifacts[target.name] = hashlib.sha256(target.read_bytes()).hexdigest()
        sources = [
            *SIMULATOR_DIR.glob("*/simulatorspec.json"),
            *TOOL_DIR.glob("*/toolspec.json"),
            *DOC_FIGURE_DIR.glob("build_*.py"),
            *(ROOT / "andrea/catalog_inference_tools/input_specs").glob("*.json"),
            *(ROOT / "andrea/catalog_simulation_data_tools/input_specs").glob("*.json"),
        ]
        manifest = {
            "git_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "git_dirty": bool(
                subprocess.check_output(
                    ["git", "status", "--porcelain"], cwd=ROOT, text=True
                ).strip()
            ),
            "description": "Catalog figures generated from the current ANDREA sources.",
            "sources_sha256": {
                str(path.relative_to(ROOT)): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in sorted(sources)
            },
            "artifacts_sha256": artifacts,
        }
        (output_dir / "catalog_figures_manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
    for generated_pdf in (
        OUT_DIR / "simulator_semantic_coverage.pdf",
        OUT_DIR / "inference_tool_contract_map.pdf",
    ):
        if generated_pdf.exists():
            generated_pdf.unlink()


def _markdown_cell(value: object) -> str:
    text = str(value).replace("\n", " ").replace("|", "\\|").strip()
    return text or "-"


def _publications(spec: dict) -> str:
    publications = [
        str(item).strip() for item in spec.get("publication", []) if str(item).strip()
    ]
    return ", ".join(publications) or "-"


def _simulator_table(simulators: list[dict]) -> list[str]:
    lines = [
        "| Simulator | Capabilities | Data axes covered | Publication |",
        "|---|---:|---|---|",
    ]
    for spec in simulators:
        axes = []
        for capability in spec.get("capabilities", []):
            data_axes = capability.get("data_axes", {})
            if not isinstance(data_axes, dict):
                continue
            axis = "/".join(
                str(data_axes.get(key, "-"))
                for key in (
                    "measurement",
                    "resolution",
                    "column_kind",
                    "experimental_design",
                )
            )
            if axis not in axes:
                axes.append(axis)
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(spec.get("name", spec["_path_id"])),
                    str(len(spec.get("capabilities", []))),
                    _markdown_cell(", ".join(axes) or "-"),
                    _markdown_cell(_publications(spec)),
                ]
            )
            + " |"
        )
    return lines


def _inference_tool_table(tools: list[dict]) -> list[str]:
    lines = [
        "| Tool | Primary method family | Execution modes | Output semantics | Publication |",
        "|---|---|---|---|---|",
    ]
    for spec in tools:
        modes = (
            ", ".join(f"`{mode}`" for mode in spec.get("execution_capabilities", []))
            or "-"
        )
        outputs = spec.get("outputs", {})
        if isinstance(outputs, dict):
            output_semantics = ", ".join(
                [
                    f"directed={outputs.get('directed', '-')}",
                    f"sign={outputs.get('sign', '-')}",
                    f"evidence={outputs.get('evidence', '-')}",
                ]
            )
        else:
            output_semantics = "-"
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(spec.get("name", spec["_path_id"])),
                    _markdown_cell(spec["method_family"]),
                    _markdown_cell(modes),
                    _markdown_cell(output_semantics),
                    _markdown_cell(_publications(spec)),
                ]
            )
            + " |"
        )
    return lines


def render_catalogs_page(simulators: list[dict], tools: list[dict]) -> str:
    simulator_caps = sum(len(spec.get("capabilities", [])) for spec in simulators)
    tool_modes = sum(len(spec.get("execution_capabilities", [])) for spec in tools)
    lines = [
        "<!-- Generated by scripts/render_doc_assets.py. Do not edit manually. -->",
        "",
        "# Catalogs And Coverage",
        "",
        "ANDREA catalogs describe what each simulator or inference tool can do before",
        "any expensive execution is launched. Specs are versioned JSON files and are",
        "validated independently from wrapper runtime tests.",
        "",
        "| Catalog | Entries | Executable coverage entries | Source specs |",
        "|---|---:|---:|---|",
        f"| Simulation data tools | {len(simulators)} | {simulator_caps} capabilities | `simulatorspec.json` |",
        f"| Inference tools | {len(tools)} | {tool_modes} execution modes | `toolspec.json` |",
        "",
        "## Simulators",
        "",
        "Simulator specs live under",
        "`andrea/catalog_simulation_data_tools/simulators/<simulator>/simulatorspec.json`.",
        "They describe publication metadata, semantic data axes, native and derived",
        "outputs, extra inputs, parameters, runtime resources and compatibility rules.",
        "",
        "![Simulator semantic coverage](assets/simulator_semantic_coverage.svg)",
        "",
        *_simulator_table(simulators),
        "",
        "## Inference Tools",
        "",
        "Inference specs live under",
        "`andrea/catalog_inference_tools/tools/<tool>/toolspec.json`. They describe",
        "publication metadata, execution capabilities, accepted input semantics,",
        "extra inputs, output semantics, parameters, runtime resources and",
        "compatibility rules.",
        "The figure groups the primary integrated algorithm by the reviewed",
        "`method_family` field. Context, prior knowledge and workflow composition",
        "are separate attributes; this grouping does not assert that methods",
        "within a family are identical. Integration decision logs cite sources.",
        "",
        "![Inference-tool contract map](assets/inference_tool_contract_map.svg)",
        "",
        *_inference_tool_table(tools),
        "",
        "## Maintenance",
        "",
        "This page and its figures are generated from tracked catalog and input specs.",
        "They do not depend on cost profiles or ignored workspaces. Regeneration",
        "instructions are kept in [development.md](development.md).",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalogs-only",
        action="store_true",
        help="Skip the overview requiring LaTeX.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Export catalog SVG/PDF figures and a manifest of source hashes.",
    )
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not args.catalogs_only:
        render_overview_png()
    render_catalog_figures(args.output_dir)
    simulators = _load_simulators()
    tools = _load_tools()
    (ROOT / "docs/catalogs.md").write_text(
        render_catalogs_page(simulators, tools),
        encoding="utf-8",
    )

    for stale in (
        "catalog_simulator_coverage.svg",
        "catalog_inference_tool_coverage.svg",
        "inference_tool_contract.svg",
    ):
        stale_path = OUT_DIR / stale
        if stale_path.exists():
            stale_path.unlink()


if __name__ == "__main__":
    main()
