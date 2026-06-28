#!/usr/bin/env python3
"""Build the inference-tool contract map figure.

The figure is generated from executable inference-tool specs.  The only
curated part is the method-family grouping, because that is scientific
interpretation rather than a machine-readable contract field.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from build_simulator_semantic_alluvial import (
    BACKGROUND,
    INK,
    MUTED,
    PANEL,
    Canvas,
    blend,
    hex_to_rgb,
    text_width,
)


ROOT = Path(__file__).resolve().parents[2]
SPEC_ROOT = ROOT / "andrea/catalog_inference_tools/tools"
FIGURE_DIR = ROOT / "docs/assets"
SVG_PATH = FIGURE_DIR / "inference_tool_contract_map.svg"
PDF_PATH = FIGURE_DIR / "inference_tool_contract_map.pdf"

WIDTH = 1280
HEIGHT = 735

TOOL_X = 570
TOOL_W = 166
TOOL_H = 13.5
TOOL_LEFT = TOOL_X - TOOL_W / 2
TOOL_RIGHT = TOOL_X + TOOL_W / 2

FAMILY_X = 406
FAMILY_W = 298
FAMILY_RIGHT = FAMILY_X + FAMILY_W

INPUT_CARD_X = 70
INPUT_CARD_W = 210
INPUT_CARD_H = 58
INPUT_X = INPUT_CARD_X + INPUT_CARD_W
INPUT_HEADER_X = INPUT_CARD_X + INPUT_CARD_W / 2
CONTEXT_X = 925
ACCEPT_X = CONTEXT_X
ACCEPT_W = 124

FAMILY_RULES = (
    (
        "information",
        "Information-theoretic",
        "#2f6690",
        {
            "conditional_mutual_information",
            "context_likelihood",
            "dpi_pruning",
            "information_theory",
            "mutual_information",
            "multivariate_information",
            "network_context",
            "partial_information_decomposition",
            "sjaracne",
        },
    ),
    (
        "tree",
        "Tree / ensemble",
        "#c9791f",
        {
            "bagging",
            "early_stopping",
            "extra_trees",
            "feature_importance",
            "gradient_boosting",
            "gradient_boosting_regression",
            "random_forest",
            "tree_ensemble",
        },
    ),
    (
        "regression",
        "Regression / statistical",
        "#4c8a4f",
        {
            "bayesian_best_subset_regression",
            "coexpression",
            "covariance_inverse",
            "fused_lasso",
            "l0_regularization",
            "lars",
            "partial_correlation",
            "pearson",
            "regularized_regression",
            "signed_association",
            "sparse_regression",
            "stability_selection",
            "structural_equation_model",
        },
    ),
    (
        "neural",
        "Deep learning / neural",
        "#8a5fbf",
        {
            "deep_learning",
            "diffusion_model",
            "discrete_generation",
            "explainable_ai",
            "few_shot_learning",
            "graph_attention",
            "graph_transformer",
            "hybrid_attention",
            "layer_wise_relevance_propagation",
            "meta_learning",
            "probabilistic_generation",
            "variational_autoencoder",
        },
    ),
    (
        "graph",
        "Graph / kernel",
        "#7b5a2a",
        {
            "admm",
            "dropout_aware",
            "gaussian_copula_graphical_model",
            "graph_signal_processing",
            "kernel_methods",
            "kernel_weighting",
            "k_nearest_neighbors",
            "signed_graph_learning",
        },
    ),
    (
        "context",
        "Context / aggregation",
        "#008c86",
        {
            "aggregate_network",
            "cell_phenotype",
            "cell_specific_network",
            "cell_state",
            "cell_type_specific",
            "linear_interpolation",
            "metacell",
            "single_sample",
        },
    ),
    (
        "workflow",
        "Workflow / prior-based",
        "#b4577a",
        {
            "borda_ranking",
            "lineage_aware",
            "motif_enrichment",
            "multi_task_learning",
            "prior_guided",
            "prior_knowledge",
            "regression_per_target",
            "scenic",
            "transcription_factor_activity",
        },
    ),
)

OTHER_FAMILY = ("other", "Other / uncategorized", "#5d6878")
FAMILIES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = ()

PREFERRED_INPUT_ORDER = (
    ("groups", "groups.tsv"),
    ("tf_list", "tf_list.txt"),
    ("prior_grn", "prior_grn.tsv"),
    ("prior_grn_by_group", "prior_grn_by_group.tsv"),
    ("column_phenotypes", "column_phenotypes.tsv"),
    ("column_descriptors", "column_descriptors.tsv"),
    ("pseudotime", "pseudotime.tsv"),
    ("spatial_coordinates", "spatial_coordinates.tsv"),
    ("lineage_tree", "lineage_tree.tsv"),
    ("cluster_markers", "cluster_markers.tsv"),
    ("cluster_identities", "cluster_identities.tsv"),
    ("terms_of_interest", "terms_of_interest.txt"),
    ("enrichment_background", "enrichment_background.txt"),
    ("grnboost_network", "grnboost_network.tsv"),
)

INPUT_ORDER: tuple[tuple[str, str], ...] = PREFERRED_INPUT_ORDER

TOOL_SPECIFIC_INPUT = "__tool_specific_inputs__"
TOOL_SPECIFIC_LABEL = "tool-specific inputs"

INPUT_SUBTITLES = {
    "groups": "column to group labels",
    "tf_list": "candidate regulators",
    "prior_grn": "known regulatory edges",
    TOOL_SPECIFIC_INPUT: "tool-specific files",
}

PREFERRED_ACCEPT_ORDER = (
    ("samples", "samples"),
    ("cells", "cells"),
    ("spots", "spots"),
    ("timepoints", "timepoints"),
    ("perturbations", "perturbations"),
)

ACCEPT_ORDER: tuple[tuple[str, str], ...] = PREFERRED_ACCEPT_ORDER

ACCEPT_STYLES = {
    "samples": {"dash": None, "marker": "circle"},
    "cells": {"dash": "5 3", "marker": "square"},
    "spots": {"dash": "1.5 4", "marker": "ring"},
    "timepoints": {"dash": "8 3 1.5 3", "marker": "tick"},
    "perturbations": {"dash": "3 2 1 2", "marker": "cross"},
}

CONTEXT_NODES = (
    ("global", "global"),
    ("group", "group"),
    ("column", "column"),
)

INPUT_STYLES = {
    "required": {"color": "#744210", "width": 2.25, "dash": None, "label": "required"},
    "conditional_required": {
        "color": "#6b46c1",
        "width": 1.95,
        "dash": "6 4",
        "label": "conditional",
    },
    "optional": {"color": "#64748b", "width": 1.55, "dash": "1.5 4", "label": "optional"},
}

CAPABILITY_STYLES = {
    "native": {"color": "#2f855a", "width": 2.35, "dash": None, "label": "native/direct"},
    "emulated": {"color": "#2b6cb0", "width": 2.0, "dash": "6 4", "label": "emulated"},
    "aggregated": {"color": "#c47a00", "width": 2.0, "dash": "1.5 4", "label": "aggregated"},
}

@dataclass(frozen=True)
class Tool:
    tool_id: str
    label: str
    family_id: str
    family_label: str
    family_color: str
    capabilities: frozenset[str]
    inputs: dict[str, str]
    accepts: frozenset[str]
    threaded: bool
    default_threads: int
    max_threads: int
    directed: bool
    sign: str
    param_count: int


def curve(
    canvas: Canvas,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    stroke: str,
    stroke_width: float = 1.5,
    alpha: float = 0.55,
    dash: str | None = None,
    bend: float = 0.50,
) -> None:
    """Draw a cubic Bezier stroke in both SVG and PDF outputs."""

    distance = abs(x2 - x1)
    control = max(36.0, distance * bend)
    c1x = x1 + control if x2 >= x1 else x1 - control
    c2x = x2 - control if x2 >= x1 else x2 + control
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    canvas.svg.append(
        f'<path d="M {x1:.2f},{y1:.2f} C {c1x:.2f},{y1:.2f} '
        f'{c2x:.2f},{y2:.2f} {x2:.2f},{y2:.2f}" fill="none" '
        f'stroke="{stroke}" stroke-width="{stroke_width:.2f}" '
        f'opacity="{alpha:.3f}" stroke-linecap="round"{dash_attr}/>'
    )
    r, g, b = hex_to_rgb(stroke)
    dash_pdf = ""
    if dash:
        dash_pdf = f"[{' '.join(dash.replace(',', ' ').split())}] 0 d"
    canvas.pdf.extend(
        [
            "q",
            canvas._alpha_state(alpha),
            f"{r:.4f} {g:.4f} {b:.4f} RG",
            f"{stroke_width:.3f} w",
            "1 J",
            dash_pdf,
            (
                f"{x1:.3f} {canvas._py(y1):.3f} m "
                f"{c1x:.3f} {canvas._py(y1):.3f} "
                f"{c2x:.3f} {canvas._py(y2):.3f} "
                f"{x2:.3f} {canvas._py(y2):.3f} c S"
            ),
            "[] 0 d",
            "0 J",
            "Q",
        ]
    )


def load_tools() -> list[Tool]:
    global FAMILIES, INPUT_ORDER, ACCEPT_ORDER

    specs = sorted(SPEC_ROOT.glob("*/toolspec.json"))
    if not specs:
        raise SystemExit(f"No inference-tool specs found under {SPEC_ROOT}")

    loaded: dict[str, Tool] = {}
    family_members: dict[str, list[str]] = {}
    used_inputs: set[str] = set()
    used_accepts: set[str] = set()
    for spec_path in specs:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        tool_id = spec.get("id") or spec_path.parent.name
        family_id, family_label, color = classify_family(method_keywords(spec))

        inputs = normalize_inputs(spec.get("extra_inputs") or {})
        accepts = frozenset(str(item) for item in spec.get("accepts", []) or [])
        used_inputs.update(inputs)
        used_accepts.update(accepts)

        threading = (spec.get("runtime_resources") or {}).get("threading") or {}
        outputs = spec.get("outputs") or {}
        loaded[tool_id] = Tool(
            tool_id=tool_id,
            label=str(spec.get("name") or tool_id),
            family_id=family_id,
            family_label=family_label,
            family_color=color,
            capabilities=frozenset(capability_keys(spec.get("execution_capabilities") or {})),
            inputs=inputs,
            accepts=accepts,
            threaded=bool(threading.get("supported")),
            default_threads=int(threading.get("default_threads", 1)),
            max_threads=int(threading.get("max_threads", 1)),
            directed=bool(outputs.get("directed")),
            sign=str(outputs.get("sign", "none")),
            param_count=parameter_count(spec),
        )
        family_members.setdefault(family_id, []).append(tool_id)

    preferred_inputs = [item for item in PREFERRED_INPUT_ORDER if item[0] in used_inputs]
    known_input_ids = {item_id for item_id, _label in preferred_inputs}
    extra_inputs = [(input_id, input_label(input_id)) for input_id in sorted(used_inputs - known_input_ids)]
    INPUT_ORDER = tuple(preferred_inputs + extra_inputs)

    preferred_accepts = [item for item in PREFERRED_ACCEPT_ORDER if item[0] in used_accepts]
    known_accept_ids = {item_id for item_id, _label in preferred_accepts}
    extra_accepts = [(accept_id, accept_id.replace("_", " ")) for accept_id in sorted(used_accepts - known_accept_ids)]
    ACCEPT_ORDER = tuple(preferred_accepts + extra_accepts)

    family_rows: list[tuple[str, str, str, tuple[str, ...]]] = []
    for family_id, family_label, color, _keywords in FAMILY_RULES:
        members = sorted(family_members.get(family_id, []), key=lambda tool_id: loaded[tool_id].label.lower())
        if members:
            family_rows.append((family_id, family_label, color, tuple(members)))
    other_members = sorted(family_members.get(OTHER_FAMILY[0], []), key=lambda tool_id: loaded[tool_id].label.lower())
    if other_members:
        family_rows.append((OTHER_FAMILY[0], OTHER_FAMILY[1], OTHER_FAMILY[2], tuple(other_members)))
    FAMILIES = tuple(family_rows)

    ordered: list[Tool] = []
    for _family_id, _family_label, _color, tool_ids in FAMILIES:
        ordered.extend(loaded[tool_id] for tool_id in tool_ids)
    return ordered


def input_label(input_id: str) -> str:
    spec_path = ROOT / "andrea/catalog_inference_tools/input_specs" / f"{input_id}.json"
    if not spec_path.exists():
        return input_id
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    file_kind = str(spec.get("file_kind", "tsv"))
    if file_kind in {"txt", "txt_list", "list"}:
        return f"{input_id}.txt"
    if file_kind in {"json", "json_object"}:
        return f"{input_id}.json"
    return f"{input_id}.tsv"


def parameter_count(spec: dict) -> int:
    params = spec.get("params", spec.get("parameters"))
    if params is None:
        return 0
    if isinstance(params, (dict, list)):
        return len(params)
    return 1


def capability_keys(raw: object) -> list[str]:
    if isinstance(raw, dict):
        return list(raw)
    if isinstance(raw, list):
        keys: list[str] = []
        for item in raw:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                value = item.get("id") or item.get("capability") or item.get("name")
                keys.append(str(value) if value is not None else str(item))
            else:
                keys.append(str(item))
        return keys
    return [str(raw)]


def method_keywords(spec: dict) -> set[str]:
    raw = spec.get("method_keywords", [])
    if isinstance(raw, str):
        items = raw.replace(",", " ").split()
    elif isinstance(raw, list):
        items = raw
    else:
        items = []
    return {str(item).strip().lower() for item in items if str(item).strip()}


def classify_family(keywords: set[str]) -> tuple[str, str, str]:
    best: tuple[str, str, str] = OTHER_FAMILY
    best_score = 0
    for family_id, family_label, color, family_keywords in FAMILY_RULES:
        score = len(keywords & family_keywords)
        if score > best_score:
            best = (family_id, family_label, color)
            best_score = score
    return best


def compress_inputs(tools: list[Tool]) -> tuple[list[tuple[str, str]], dict[str, dict[str, str]], dict[str, int]]:
    usage: dict[str, set[str]] = {}
    for tool in tools:
        for input_id in tool.inputs:
            usage.setdefault(input_id, set()).add(tool.tool_id)
    single_use = {input_id for input_id, tool_ids in usage.items() if len(tool_ids) == 1}

    input_nodes: list[tuple[str, str]] = []
    counts: dict[str, int] = {}
    for input_id, label in INPUT_ORDER:
        if input_id not in usage or input_id in single_use:
            continue
        input_nodes.append((input_id, label))
        counts[input_id] = len(usage[input_id])

    if single_use:
        input_nodes.append((TOOL_SPECIFIC_INPUT, TOOL_SPECIFIC_LABEL))
        counts[TOOL_SPECIFIC_INPUT] = len(single_use)

    precedence = {"required": 3, "conditional_required": 2, "optional": 1}
    compressed: dict[str, dict[str, str]] = {}
    for tool in tools:
        tool_inputs: dict[str, str] = {}
        specific_requirements: list[str] = []
        for input_id, requirement in tool.inputs.items():
            if input_id in single_use:
                specific_requirements.append(requirement)
            else:
                tool_inputs[input_id] = requirement
        if specific_requirements:
            tool_inputs[TOOL_SPECIFIC_INPUT] = max(specific_requirements, key=lambda item: precedence[item])
        compressed[tool.tool_id] = tool_inputs
    return input_nodes, compressed, counts


def normalize_inputs(extra_inputs: dict) -> dict[str, str]:
    precedence = {"required": 3, "conditional_required": 2, "optional": 1}
    out: dict[str, str] = {}
    for kind in ("required", "conditional_required", "optional"):
        items = extra_inputs.get(kind, []) or []
        if isinstance(items, dict):
            items = list(items.values())
        for item in items:
            input_id = normalize_input_id(item)
            if not input_id:
                continue
            if precedence[kind] > precedence.get(out.get(input_id, ""), 0):
                out[input_id] = kind
    return out


def normalize_input_id(item: object) -> str | None:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        value = item.get("input") or item.get("id") or item.get("input_id")
        return str(value) if value else None
    return str(item) if item is not None else None


def context_edges(tool: Tool) -> list[tuple[str, str]]:
    edges: list[tuple[str, str]] = []
    caps = tool.capabilities
    if "global" in caps:
        edges.append(("global", "native"))
    if "group_native" in caps:
        edges.append(("group", "native"))
    if "group_emulated" in caps:
        edges.append(("group", "emulated"))
    if "group_aggregated" in caps:
        edges.append(("group", "aggregated"))
    if "column_native" in caps:
        edges.append(("column", "native"))

    supported = {"global", "group_native", "group_emulated", "group_aggregated", "column_native"}
    unknown = caps - supported
    if unknown:
        raise SystemExit(f"Tool '{tool.tool_id}' has unmapped execution capabilities: {sorted(unknown)}")
    return edges


def layout_tools(tools: list[Tool]) -> tuple[dict[str, float], dict[str, tuple[float, float, str, str, str]]]:
    y = 132.0
    row_gap = 15.4
    family_gap = 4.8
    header_h = 24.0
    y_by_tool: dict[str, float] = {}
    family_boxes: dict[str, tuple[float, float, str, str, str]] = {}
    index = 0
    for family_id, family_label, color, tool_ids in FAMILIES:
        family_top = y
        first_y = y + header_h
        y = first_y
        for tool_id in tool_ids:
            y_by_tool[tool_id] = y
            y += row_gap
            index += 1
        last_y = y_by_tool[tool_ids[-1]]
        family_boxes[family_id] = (family_top, last_y + 11, family_label, color, str(len(tool_ids)))
        y += family_gap
    return y_by_tool, family_boxes


def input_positions(input_nodes: list[tuple[str, str]]) -> dict[str, float]:
    if len(input_nodes) == 1:
        return {input_nodes[0][0]: 410.0}
    y0 = 206.0
    y1 = 610.0
    step = (y1 - y0) / (len(input_nodes) - 1)
    return {input_id: y0 + i * step for i, (input_id, _label) in enumerate(input_nodes)}


def accept_positions() -> dict[str, float]:
    if not ACCEPT_ORDER:
        return {}
    if len(ACCEPT_ORDER) == 1:
        return {ACCEPT_ORDER[0][0]: 620.0}
    y0 = 552.0
    y1 = 682.0
    step = (y1 - y0) / max(1, len(ACCEPT_ORDER) - 1)
    return {accept_id: y0 + index * step for index, (accept_id, _label) in enumerate(ACCEPT_ORDER)}


def context_positions() -> dict[str, float]:
    return {"global": 184.0, "group": 296.0, "column": 408.0}


def draw_title(canvas: Canvas) -> None:
    canvas.rect(0, 0, WIDTH, HEIGHT, fill=BACKGROUND, stroke="none")
    canvas.text(40, 41, "Inference tool contract map", size=25.5, bold=True, fill=INK)
    canvas.text(
        40,
        65,
        "Method families, required inputs, output-network contexts and runtime parallelism generated from ANDREA tool specifications.",
        size=11.4,
        fill=MUTED,
    )


def draw_panel(canvas: Canvas) -> None:
    canvas.rect(24, 76, WIDTH - 48, 632, fill=PANEL, stroke="#cfc6b8", stroke_width=1.2, rx=14)
    canvas.text(48, 106, "TOOL CONTRACT FLOW", size=17.2, bold=True, fill=INK)
    canvas.text(INPUT_HEADER_X, 145, "INPUTS", size=14.0, fill=INK, bold=True, anchor="middle")
    canvas.text(TOOL_X, 106, "METHOD FAMILY / TOOL", size=13.4, fill=INK, bold=True, anchor="middle")
    canvas.text(CONTEXT_X, 145, "OUTPUT CONTEXTS", size=14.0, fill=INK, bold=True, anchor="middle")
    canvas.text(ACCEPT_X, 532, "EXPRESSION COLUMNS", size=13.0, fill=INK, bold=True, anchor="middle")


def draw_family_backgrounds(
    canvas: Canvas,
    family_boxes: dict[str, tuple[float, float, str, str, str]],
) -> None:
    for family_id, (y0, y1, label, color, count) in family_boxes.items():
        canvas.rect(FAMILY_X, y0, FAMILY_W, y1 - y0, fill=blend(color, alpha=0.060), stroke=blend(color, alpha=0.50), rx=9)
        canvas.line(FAMILY_X + 16, y0 + 20, FAMILY_RIGHT - 16, y0 + 20, stroke=blend(color, alpha=0.55), stroke_width=0.8, alpha=0.75)


def draw_family_headers(
    canvas: Canvas,
    family_boxes: dict[str, tuple[float, float, str, str, str]],
) -> None:
    for family_id, (y0, y1, label, color, count) in family_boxes.items():
        canvas.rect(FAMILY_X + 6, y0 + 5, 5, min(15, y1 - y0 - 10), fill=color, stroke="none", rx=2, alpha=0.86)
        canvas.text(FAMILY_X + FAMILY_W / 2, y0 + 12.5, label, size=9.9, fill=color, bold=True, anchor="middle")
        canvas.text(FAMILY_RIGHT - 14, y0 + 12.5, count, size=8.7, fill=color, bold=True, anchor="end")


def draw_input_nodes(
    canvas: Canvas,
    input_nodes: list[tuple[str, str]],
    input_y: dict[str, float],
    input_counts: dict[str, int],
) -> None:
    for input_id, label in input_nodes:
        y = input_y[input_id]
        canvas.rect(
            INPUT_CARD_X,
            y - INPUT_CARD_H / 2,
            INPUT_CARD_W,
            INPUT_CARD_H,
            fill="#fbfaf7",
            stroke="#cfc6b8",
            stroke_width=1,
            rx=8,
        )
        draw_input_icon(canvas, input_id, INPUT_CARD_X + 27, y)
        canvas.text(INPUT_CARD_X + 72, y - 7.5, label, size=10.8, fill=INK, bold=True)
        subtitle = INPUT_SUBTITLES.get(input_id, "additional file")
        canvas.text(INPUT_CARD_X + 72, y + 9.5, subtitle, size=8.8, fill=MUTED)
        canvas.rect(INPUT_CARD_X + INPUT_CARD_W - 34, y - 20.0, 22, 13.5, fill=PANEL, stroke="#aeb7c4", stroke_width=0.8, rx=6)
        canvas.text(INPUT_CARD_X + INPUT_CARD_W - 23, y - 10.3, str(input_counts[input_id]), size=8.1, fill=MUTED, bold=True, anchor="middle")
        canvas.circle(INPUT_X, y, 4.8, fill=PANEL, stroke="#8994a5", stroke_width=1)
        canvas.circle(INPUT_X, y, 2.2, fill="#8994a5", stroke="none")


def draw_input_icon(canvas: Canvas, input_id: str, x: float, y: float) -> None:
    if input_id == "groups":
        draw_groups_icon(canvas, x, y)
    elif input_id == "tf_list":
        draw_tf_list_icon(canvas, x, y)
    elif input_id == "prior_grn":
        draw_prior_grn_icon(canvas, x, y)
    else:
        draw_tool_specific_icon(canvas, x, y)


def draw_groups_icon(canvas: Canvas, x: float, y: float) -> None:
    color = "#5d6878"
    group_color = "#8d99ae"
    left = x - 14
    for i, dy in enumerate((-11, 0, 11)):
        canvas.circle(left, y + dy, 2.7, fill=color, stroke="none")
    for dy in (-6, 8):
        canvas.rect(x + 5, y + dy - 5, 22, 10, fill="#f4f6f8", stroke=group_color, stroke_width=0.8, rx=5)
    canvas.line(left + 3, y - 11, x + 5, y - 6, stroke=group_color, stroke_width=0.8, alpha=0.85)
    canvas.line(left + 3, y, x + 5, y - 6, stroke=group_color, stroke_width=0.8, alpha=0.85)
    canvas.line(left + 3, y + 11, x + 5, y + 8, stroke=group_color, stroke_width=0.8, alpha=0.85)


def draw_tf_list_icon(canvas: Canvas, x: float, y: float) -> None:
    color = "#5d6878"
    canvas.rect(x - 16, y - 16, 26, 32, fill="#fbfaf7", stroke="#aeb7c4", stroke_width=0.9, rx=3)
    for i, yy in enumerate((y - 7, y, y + 7)):
        canvas.line(x - 10, yy, x + 4, yy, stroke="#aeb7c4", stroke_width=0.9)
    canvas.rect(x + 4, y - 12, 21, 15, fill=blend("#2b6cb0", alpha=0.10), stroke="#2b6cb0", stroke_width=0.8, rx=6)
    canvas.text(x + 14.5, y - 1.5, "TF", size=6.7, fill="#2b6cb0", bold=True, anchor="middle")


def draw_prior_grn_icon(canvas: Canvas, x: float, y: float) -> None:
    color = "#5d6878"
    edge = "#8d99ae"
    nodes = ((x - 13, y + 9), (x - 2, y - 10), (x + 16, y + 4))
    canvas.line(nodes[0][0], nodes[0][1], nodes[1][0], nodes[1][1], stroke=edge, stroke_width=1.0)
    canvas.line(nodes[1][0], nodes[1][1], nodes[2][0], nodes[2][1], stroke=edge, stroke_width=1.0)
    canvas.line(nodes[0][0], nodes[0][1], nodes[2][0], nodes[2][1], stroke=edge, stroke_width=0.8, dash="2 2")
    for cx, cy in nodes:
        canvas.circle(cx, cy, 3.5, fill=PANEL, stroke=color, stroke_width=1.1)
    canvas.circle(nodes[1][0], nodes[1][1], 1.7, fill=color, stroke="none")


def draw_tool_specific_icon(canvas: Canvas, x: float, y: float) -> None:
    color = "#5d6878"
    for i, (dx, dy) in enumerate(((-11, -9), (-5, -3), (1, 3))):
        canvas.rect(x + dx, y + dy, 22, 18, fill="#fbfaf7", stroke="#aeb7c4", stroke_width=0.8, rx=3)
        canvas.line(x + dx + 5, y + dy + 7, x + dx + 16, y + dy + 7, stroke="#aeb7c4", stroke_width=0.7)
    canvas.circle(x + 16, y - 11, 5.8, fill=blend("#744210", alpha=0.10), stroke="#744210", stroke_width=0.8)
    canvas.text(x + 16, y - 8.7, "*", size=8, fill="#744210", bold=True, anchor="middle")


def draw_accept_nodes(canvas: Canvas, accept_y: dict[str, float], tools: list[Tool]) -> None:
    counts = {accept_id: 0 for accept_id, _label in ACCEPT_ORDER}
    for tool in tools:
        for accept_id in tool.accepts:
            counts[accept_id] += 1
    for accept_id, label in ACCEPT_ORDER:
        y = accept_y[accept_id]
        style = accept_style(accept_id)
        canvas.rect(
            ACCEPT_X - ACCEPT_W / 2,
            y - 9,
            ACCEPT_W,
            18,
            fill="#fbfaf7",
            stroke="#aeb7c4",
            stroke_width=0.9,
            rx=9,
        )
        draw_accept_marker(canvas, ACCEPT_X - ACCEPT_W / 2 + 11, y, style["marker"])
        canvas.text(ACCEPT_X - ACCEPT_W / 2 + 23, y + 3.2, label, size=9.2, fill=INK, bold=True)
        canvas.text(ACCEPT_X + ACCEPT_W / 2 - 10, y + 3.0, str(counts[accept_id]), size=8.5, fill=MUTED, bold=True, anchor="end")


def draw_accept_marker(canvas: Canvas, x: float, y: float, marker: str) -> None:
    color = "#5d6878"
    if marker == "circle":
        canvas.circle(x, y, 2.7, fill=color, stroke="none")
    elif marker == "square":
        canvas.rect(x - 2.7, y - 2.7, 5.4, 5.4, fill=color, stroke="none", rx=1)
    elif marker == "ring":
        canvas.circle(x, y, 3.0, fill=PANEL, stroke=color, stroke_width=1.2)
    elif marker == "tick":
        canvas.line(x - 4, y, x + 4, y, stroke=color, stroke_width=1.1)
        canvas.line(x, y - 4, x, y + 4, stroke=color, stroke_width=1.1)
    elif marker == "cross":
        canvas.line(x - 3.4, y - 3.4, x + 3.4, y + 3.4, stroke=color, stroke_width=1.1)
        canvas.line(x - 3.4, y + 3.4, x + 3.4, y - 3.4, stroke=color, stroke_width=1.1)
    else:
        canvas.circle(x, y, 2.7, fill=color, stroke="none")


def accept_style(accept_id: str) -> dict[str, str | None]:
    return ACCEPT_STYLES.get(accept_id, {"dash": None, "marker": "circle"})


def draw_context_nodes(canvas: Canvas, context_y: dict[str, float], tools: list[Tool]) -> None:
    counts = {context: 0 for context, _label in CONTEXT_NODES}
    detail = {context: {"native": 0, "emulated": 0, "aggregated": 0} for context, _label in CONTEXT_NODES}
    for tool in tools:
        for context, status in context_edges(tool):
            counts[context] += 1
            detail[context][status] += 1
    for context, label in CONTEXT_NODES:
        y = context_y[context]
        canvas.rect(CONTEXT_X - 56, y - 18, 112, 36, fill=PANEL, stroke="#cfc6b8", stroke_width=1, rx=9)
        canvas.text(CONTEXT_X - 42, y - 2, label, size=11.6, fill=INK, bold=True)
        canvas.text(CONTEXT_X + 42, y - 2, str(counts[context]), size=9.8, fill=MUTED, bold=True, anchor="end")
        x = CONTEXT_X - 40
        for status in ("native", "emulated", "aggregated"):
            value = detail[context][status]
            if not value:
                continue
            style = CAPABILITY_STYLES[status]
            canvas.line(x, y + 11, x + 12, y + 11, stroke="#5d6878", stroke_width=2, dash=style["dash"])
            canvas.text(x + 16, y + 14, str(value), size=8.4, fill=MUTED, bold=True)
            x += 34
        draw_context_scope_glyph(canvas, context, CONTEXT_X, y + 47)


def draw_context_scope_glyph(canvas: Canvas, context: str, x: float, y: float) -> None:
    canvas.line(x, y - 28, x, y - 21, stroke="#d8d0c4", stroke_width=0.7, alpha=0.85)
    if context == "global":
        draw_global_scope(canvas, x, y)
    elif context == "group":
        draw_group_scope(canvas, x, y)
    elif context == "column":
        draw_column_scope(canvas, x, y)


def draw_global_scope(canvas: Canvas, x: float, y: float) -> None:
    canvas.circle(x, y - 2, 18, fill=blend("#2b6cb0", alpha=0.05), stroke="#aeb7c4", stroke_width=0.8)
    draw_mini_network(canvas, x, y - 2, scale=0.88)
    canvas.text(x, y + 25, "one network", size=8.2, fill=MUTED, bold=True, anchor="middle")


def draw_group_scope(canvas: Canvas, x: float, y: float) -> None:
    canvas.line(x - 36, y - 19, x + 36, y - 19, stroke="#d8d0c4", stroke_width=0.7, alpha=0.9)
    for variant, dx in enumerate((-32, 0, 32)):
        canvas.line(x + dx, y - 19, x + dx, y - 14, stroke="#d8d0c4", stroke_width=0.7, alpha=0.9)
        canvas.circle(x + dx, y - 2, 12.2, fill="#fbfaf7", stroke="#aeb7c4", stroke_width=0.72)
        draw_mini_network(canvas, x + dx, y - 2, scale=0.46, variant=variant)
    canvas.text(x, y + 25, "per group", size=8.2, fill=MUTED, bold=True, anchor="middle")


def draw_column_scope(canvas: Canvas, x: float, y: float) -> None:
    canvas.line(x - 36, y - 19, x + 36, y - 19, stroke="#d8d0c4", stroke_width=0.7, alpha=0.9)
    for dx in (-28, 0, 28):
        canvas.line(x + dx, y - 19, x + dx, y - 15, stroke="#d8d0c4", stroke_width=0.7, alpha=0.9)
    positions = (
        (-28, -8),
        (0, -8),
        (28, -8),
        (-28, 16),
        (0, 16),
        (28, 16),
    )
    for variant, (dx, dy) in enumerate(positions):
        canvas.circle(x + dx, y + dy, 9.4, fill="#fbfaf7", stroke="#aeb7c4", stroke_width=0.66)
        draw_micro_network(canvas, x + dx, y + dy, variant=variant)
    canvas.text(x, y + 33, "per column", size=8.2, fill=MUTED, bold=True, anchor="middle")


def draw_mini_network(canvas: Canvas, x: float, y: float, *, scale: float = 1.0, variant: int = 0) -> None:
    color = "#5d6878"
    edge = "#9aa4b2"
    points = (
        (x - 12 * scale, y + 6 * scale),
        (x - 4 * scale, y - 8 * scale),
        (x + 12 * scale, y - 2 * scale),
        (x + 5 * scale, y + 9 * scale),
    )
    edge_sets = (
        ((0, 1), (1, 2), (2, 3), (0, 3), (1, 3)),
        ((0, 1), (0, 3), (1, 3), (2, 3)),
        ((0, 2), (1, 2), (1, 3), (2, 3)),
    )
    for a, b in edge_sets[variant % len(edge_sets)]:
        canvas.line(points[a][0], points[a][1], points[b][0], points[b][1], stroke=edge, stroke_width=0.75 * scale, alpha=0.9)
    for px, py in points:
        canvas.circle(px, py, 2.6 * scale, fill=PANEL, stroke=color, stroke_width=0.85 * scale)


def draw_micro_network(canvas: Canvas, x: float, y: float, *, variant: int = 0) -> None:
    color = "#5d6878"
    edge = "#aeb7c4"
    variants = (
        (((x - 5, y + 4), (x, y - 5), (x + 5, y + 3)), ((0, 1), (1, 2))),
        (((x - 5, y - 3), (x - 1, y + 5), (x + 6, y - 1)), ((0, 1), (0, 2))),
        (((x - 5, y + 3), (x, y - 5), (x + 6, y + 3)), ((0, 1), (1, 2), (0, 2))),
        (((x - 6, y), (x, y - 5), (x + 6, y), (x, y + 5)), ((0, 1), (1, 2), (1, 3))),
    )
    points, edges = variants[variant % len(variants)]
    for a, b in edges:
        canvas.line(points[a][0], points[a][1], points[b][0], points[b][1], stroke=edge, stroke_width=0.55, alpha=0.9)
    for px, py in points:
        canvas.circle(px, py, 1.45, fill=PANEL, stroke=color, stroke_width=0.55)


def draw_edges(
    canvas: Canvas,
    tools: list[Tool],
    tool_y: dict[str, float],
    tool_inputs: dict[str, dict[str, str]],
    input_y: dict[str, float],
    accept_y: dict[str, float],
    context_y: dict[str, float],
) -> None:
    for tool in tools:
        ty = tool_y[tool.tool_id]
        for input_id, requirement in sorted(tool_inputs[tool.tool_id].items()):
            style = INPUT_STYLES[requirement]
            curve(
                canvas,
                INPUT_X + 6,
                input_y[input_id],
                TOOL_LEFT - 6,
                ty,
                stroke=style["color"],
                stroke_width=style["width"],
                alpha=0.34,
                dash=style["dash"],
                bend=0.43,
            )
        for accept_id in sorted(tool.accepts):
            style = accept_style(accept_id)
            curve(
                canvas,
                TOOL_RIGHT + 5,
                ty,
                ACCEPT_X - ACCEPT_W / 2 - 7,
                accept_y[accept_id],
                stroke="#8b95a3",
                stroke_width=1.15,
                alpha=0.24,
                dash=style["dash"],
                bend=0.42,
            )
        for context, status in context_edges(tool):
            style = CAPABILITY_STYLES[status]
            curve(
                canvas,
                TOOL_RIGHT + 6,
                ty,
                CONTEXT_X - 62,
                context_y[context],
                stroke=style["color"],
                stroke_width=style["width"],
                alpha=0.52,
                dash=style["dash"],
                bend=0.45,
            )


def draw_tools(canvas: Canvas, tools: list[Tool], tool_y: dict[str, float]) -> None:
    for tool in tools:
        y = tool_y[tool.tool_id]
        fill = blend(tool.family_color, alpha=0.12)
        canvas.rect(TOOL_LEFT, y - TOOL_H / 2, TOOL_W, TOOL_H, fill=fill, stroke=tool.family_color, rx=8)
        canvas.circle(TOOL_LEFT + 9, y, 2.8, fill=tool.family_color, stroke="none")
        canvas.text(TOOL_LEFT + 18, y + 2.9, tool.label, size=8.8, fill=INK, bold=True)
        draw_tool_badges(canvas, tool, y)


def draw_tool_badges(canvas: Canvas, tool: Tool, y: float) -> None:
    direction_label = "D" if tool.directed else "U"
    direction_color = "#2b6cb0" if tool.directed else "#5d6878"
    sign_label = {"signed": "S", "mixed": "M", "none": "0"}.get(tool.sign, "?")
    sign_color = {"signed": "#2f855a", "mixed": "#c47a00", "none": "#8792a2"}.get(tool.sign, "#8792a2")
    runtime_label = "T" if tool.threaded else "1"
    runtime_color = "#008c86" if tool.threaded else "#8792a2"
    param_label = str(tool.param_count)
    param_color = "#33415c"
    badges = (
        (TOOL_RIGHT - 73, direction_label, direction_color),
        (TOOL_RIGHT - 54, sign_label, sign_color),
        (TOOL_RIGHT - 35, runtime_label, runtime_color),
        (TOOL_RIGHT - 16, param_label, param_color),
    )
    for x, label, color in badges:
        canvas.rect(x - 7.0, y - 5.3, 14.0, 10.6, fill=blend(color, alpha=0.12), stroke=color, stroke_width=0.75, rx=5)
        canvas.text(x, y + 2.5, label, size=7.0 if len(label) > 1 else 7.4, fill=color, bold=True, anchor="middle")


def legend_line(
    canvas: Canvas,
    x: float,
    y: float,
    *,
    color: str,
    dash: str | None,
    label: str,
    width: float = 2.2,
) -> float:
    canvas.line(x, y, x + 28, y, stroke=color, stroke_width=width, dash=dash)
    canvas.text(x + 36, y + 3.4, label, size=9.5, fill=INK)
    return x + 36 + text_width(label, 9.5) + 26


def draw_legend(canvas: Canvas) -> None:
    x = 1032
    y = 156
    w = 208
    h = 530
    canvas.rect(x, y, w, h, fill=PANEL, stroke="#cfc6b8", stroke_width=1.1, rx=14)
    canvas.text(x + 18, y + 32, "Visual encoding", size=15.0, fill=INK, bold=True)

    def section(label: str, yy: float) -> None:
        canvas.text(x + 18, yy, label, size=11.6, fill=INK, bold=True)
        canvas.line(x + 18, yy + 8, x + w - 18, yy + 8, stroke="#e3dccf", stroke_width=0.8)

    def line_item(yy: float, *, color: str, dash: str | None, label: str, width: float = 2.2) -> None:
        canvas.line(x + 24, yy, x + 56, yy, stroke=color, stroke_width=width, dash=dash)
        canvas.text(x + 68, yy + 3.7, label, size=10.5, fill=INK)

    section("Inputs", y + 67)
    yy = y + 94
    for kind in ("required", "conditional_required", "optional"):
        style = INPUT_STYLES[kind]
        line_item(yy, color=style["color"], dash=style["dash"], label=style["label"], width=style["width"])
        yy += 29

    section("Output context", y + 196)
    yy = y + 223
    for kind in ("native", "emulated", "aggregated"):
        style = CAPABILITY_STYLES[kind]
        line_item(yy, color=style["color"], dash=style["dash"], label=style["label"], width=style["width"])
        yy += 29

    section("Tool badges", y + 325)
    draw_badge_legend(canvas, x + 24, y + 356)


def draw_badge_legend(canvas: Canvas, x: float, y: float) -> None:
    items = (
        ("D/U", "#2b6cb0", "directedness"),
        ("S/M/0", "#2f855a", "score sign"),
        ("T/1", "#008c86", "runtime"),
        ("#", "#33415c", "parameters"),
    )
    yy = y
    for label, color, text in items:
        w = 34 if len(label) <= 3 else 46
        canvas.rect(x, yy - 11, w, 22, fill=blend(color, alpha=0.12), stroke=color, stroke_width=0.95, rx=10)
        canvas.text(x + w / 2, yy + 4.0, label, size=8.8, fill=color, bold=True, anchor="middle")
        canvas.text(x + w + 10, yy + 4.0, text, size=10.5, fill=INK)
        yy += 32


def build() -> None:
    tools = load_tools()
    tool_y, family_boxes = layout_tools(tools)
    input_nodes, tool_inputs, input_counts = compress_inputs(tools)
    input_y = input_positions(input_nodes)
    accept_y = accept_positions()
    context_y = context_positions()

    canvas = Canvas(WIDTH, HEIGHT)
    draw_title(canvas)
    draw_panel(canvas)
    draw_family_backgrounds(canvas, family_boxes)
    draw_edges(canvas, tools, tool_y, tool_inputs, input_y, accept_y, context_y)
    draw_family_headers(canvas, family_boxes)
    draw_input_nodes(canvas, input_nodes, input_y, input_counts)
    draw_accept_nodes(canvas, accept_y, tools)
    draw_context_nodes(canvas, context_y, tools)
    draw_tools(canvas, tools, tool_y)
    draw_legend(canvas)
    canvas.save(SVG_PATH, PDF_PATH)

    expected_tools = {path.parent.name for path in SPEC_ROOT.glob("*/toolspec.json")}
    figure_tools = {tool.tool_id for tool in tools}
    if expected_tools != figure_tools:
        raise SystemExit(f"Tool coverage mismatch: spec={sorted(expected_tools)} figure={sorted(figure_tools)}")
    print(f"Wrote {SVG_PATH.relative_to(ROOT)}")
    print(f"Wrote {PDF_PATH.relative_to(ROOT)}")
    print(f"Included {len(tools)} inference tools.")


if __name__ == "__main__":
    build()
