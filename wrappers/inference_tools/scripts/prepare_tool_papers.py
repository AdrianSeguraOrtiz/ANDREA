"""Accept local TXT evidence and optionally extract PDF papers to sidecars.

Usage examples:
1) Extract local paper PDFs for one tool:
   python prepare_tool_papers.py --tool genie3

2) Re-extract every local PDF under tools/*/papers:
   python prepare_tool_papers.py --force

The script validates TXT and optionally extracts PDFs under:
  wrappers/inference_tools/tools/<tool_id>/papers/

For each PDF it writes a text file next to it:
- `<name>.txt` by default
- `article.txt` when the PDF is named `source.pdf`
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from shared.catalog_tools import (
    DEFAULT_TOOL_SOURCES_ROOT,
    discover_tool_source_dirs,
    select_tools,
)
from shared.paper_text import (
    discover_paper_files,
    extract_pdf_to_text,
    output_path_for_pdf,
    validate_paper_text,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Accept local paper TXT directly; extract optional PDFs under "
            "wrappers/inference_tools/tools/<tool_id>/papers into text sidecars."
        )
    )
    parser.add_argument(
        "--tool-sources-root",
        type=Path,
        default=DEFAULT_TOOL_SOURCES_ROOT,
        help=f"Path to tool source directories. Default: {DEFAULT_TOOL_SOURCES_ROOT}",
    )
    parser.add_argument(
        "--tool",
        action="append",
        default=[],
        help="Tool id to operate on (repeatable). If omitted, scans every tool source dir.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-extract text even if the output .txt already exists.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop after the first failed extraction.",
    )
    return parser.parse_args(argv)


def papers_root_for(tool_dir: Path) -> Path:
    return tool_dir / "papers"


def extract_pdf(
    pdf_path: Path,
    output_path: Path,
    *,
    force: bool,
    pdftotext: str | None = None,
) -> str:
    existed_before = output_path.exists()
    if existed_before and not force:
        validate_paper_text(output_path)
        return "skipped"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    extracted, error = extract_pdf_to_text(
        pdf_path=pdf_path,
        output_path=output_path,
        pdftotext_bin=pdftotext,
    )
    if not extracted:
        raise RuntimeError(f"{pdf_path}: {error or 'pdftotext failed'}")
    return "updated" if existed_before else "created"


def run_for_tool(
    *,
    tool_id: str,
    tool_dir: Path,
    force: bool,
    pdftotext: str | None = None,
) -> tuple[int, int]:
    papers_root = papers_root_for(tool_dir)
    if not papers_root.exists() or not papers_root.is_dir():
        print(f"[{tool_id}] no papers directory: {papers_root}")
        return 0, 0

    text_paths, pdf_paths = discover_paper_files(papers_root)
    regenerated_paths = (
        {output_path_for_pdf(path) for path in pdf_paths} if force else set()
    )
    for text_path in text_paths:
        if text_path in regenerated_paths:
            continue
        validate_paper_text(text_path)
        print(f"  TXT     {text_path}")
    if not pdf_paths:
        print(
            f"[{tool_id}] usable TXT files={len(text_paths)}; PDF not required. "
            "Bibliography, extraction quality and scientific scope still require review."
        )
        return 0, 0

    converted = 0
    skipped = 0
    print(f"[{tool_id}] extracting {len(pdf_paths)} PDF file(s)")
    for pdf_path in pdf_paths:
        output_path = output_path_for_pdf(pdf_path)
        status = extract_pdf(
            pdf_path,
            output_path,
            force=force,
            pdftotext=pdftotext,
        )
        if status == "skipped":
            skipped += 1
        else:
            converted += 1
        print(f"  {status.upper():7s} {pdf_path} -> {output_path}")
    return converted, skipped


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        discovered = discover_tool_source_dirs(args.tool_sources_root)
        selected = select_tools(discovered, args.tool)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    total_converted = 0
    total_skipped = 0
    failed = 0
    for tool_id, tool_dir in selected:
        try:
            converted, skipped = run_for_tool(
                tool_id=tool_id,
                tool_dir=tool_dir,
                force=args.force,
            )
        except RuntimeError as exc:
            failed += 1
            print(f"[{tool_id}] ERROR: {exc}")
            if args.fail_fast:
                break
            continue
        total_converted += converted
        total_skipped += skipped

    print()
    print(
        "Summary: converted={converted} skipped={skipped} failed_tools={failed}".format(
            converted=total_converted,
            skipped=total_skipped,
            failed=failed,
        )
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
