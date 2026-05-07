"""Shared file bundle and preview helpers for local GUI servers."""

from __future__ import annotations

import csv
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any, Optional

MAX_TABLE_PREVIEW_ROWS = 200
MAX_TEXT_PREVIEW_BYTES = 256_000


def read_json_if_exists(path: Optional[str | Path]) -> Optional[dict[str, Any]]:
    if not path:
        return None
    json_path = Path(path)
    if not json_path.exists() or not json_path.is_file():
        return None
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    return data


def save_upload(upload: Any, destination: Path) -> None:
    """Save a FastAPI/Starlette upload object to a local path."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    file_obj = getattr(upload, "file", None)
    if file_obj is None:
        raise ValueError("Invalid uploaded file")
    file_obj.seek(0)
    with destination.open("wb") as out_fh:
        shutil.copyfileobj(file_obj, out_fh)


def extract_zip_upload(upload: Any, *, zip_path: Path, extract_dir: Path) -> None:
    """Save and safely extract an uploaded ZIP archive."""
    filename = str(getattr(upload, "filename", "") or "")
    if not filename.lower().endswith(".zip"):
        raise ValueError(
            f"Uploaded file must be a ZIP archive: {filename or 'unnamed'}"
        )
    save_upload(upload, zip_path)
    if not zipfile.is_zipfile(zip_path):
        raise ValueError(f"Uploaded file is not a valid ZIP archive: {filename}")

    extract_dir.mkdir(parents=True, exist_ok=True)
    root = extract_dir.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            member = Path(info.filename)
            if member.is_absolute() or ".." in member.parts:
                raise ValueError(f"Unsafe ZIP member path: {info.filename}")
            if info.is_dir():
                continue
            destination = (extract_dir / member).resolve()
            if not destination.is_relative_to(root):
                raise ValueError(f"Unsafe ZIP member path: {info.filename}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as in_fh, destination.open("wb") as out_fh:
                shutil.copyfileobj(in_fh, out_fh)


def uploaded_file(form: Any, key: str) -> Any:
    upload = form.get(key)
    if upload is None or not getattr(upload, "filename", ""):
        return None
    return upload


def output_dir_from_form(form: Any, *, default: str) -> Path:
    raw = str(form.get("output_dir") or default).strip() or default
    return Path(raw).expanduser().resolve()


def resolve_report_path(report_path: Path, value: Any) -> Optional[Path]:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    if not path.is_absolute():
        path = report_path.parent / path
    return path.resolve()


def default_viewer_for_path(path: str) -> str:
    normalized = path.lower()
    basename = Path(normalized).name
    if normalized.endswith(".json"):
        return "json"
    if normalized.endswith(".csv"):
        return "table_csv"
    if normalized.endswith(".tsv"):
        return "table_tsv"
    if (
        normalized.endswith(".txt")
        or normalized.endswith(".log")
        or normalized.endswith(".r")
        or normalized.endswith(".py")
        or basename.endswith(".out")
        or basename.endswith(".err")
        or basename.endswith(".stderr")
        or basename.endswith(".stdout")
        or "log" in basename
    ):
        return "text"
    return "none"


def build_bundle_entries(
    sources: list[tuple[str, Path]],
    *,
    viewer_for_path: Any = default_viewer_for_path,
) -> list[dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for virtual_path, source in sources:
        parts = virtual_path.split("/")
        for idx in range(1, len(parts)):
            dir_path = "/".join(parts[:idx])
            entries.setdefault(
                dir_path,
                {
                    "path": dir_path,
                    "kind": "dir",
                    "size_bytes": None,
                    "viewer": "none",
                    "visualizable": False,
                    "depth": max(0, len(parts[:idx]) - 1),
                },
            )

        viewer = str(viewer_for_path(virtual_path))
        entries[virtual_path] = {
            "path": virtual_path,
            "kind": "file",
            "size_bytes": source.stat().st_size if source.exists() else None,
            "viewer": viewer,
            "visualizable": viewer != "none",
            "depth": max(0, len(parts) - 1),
        }

    return sorted(
        entries.values(),
        key=lambda item: (item["kind"] != "dir", item["path"]),
    )


def resolve_virtual_source(
    *,
    sources: list[tuple[str, Path]],
    virtual_path: str,
) -> Optional[Path]:
    for candidate_path, source in sources:
        if candidate_path == virtual_path:
            return source
    return None


def preview_table(
    *,
    source: Path,
    delimiter: str,
    max_rows: int,
) -> dict[str, Any]:
    headers: list[str] = []
    rows: list[list[str]] = []
    total_rows = 0

    with source.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh, delimiter=delimiter)
        first = next(reader, None)
        if first is None:
            return {"headers": [], "rows": [], "total_rows": 0, "truncated": False}
        headers = [str(x) for x in first]
        for row in reader:
            total_rows += 1
            if len(rows) < max_rows:
                rows.append([str(x) for x in row])

    return {
        "headers": headers,
        "rows": rows,
        "total_rows": total_rows,
        "truncated": total_rows > len(rows),
    }


def preview_text(
    source: Path, max_bytes: int = MAX_TEXT_PREVIEW_BYTES
) -> dict[str, Any]:
    with source.open("rb") as fh:
        raw = fh.read(max_bytes + 1)
    truncated = len(raw) > max_bytes
    if truncated:
        raw = raw[:max_bytes]
    text = raw.decode("utf-8", errors="replace")
    return {"text": text, "truncated": truncated, "size_bytes": source.stat().st_size}


def is_probably_text(source: Path, sample_bytes: int = 4096) -> bool:
    try:
        with source.open("rb") as fh:
            raw = fh.read(sample_bytes)
    except Exception:  # noqa: BLE001
        return False
    if not raw:
        return True
    if b"\x00" in raw:
        return False
    return True


def build_zip_bundle(
    *,
    zip_path: Path,
    sources: list[tuple[str, Path]],
) -> Path:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for virtual_path, source in sources:
            if source.exists() and source.is_file():
                zf.write(source, arcname=virtual_path)
    return zip_path
