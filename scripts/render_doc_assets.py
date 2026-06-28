#!/usr/bin/env python3
"""Render documentation assets from ANDREA catalog and documentation sources.

This script intentionally depends only on tracked project files.  It must keep
working if git-ignored workspaces are removed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
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
        _run(["pdftoppm", "-png", "-singlefile", "-r", "180", "andrea_overview.pdf", "andrea_overview"], cwd=tmp_dir)
        shutil.copyfile(tmp_dir / "andrea_overview.png", OUT_DIR / "andrea_overview.png")


def render_catalog_figures() -> None:
    """Regenerate catalog figures from tracked documentation figure scripts."""
    _run(["python3", str(DOC_FIGURE_DIR / "build_simulator_semantic_alluvial.py")])
    _run(["python3", str(DOC_FIGURE_DIR / "build_inference_tool_contract_map.py")])
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
    publications = [str(item).strip() for item in spec.get("publication", []) if str(item).strip()]
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
                for key in ("measurement", "resolution", "column_kind", "experimental_design")
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
        "| Tool | Execution modes | Output semantics | Publication |",
        "|---|---|---|---|",
    ]
    for spec in tools:
        modes = ", ".join(f"`{mode}`" for mode in spec.get("execution_capabilities", [])) or "-"
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
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    render_overview_png()
    render_catalog_figures()
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
