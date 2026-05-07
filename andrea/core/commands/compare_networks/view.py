"""HTML view rendering for network comparison reports."""

from __future__ import annotations

import html
import json
from importlib import resources
from pathlib import Path
from typing import Any

from andrea.core.commands.compare_networks.models import VIEW_ASSETS_PACKAGE


def write_comparison_view(path: Path, report: dict[str, Any]) -> None:
    path.write_text(comparison_view_html(report), encoding="utf-8")


def comparison_view_html(report: dict[str, Any]) -> str:
    title = "ANDREA Network Comparison"
    request_id = report.get("request", {}).get("id")
    if request_id:
        title = f"{title} - {request_id}"
    report_json = json.dumps(report, ensure_ascii=True).replace("</", "<\\/")
    return (
        read_view_asset("template.html")
        .replace("__TITLE__", html.escape(title, quote=True))
        .replace("__STYLE__", read_view_asset("view.css"))
        .replace("__SCRIPT__", read_view_asset("view.js"))
        .replace("__REPORT_JSON__", report_json)
    )


def read_view_asset(name: str) -> str:
    return (
        resources.files(VIEW_ASSETS_PACKAGE).joinpath(name).read_text(encoding="utf-8")
    )
