"""Local paper evidence: TXT is sufficient; PDF extraction is optional."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


def resolve_pdftotext_bin() -> str:
    binary = shutil.which("pdftotext")
    if binary is None:
        raise RuntimeError("pdftotext not available in PATH")
    return binary


def output_path_for_pdf(pdf_path: Path) -> Path:
    if pdf_path.name.lower() == "source.pdf":
        return pdf_path.with_name("article.txt")
    return pdf_path.with_suffix(".txt")


def discover_paper_files(papers_root: Path) -> tuple[list[Path], list[Path]]:
    files = sorted(path for path in papers_root.rglob("*") if path.is_file())
    texts = [path for path in files if path.suffix.lower() == ".txt"]
    pdfs = [path for path in files if path.suffix.lower() == ".pdf"]
    outputs: dict[Path, Path] = {}
    for pdf in pdfs:
        output = output_path_for_pdf(pdf)
        if output in outputs:
            raise RuntimeError(
                f"PDF text destination collision: {outputs[output]} and {pdf} -> {output}. "
                "Rename one PDF before extraction."
            )
        outputs[output] = pdf
    return texts, pdfs


def validate_paper_text(path: Path) -> None:
    """Reject unreadable/empty evidence without claiming scientific completeness."""
    try:
        content = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"Cannot read UTF-8 paper text {path}: {exc}") from exc
    if not content.strip():
        raise RuntimeError(
            f"Empty paper text: {path}; supply usable TXT or review OCR."
        )


def extract_pdf_to_text(
    *,
    pdf_path: Path,
    output_path: Path,
    pdftotext_bin: str | None = None,
) -> tuple[bool, str | None]:
    binary = pdftotext_bin
    if binary is None:
        try:
            binary = resolve_pdftotext_bin()
        except RuntimeError as exc:
            return False, str(exc)

    # Extract atomically so a failed/empty conversion cannot destroy prior TXT.
    temporary_path: Path | None = None
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        result = subprocess.run(
            [binary, "-layout", str(pdf_path), str(temporary_path)],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            return (
                False,
                result.stderr.strip() or result.stdout.strip() or "pdftotext failed",
            )
        validate_paper_text(temporary_path)
        temporary_path.replace(output_path)
        return True, None
    except (OSError, RuntimeError, UnicodeError) as exc:
        return False, str(exc)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
