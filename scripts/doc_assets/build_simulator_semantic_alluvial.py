#!/usr/bin/env python3
"""Build the simulator semantic coverage alluvial figure.

The script intentionally uses only the Python standard library.  It reads the
executable simulator specs and writes both an editable SVG preview and a vector
PDF that pdflatex can include directly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[2]
SPEC_ROOT = ROOT / "andrea/catalog_simulation_data_tools/simulators"
FIGURE_DIR = ROOT / "docs/assets"
SVG_PATH = FIGURE_DIR / "simulator_semantic_coverage.svg"
PDF_PATH = FIGURE_DIR / "simulator_semantic_coverage.pdf"

WIDTH = 1220
HEIGHT = 720

BACKGROUND = "#fbf7ef"
INK = "#162033"
MUTED = "#5c6678"
GRID = "#ded6c8"
PANEL = "#fffdf8"

SIMULATOR_COLOR_PALETTE = (
    "#bd2b1f",
    "#33415c",
    "#008c86",
    "#25894d",
    "#0077a8",
    "#c96500",
    "#6548a9",
    "#8d5916",
    "#2f6690",
    "#b4577a",
    "#4c8a4f",
    "#7b5a2a",
)

SIMULATOR_ORDER: tuple[str, ...] = ()
SIMULATOR_LABELS: dict[str, str] = {}
SIMULATOR_COLORS: dict[str, str] = {}
SIMULATOR_SHORT_LABELS: dict[str, str] = {}

STAGES = (
    ("simulator", "SIMULATOR"),
    ("resolution", "RESOLUTION"),
    ("column_kind", "COLUMN"),
    ("experimental_design", "DESIGN"),
    ("truth", "TRUTH"),
)

STAGE_X = {
    "simulator": 132,
    "resolution": 294,
    "column_kind": 436,
    "experimental_design": 601,
    "truth": 713,
}

NODE_WIDTH = {
    "simulator": 16,
    "resolution": 12,
    "column_kind": 12,
    "experimental_design": 12,
    "truth": 12,
}

ORDER = {
    "simulator": SIMULATOR_ORDER,
    "resolution": ("single_cell", "spatial", "bulk"),
    "column_kind": ("cells", "spots", "samples", "timepoints", "perturbations"),
    "experimental_design": (
        "observational",
        "steady_state",
        "differentiation",
        "trajectory",
        "time_series",
        "perturbational",
    ),
    "truth": (
        "global|native",
        "global|derived",
        "global + group|native",
        "global + group|derived",
        "global + group + column|native",
        "global + group + column|derived",
    ),
}

LABELS = {
    "single_cell": "single-cell",
    "spatial": "spatial",
    "bulk": "bulk",
    "cells": "cells",
    "spots": "spots",
    "samples": "samples",
    "timepoints": "timepoints",
    "perturbations": "perturbations",
    "observational": "observational",
    "steady_state": "steady-state",
    "differentiation": "differentiation",
    "trajectory": "trajectory",
    "time_series": "time-series",
    "perturbational": "perturbational",
    "global": "global",
    "global + group": "global + group",
    "global + group + column": "global + group + column",
    "native": "native",
    "derived": "derived",
}

BASE_OUTPUT_ROWS = (
    ("expression.tsv", "expression.tsv", "core"),
    ("truth/networks.csv", "truth/networks.csv", "core"),
    ("truth/gene_universe.txt", "gene_universe.txt", "core"),
)

PREFERRED_EXTRA_ORDER = (
    "groups",
    "timepoints",
    "perturbation_design",
    "interventions",
    "spatial_coordinates",
    "pseudotime",
    "lineage_tree",
    "cluster_identities",
    "column_phenotypes",
    "prior_grn",
    "prior_grn_by_group",
    "tf_list",
    "enrichment_background",
    "chromatin_accessibility",
    "chromatin_regions",
    "cell_cell_interactions",
    "replicates",
)

OUTPUT_ROWS: tuple[tuple[str, str, str], ...] = (
    *BASE_OUTPUT_ROWS,
    ("__native_outputs__", "native/raw outputs", "native_outputs"),
)

OUTPUT_STATUS_COLORS = {
    "core": "#5d6878",
    "native": "#2f855a",
    "derivable": "#2b6cb0",
    "native_outputs": "#8a6f4d",
}

KNOWN_EXTRAS: set[str] = set()


@dataclass(frozen=True)
class Capability:
    index: int
    simulator: str
    resolution: str
    column_kind: str
    experimental_design: str
    truth: str
    truth_is_derived: bool
    truth_node: str
    native_extras: frozenset[str]
    derivable_extras: frozenset[str]
    native_output_count: int

    def value_for(self, stage: str) -> str:
        if stage == "truth":
            return self.truth_node
        return getattr(self, stage)


def hex_to_rgb(color: str) -> tuple[float, float, float]:
    color = color.lstrip("#")
    return tuple(int(color[i : i + 2], 16) / 255 for i in (0, 2, 4))


def blend(color: str, background: str = BACKGROUND, alpha: float = 0.5) -> str:
    r1, g1, b1 = hex_to_rgb(color)
    r2, g2, b2 = hex_to_rgb(background)
    rgb = (
        int((r1 * alpha + r2 * (1 - alpha)) * 255),
        int((g1 * alpha + g2 * (1 - alpha)) * 255),
        int((b1 * alpha + b2 * (1 - alpha)) * 255),
    )
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def escape_xml(text: object) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def escape_pdf(text: object) -> str:
    return str(text).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def text_width(text: str, size: float, *, bold: bool = False) -> float:
    weight = 0.56 if bold else 0.52
    return len(text) * size * weight


class Canvas:
    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.svg: list[str] = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}">'
        ]
        self.pdf: list[str] = []

    def _py(self, y: float) -> float:
        return self.height - y

    def _alpha_state(self, alpha: float) -> str:
        if alpha <= 0.20:
            return "/GS20 gs"
        if alpha <= 0.35:
            return "/GS32 gs"
        if alpha <= 0.50:
            return "/GS45 gs"
        if alpha <= 0.70:
            return "/GS65 gs"
        if alpha <= 0.88:
            return "/GS82 gs"
        return "/GS100 gs"

    def rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        *,
        fill: str = "none",
        stroke: str = "none",
        stroke_width: float = 1,
        rx: float = 0,
        alpha: float = 1,
    ) -> None:
        attrs = [
            f'x="{x:.2f}"',
            f'y="{y:.2f}"',
            f'width="{w:.2f}"',
            f'height="{h:.2f}"',
        ]
        if rx:
            attrs.extend([f'rx="{rx:.2f}"', f'ry="{rx:.2f}"'])
        attrs.extend(
            [
                f'fill="{fill}"',
                f'stroke="{stroke}"',
                f'stroke-width="{stroke_width:.2f}"',
                f'opacity="{alpha:.3f}"',
            ]
        )
        self.svg.append(f"<rect {' '.join(attrs)}/>")
        if fill == "none" and stroke == "none":
            return
        path = self._rounded_rect_path(x, y, w, h, rx)
        ops = ["q", self._alpha_state(alpha), self._path_to_pdf(path)]
        if fill != "none":
            r, g, b = hex_to_rgb(fill)
            ops.append(f"{r:.4f} {g:.4f} {b:.4f} rg")
        if stroke != "none":
            r, g, b = hex_to_rgb(stroke)
            ops.append(f"{r:.4f} {g:.4f} {b:.4f} RG")
            ops.append(f"{stroke_width:.3f} w")
        if fill != "none" and stroke != "none":
            ops.append("B")
        elif fill != "none":
            ops.append("f")
        else:
            ops.append("S")
        ops.append("Q")
        self.pdf.extend(ops)

    def line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        *,
        stroke: str,
        stroke_width: float = 1,
        alpha: float = 1,
        dash: str | None = None,
    ) -> None:
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        self.svg.append(
            f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
            f'stroke="{stroke}" stroke-width="{stroke_width:.2f}" opacity="{alpha:.3f}"{dash_attr}/>'
        )
        r, g, b = hex_to_rgb(stroke)
        dash_pdf = ""
        if dash:
            parts = " ".join(dash.replace(",", " ").split())
            dash_pdf = f"[{parts}] 0 d"
        self.pdf.extend(
            [
                "q",
                self._alpha_state(alpha),
                f"{r:.4f} {g:.4f} {b:.4f} RG",
                f"{stroke_width:.3f} w",
                dash_pdf,
                f"{x1:.3f} {self._py(y1):.3f} m {x2:.3f} {self._py(y2):.3f} l S",
                "[] 0 d",
                "Q",
            ]
        )

    def circle(
        self,
        cx: float,
        cy: float,
        r: float,
        *,
        fill: str = "none",
        stroke: str = "none",
        stroke_width: float = 1,
        alpha: float = 1,
        dash: str | None = None,
    ) -> None:
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        self.svg.append(
            f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="{stroke_width:.2f}" opacity="{alpha:.3f}"{dash_attr}/>'
        )
        path = self._circle_path(cx, cy, r)
        ops = ["q", self._alpha_state(alpha), self._path_to_pdf(path)]
        if fill != "none":
            fr, fg, fb = hex_to_rgb(fill)
            ops.append(f"{fr:.4f} {fg:.4f} {fb:.4f} rg")
        if stroke != "none":
            sr, sg, sb = hex_to_rgb(stroke)
            ops.extend([f"{sr:.4f} {sg:.4f} {sb:.4f} RG", f"{stroke_width:.3f} w"])
            if dash:
                ops.append(f"[{' '.join(dash.replace(',', ' ').split())}] 0 d")
        if fill != "none" and stroke != "none":
            ops.append("B")
        elif fill != "none":
            ops.append("f")
        else:
            ops.append("S")
        ops.extend(["[] 0 d", "Q"])
        self.pdf.extend(ops)

    def text(
        self,
        x: float,
        y: float,
        text: str,
        *,
        size: float = 10,
        fill: str = INK,
        bold: bool = False,
        anchor: str = "start",
        alpha: float = 1,
    ) -> None:
        family = "Helvetica"
        weight = "700" if bold else "400"
        self.svg.append(
            f'<text x="{x:.2f}" y="{y:.2f}" font-family="{family}" font-size="{size:.2f}" '
            f'font-weight="{weight}" text-anchor="{anchor}" fill="{fill}" opacity="{alpha:.3f}">'
            f"{escape_xml(text)}</text>"
        )
        tx = x
        if anchor == "middle":
            tx -= text_width(text, size, bold=bold) / 2
        elif anchor == "end":
            tx -= text_width(text, size, bold=bold)
        r, g, b = hex_to_rgb(fill)
        font = "/F2" if bold else "/F1"
        self.pdf.extend(
            [
                "q",
                self._alpha_state(alpha),
                "BT",
                f"{r:.4f} {g:.4f} {b:.4f} rg",
                f"{font} {size:.3f} Tf",
                f"1 0 0 1 {tx:.3f} {self._py(y):.3f} Tm",
                f"({escape_pdf(text)}) Tj",
                "ET",
                "Q",
            ]
        )

    def band(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        width: float,
        *,
        fill: str,
        alpha: float = 0.45,
    ) -> None:
        c = max(24.0, (x2 - x1) * 0.46)
        t1, b1 = y1 - width / 2, y1 + width / 2
        t2, b2 = y2 - width / 2, y2 + width / 2
        svg_d = (
            f"M {x1:.2f},{t1:.2f} "
            f"C {x1+c:.2f},{t1:.2f} {x2-c:.2f},{t2:.2f} {x2:.2f},{t2:.2f} "
            f"L {x2:.2f},{b2:.2f} "
            f"C {x2-c:.2f},{b2:.2f} {x1+c:.2f},{b1:.2f} {x1:.2f},{b1:.2f} Z"
        )
        self.svg.append(f'<path d="{svg_d}" fill="{fill}" opacity="{alpha:.3f}"/>')
        path = [
            ("M", x1, t1),
            ("C", x1 + c, t1, x2 - c, t2, x2, t2),
            ("L", x2, b2),
            ("C", x2 - c, b2, x1 + c, b1, x1, b1),
            ("Z",),
        ]
        r, g, b = hex_to_rgb(fill)
        self.pdf.extend(
            [
                "q",
                self._alpha_state(alpha),
                f"{r:.4f} {g:.4f} {b:.4f} rg",
                self._path_to_pdf(path),
                "f",
                "Q",
            ]
        )

    def save(self, svg_path: Path, pdf_path: Path) -> None:
        self.svg.append("</svg>")
        svg_path.write_text("\n".join(self.svg) + "\n", encoding="utf-8")
        write_pdf(pdf_path, self.width, self.height, "\n".join(self.pdf).encode("latin-1"))

    def _path_to_pdf(self, path: list[tuple]) -> str:
        out: list[str] = []
        for item in path:
            cmd = item[0]
            if cmd == "M":
                out.append(f"{item[1]:.3f} {self._py(item[2]):.3f} m")
            elif cmd == "L":
                out.append(f"{item[1]:.3f} {self._py(item[2]):.3f} l")
            elif cmd == "C":
                out.append(
                    f"{item[1]:.3f} {self._py(item[2]):.3f} "
                    f"{item[3]:.3f} {self._py(item[4]):.3f} "
                    f"{item[5]:.3f} {self._py(item[6]):.3f} c"
                )
            elif cmd == "Z":
                out.append("h")
        return "\n".join(out)

    def _rounded_rect_path(self, x: float, y: float, w: float, h: float, r: float) -> list[tuple]:
        if r <= 0:
            return [("M", x, y), ("L", x + w, y), ("L", x + w, y + h), ("L", x, y + h), ("Z",)]
        r = min(r, w / 2, h / 2)
        k = 0.5522847498
        return [
            ("M", x + r, y),
            ("L", x + w - r, y),
            ("C", x + w - r + r * k, y, x + w, y + r - r * k, x + w, y + r),
            ("L", x + w, y + h - r),
            ("C", x + w, y + h - r + r * k, x + w - r + r * k, y + h, x + w - r, y + h),
            ("L", x + r, y + h),
            ("C", x + r - r * k, y + h, x, y + h - r + r * k, x, y + h - r),
            ("L", x, y + r),
            ("C", x, y + r - r * k, x + r - r * k, y, x + r, y),
            ("Z",),
        ]

    def _circle_path(self, cx: float, cy: float, r: float) -> list[tuple]:
        k = 0.5522847498
        return [
            ("M", cx + r, cy),
            ("C", cx + r, cy + r * k, cx + r * k, cy + r, cx, cy + r),
            ("C", cx - r * k, cy + r, cx - r, cy + r * k, cx - r, cy),
            ("C", cx - r, cy - r * k, cx - r * k, cy - r, cx, cy - r),
            ("C", cx + r * k, cy - r, cx + r, cy - r * k, cx + r, cy),
            ("Z",),
        ]


def write_pdf(path: Path, width: int, height: int, content: bytes) -> None:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
            "/Resources << "
            "/Font << /F1 4 0 R /F2 5 0 R >> "
            "/ExtGState << "
            "/GS20 << /ca 0.20 /CA 0.20 >> "
            "/GS32 << /ca 0.32 /CA 0.32 >> "
            "/GS45 << /ca 0.45 /CA 0.45 >> "
            "/GS65 << /ca 0.65 /CA 0.65 >> "
            "/GS82 << /ca 0.82 /CA 0.82 >> "
            "/GS100 << /ca 1 /CA 1 >> "
            ">> >> /Contents 6 0 R >>"
        ).encode("ascii"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n" + content + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{i} 0 obj\n".encode("ascii"))
        out.extend(obj)
        out.extend(b"\nendobj\n")
    xref = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    out.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode("ascii")
    )
    path.write_bytes(out)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _input_spec_filename(input_id: str) -> str:
    spec_path = ROOT / "andrea/catalog_inference_tools/input_specs" / f"{input_id}.json"
    if not spec_path.exists():
        return f"{input_id}.tsv"
    file_kind = str(_read_json(spec_path).get("file_kind", "tsv"))
    if file_kind in {"txt", "txt_list", "list"}:
        suffix = "txt"
    elif file_kind in {"json", "json_object"}:
        suffix = "json"
    else:
        suffix = "tsv"
    return f"{input_id}.{suffix}"


def _short_label(label: str, fallback: str) -> str:
    parts = "".join(char if char.isalnum() else " " for char in label).split()
    if len(parts) >= 2:
        text = "".join(part[0] for part in parts[:3]).upper()
    else:
        text = "".join(char for char in label if char.isalnum())[:3].upper()
    return text or fallback[:3].upper()


def _spec_entries() -> list[tuple[Path, dict]]:
    specs = sorted(SPEC_ROOT.glob("*/simulatorspec.json"))
    if not specs:
        raise SystemExit(f"No simulator specs found under {SPEC_ROOT}")
    return [(path, _read_json(path)) for path in specs]


def configure_from_specs(spec_entries: list[tuple[Path, dict]]) -> None:
    """Populate visual metadata from catalog specs, appending new entries."""
    global SIMULATOR_ORDER, SIMULATOR_LABELS, SIMULATOR_COLORS, SIMULATOR_SHORT_LABELS
    global OUTPUT_ROWS, KNOWN_EXTRAS

    labels = {
        str(spec.get("id") or path.parent.name): str(spec.get("name") or spec.get("id") or path.parent.name)
        for path, spec in spec_entries
    }
    SIMULATOR_ORDER = tuple(sorted(labels, key=lambda sim: (labels[sim].lower(), sim)))
    ORDER["simulator"] = SIMULATOR_ORDER
    SIMULATOR_LABELS = labels
    SIMULATOR_COLORS = {
        sim: SIMULATOR_COLOR_PALETTE[index % len(SIMULATOR_COLOR_PALETTE)]
        for index, sim in enumerate(SIMULATOR_ORDER)
    }
    SIMULATOR_SHORT_LABELS = {
        sim: _short_label(SIMULATOR_LABELS[sim], sim)
        for sim in SIMULATOR_ORDER
    }

    extras: set[str] = set()
    for _path, spec in spec_entries:
        for cap in spec.get("capabilities", []) or []:
            extras.update(str(extra) for extra in cap.get("native_extras", []) or [])
            extras.update(str(extra) for extra in cap.get("derivable_extras", []) or [])
    ordered_extras = [extra for extra in PREFERRED_EXTRA_ORDER if extra in extras]
    ordered_extras.extend(sorted(extras - set(ordered_extras)))
    extra_rows = tuple((extra, _input_spec_filename(extra), "extra") for extra in ordered_extras)
    OUTPUT_ROWS = (*BASE_OUTPUT_ROWS, *extra_rows, ("__native_outputs__", "native/raw outputs", "native_outputs"))
    KNOWN_EXTRAS = set(ordered_extras)


def load_capabilities(spec_entries: list[tuple[Path, dict]]) -> list[Capability]:
    capabilities: list[Capability] = []
    seen_simulators: set[str] = set()
    index = 0
    for spec_path, spec in spec_entries:
        simulator = spec.get("id", spec_path.parent.name)
        seen_simulators.add(simulator)
        for cap in spec.get("capabilities", []):
            axes = cap.get("data_axes", {})
            contexts = tuple(cap.get("truth_requirements", {}).get("contexts", []))
            native_extras = set(cap.get("native_extras", []) or [])
            derivable_extras = set(cap.get("derivable_extras", []) or [])
            truth_contexts = {
                item.get("context"): item.get("status")
                for item in cap.get("truth_contexts", [])
                if isinstance(item, dict)
            }
            truth_is_derived = any(truth_contexts.get(context) == "derivable" for context in contexts)
            truth = truth_label(contexts)
            truth_origin = "derived" if truth_is_derived else "native"
            capabilities.append(
                Capability(
                    index=index,
                    simulator=simulator,
                    resolution=str(axes["resolution"]),
                    column_kind=str(axes["column_kind"]),
                    experimental_design=str(axes["experimental_design"]),
                    truth=truth,
                    truth_is_derived=truth_is_derived,
                    truth_node=f"{truth}|{truth_origin}",
                    native_extras=frozenset(native_extras),
                    derivable_extras=frozenset(derivable_extras),
                    native_output_count=len(cap.get("native_outputs", []) or []),
                )
            )
            index += 1
    missing = set(SIMULATOR_ORDER) - seen_simulators
    if missing:
        raise SystemExit(f"Figure order includes simulators without executable specs: {sorted(missing)}")
    return capabilities


def truth_label(contexts: Iterable[str]) -> str:
    values = set(contexts)
    if "column" in values:
        return "global + group + column"
    if "group" in values:
        return "global + group"
    return "global"


def flow_key(cap: Capability) -> tuple[int, int, int, int, int, int]:
    return (
        ordered_index("simulator", cap.simulator),
        ordered_index("resolution", cap.resolution),
        ordered_index("column_kind", cap.column_kind),
        ordered_index("experimental_design", cap.experimental_design),
        ordered_index("truth", cap.truth_node),
        cap.index,
    )


def ordered_index(stage: str, value: str) -> int:
    order = tuple(ORDER.get(stage, ()))
    return order.index(value) if value in order else len(order)


def layout_nodes(capabilities: list[Capability]) -> dict[str, dict[str, dict[str, object]]]:
    flow_width = 7.8
    flow_gap = 1.6
    top, bottom = 178.0, 622.0
    layouts: dict[str, dict[str, dict[str, object]]] = {}
    sorted_caps = sorted(capabilities, key=flow_key)
    for stage, _label in STAGES:
        values = {cap.value_for(stage) for cap in capabilities}
        present_values = [value for value in ORDER[stage] if value in values]
        present_values.extend(sorted(values - set(present_values)))
        spans: dict[str, float] = {}
        value_caps: dict[str, list[Capability]] = {}
        for value in present_values:
            caps = [cap for cap in sorted_caps if cap.value_for(stage) == value]
            value_caps[value] = caps
            spans[value] = max(20.0, len(caps) * flow_width + max(0, len(caps) - 1) * flow_gap)
        total_span = sum(spans.values())
        gaps = value_gaps(stage, present_values)
        needed = total_span + sum(gaps)
        if needed > bottom - top and gaps:
            scale = max(0.35, ((bottom - top) - total_span) / sum(gaps))
            gaps = [max(5.0, gap * scale) for gap in gaps]
            needed = total_span + sum(gaps)
        y = top + max(0.0, ((bottom - top) - needed) / 2)
        stage_layout: dict[str, dict[str, object]] = {}
        for value in present_values:
            span = spans[value]
            caps = value_caps[value]
            centers: dict[int, float] = {}
            inner = len(caps) * flow_width + max(0, len(caps) - 1) * flow_gap
            start = y + (span - inner) / 2 + flow_width / 2
            for offset, cap in enumerate(caps):
                centers[cap.index] = start + offset * (flow_width + flow_gap)
            stage_layout[value] = {
                "y0": y,
                "y1": y + span,
                "yc": y + span / 2,
                "centers": centers,
                "count": len(caps),
            }
            if len(stage_layout) - 1 < len(gaps):
                y += span + gaps[len(stage_layout) - 1]
            else:
                y += span
        layouts[stage] = stage_layout
    return layouts


def value_gaps(stage: str, present_values: list[str]) -> list[float]:
    if len(present_values) <= 1:
        return []
    if stage != "truth":
        return [22.0] * (len(present_values) - 1)
    gaps: list[float] = []
    for left, right in zip(present_values, present_values[1:]):
        gaps.append(7.0 if truth_base(left) == truth_base(right) else 28.0)
    return gaps


def truth_base(value: str) -> str:
    return value.split("|", 1)[0]


def truth_origin(value: str) -> str:
    return value.split("|", 1)[1]


def draw_title(canvas: Canvas) -> None:
    canvas.rect(0, 0, WIDTH, HEIGHT, fill=BACKGROUND, stroke="none")
    canvas.text(34, 34, "Simulator semantic coverage", size=20, bold=True, fill=INK)
    canvas.text(
        34,
        56,
        "Executable capabilities generated directly from ANDREA simulator specifications.",
        size=11.0,
        fill=MUTED,
    )


def draw_extras_panel(canvas: Canvas, capabilities: list[Capability]) -> None:
    x, y, w, h = 850, 82, 342, 570
    canvas.rect(x, y, w, h, fill=PANEL, stroke="#d6cec1", stroke_width=1.1, rx=18)
    canvas.text(x + 18, y + 30, "OUTPUT FILES", size=13.5, bold=True, fill=INK)
    canvas.text(x + 18, y + 50, "columns follow the simulator names in the flow", size=9.4, fill=MUTED)

    if len(SIMULATOR_ORDER) == 1:
        marker_x = {SIMULATOR_ORDER[0]: x + w - 54}
    else:
        start_x = x + 160
        end_x = x + w - 32
        step = max(13.0, min(22.0, (end_x - start_x) / max(1, len(SIMULATOR_ORDER) - 1)))
        marker_x = {sim: start_x + i * step for i, sim in enumerate(SIMULATOR_ORDER)}
    header_y = y + 79
    for sim in SIMULATOR_ORDER:
        sx = marker_x[sim]
        canvas.circle(sx, header_y - 3, 3.8, fill=SIMULATOR_COLORS[sim], stroke="none")
        canvas.text(
            sx,
            header_y + 12,
            SIMULATOR_SHORT_LABELS[sim],
            size=7.0,
            fill=SIMULATOR_COLORS[sim],
            bold=True,
            anchor="middle",
        )
        canvas.line(sx, header_y + 20, sx, y + h - 76, stroke="#ece4d8", stroke_width=0.7)

    statuses = output_statuses_by_simulator(capabilities)
    row_start = y + 116
    row_step = 18.0
    for row_index, (row_id, label, kind) in enumerate(OUTPUT_ROWS):
        ry = row_start + row_index * row_step
        if row_index % 2 == 0:
            canvas.rect(x + 12, ry - 10, w - 24, row_step, fill="#fbf7ef", stroke="none", alpha=0.48)
        canvas.text(x + 18, ry + 4, label, size=8.9, fill=INK if kind != "core" else MUTED, bold=kind == "core")
        for sim in SIMULATOR_ORDER:
            draw_output_marker(canvas, marker_x[sim], ry, statuses[sim].get(row_id))

    legend_y = y + h - 38
    legend_items = (
        ("core", "core"),
        ("native", "native"),
        ("derived", "derivable"),
        ("raw", "native_outputs"),
    )
    legend_gap = 76
    legend_width = legend_gap * (len(legend_items) - 1) + 42
    legend_x = x + (w - legend_width) / 2
    for label, status in legend_items:
        draw_output_marker(canvas, legend_x, legend_y, status)
        canvas.text(legend_x + 10, legend_y + 4, label, size=8.0, fill=MUTED)
        legend_x += legend_gap


def output_statuses_by_simulator(capabilities: list[Capability]) -> dict[str, dict[str, str]]:
    statuses: dict[str, dict[str, str]] = {sim: {} for sim in SIMULATOR_ORDER}
    for sim in SIMULATOR_ORDER:
        sim_caps = [cap for cap in capabilities if cap.simulator == sim]
        if not sim_caps:
            continue
        for row_id, _label, kind in OUTPUT_ROWS:
            if kind == "core":
                statuses[sim][row_id] = "core"
            elif kind == "native_outputs":
                if any(cap.native_output_count for cap in sim_caps):
                    statuses[sim][row_id] = "native_outputs"
            elif any(row_id in cap.native_extras for cap in sim_caps):
                statuses[sim][row_id] = "native"
            elif any(row_id in cap.derivable_extras for cap in sim_caps):
                statuses[sim][row_id] = "derivable"
    return statuses


def draw_output_marker(canvas: Canvas, x: float, y: float, status: str | None) -> None:
    if status is None:
        canvas.circle(x, y, 2.2, fill="#ffffff", stroke="#d6cec1", stroke_width=0.8)
        return
    color = OUTPUT_STATUS_COLORS[status]
    if status == "derivable":
        canvas.circle(x, y, 4.8, fill="#ffffff", stroke=color, stroke_width=1.4)
        canvas.circle(x, y, 2.0, fill=blend(color, PANEL, 0.45), stroke="none")
    else:
        canvas.circle(x, y, 4.9, fill=blend(color, PANEL, 0.75), stroke=color, stroke_width=1.0)


def draw_alluvial(canvas: Canvas, capabilities: list[Capability]) -> None:
    layouts = layout_nodes(capabilities)
    x0, y0, w, h = 28, 82, 806, 570
    canvas.rect(x0, y0, w, h, fill="#fffefa", stroke="#d6cec1", stroke_width=1.1, rx=18)
    canvas.text(x0 + 18, y0 + 30, "SEMANTIC CAPABILITY FLOW", size=13.8, bold=True, fill=INK)
    canvas.text(
        x0 + 18,
        y0 + 50,
        "one ribbon per executable capability; ribbon color follows the simulator name",
        size=9.1,
        fill=MUTED,
    )

    for stage, label in STAGES:
        x = STAGE_X[stage]
        canvas.text(x, 154, label, size=9.5, fill=MUTED, bold=True, anchor="middle")

    stage_names = [stage for stage, _label in STAGES]
    for left, right in zip(stage_names, stage_names[1:]):
        x1 = STAGE_X[left] + NODE_WIDTH[left] / 2 + 4
        x2 = STAGE_X[right] - NODE_WIDTH[right] / 2 - 4
        for cap in sorted(capabilities, key=flow_key):
            y1 = layouts[left][cap.value_for(left)]["centers"][cap.index]
            y2 = layouts[right][cap.value_for(right)]["centers"][cap.index]
            canvas.band(x1, y1, x2, y2, 7.4, fill=SIMULATOR_COLORS[cap.simulator], alpha=0.40)

    for stage, _label in STAGES:
        x = STAGE_X[stage]
        for value, node in layouts[stage].items():
            y = float(node["yc"])
            count = int(node["count"])
            y_start = float(node["y0"]) - 3
            block_h = float(node["y1"]) - float(node["y0"]) + 6
            node_w = NODE_WIDTH[stage]
            if stage == "simulator":
                color = SIMULATOR_COLORS[value]
                label = SIMULATOR_LABELS[value]
                canvas.rect(x - node_w / 2, y_start, node_w, block_h, fill=blend(color, PANEL, 0.10), stroke=color, stroke_width=1.2, rx=7)
                canvas.text(x - 28, y + 4, label, size=9.3, fill=color, bold=True, anchor="end")
                canvas.text(x + 17, y + 4, str(count), size=8.3, fill=MUTED, bold=True)
            elif stage == "truth":
                granularity = truth_base(value)
                origin = truth_origin(value)
                origin_color = "#2f855a" if origin == "native" else "#2b6cb0"
                display = granularity.replace(" + ", "+")
                canvas.rect(x - node_w / 2, y_start, node_w, block_h, fill=blend(origin_color, PANEL, 0.08), stroke=origin_color, stroke_width=1.1, rx=6)
                chip_w = max(74.0, min(104.0, text_width(display, 7.5, bold=True) + 24.0))
                chip_x = x + 14
                canvas.rect(chip_x, y - 11, chip_w, 22, fill="#ffffff", stroke="#d9d0c3", stroke_width=0.9, rx=8)
                canvas.text(chip_x + 8, y - 1, display, size=7.5, fill=INK, bold=True)
                canvas.text(chip_x + 8, y + 8, origin, size=6.5, fill=origin_color, bold=True)
                canvas.text(chip_x + chip_w - 8, y + 3, str(count), size=7.3, fill=MUTED, bold=True, anchor="end")
            else:
                label = LABELS.get(value, value.replace("_", "-"))
                canvas.rect(x - node_w / 2, y_start, node_w, block_h, fill="#ffffff", stroke="#d9d0c3", stroke_width=1.0, rx=6)
                chip_w = min(116.0, max(52.0, text_width(label, 8.4, bold=True) + 28.0))
                canvas.rect(x - chip_w / 2, y - 9, chip_w, 18, fill="#ffffff", stroke="#d9d0c3", stroke_width=0.8, rx=7)
                canvas.text(x - 5, y + 4, label, size=8.4, fill=INK, bold=True, anchor="middle")
                canvas.text(x + chip_w / 2 - 8, y + 4, str(count), size=7.3, fill=MUTED, bold=True, anchor="end")



def main() -> None:
    spec_entries = _spec_entries()
    configure_from_specs(spec_entries)
    capabilities = load_capabilities(spec_entries)
    if len(capabilities) != sum(1 for _ in capabilities):
        raise SystemExit("Internal capability accounting error.")
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    canvas = Canvas(WIDTH, HEIGHT)
    draw_title(canvas)
    draw_extras_panel(canvas, capabilities)
    draw_alluvial(canvas, capabilities)
    canvas.save(SVG_PATH, PDF_PATH)
    print(f"Wrote {SVG_PATH.relative_to(ROOT)}")
    print(f"Wrote {PDF_PATH.relative_to(ROOT)}")
    print(f"Rendered {len(capabilities)} executable simulator capabilities.")


if __name__ == "__main__":
    main()
