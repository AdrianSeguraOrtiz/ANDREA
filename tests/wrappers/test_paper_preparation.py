from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def paper_module(kind: str):
    script_root = "inference_tools" if kind == "inference" else "simulation_data_tools"
    module_path = REPO_ROOT / f"wrappers/{script_root}/scripts/shared/paper_text.py"
    spec = importlib.util.spec_from_file_location(f"paper_text_{kind}", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("kind", ["inference", "simulation"])
def test_txt_only_evidence_does_not_require_pdftotext(
    tmp_path: Path, kind: str
) -> None:
    papers = tmp_path / "toy/papers"
    papers.mkdir(parents=True)
    article = papers / "article.TXT"
    article.write_text(
        "Example article with observed time series and identified method.\n"
    )
    if kind == "inference":
        script = REPO_ROOT / "wrappers/inference_tools/scripts/prepare_tool_papers.py"
        flags = ["--tool-sources-root", str(tmp_path), "--tool", "toy"]
    else:
        script = (
            REPO_ROOT
            / "wrappers/simulation_data_tools/scripts/prepare_simulator_papers.py"
        )
        flags = ["--simulator-evidence-root", str(tmp_path), "--simulator", "toy"]
    env = dict(os.environ, PATH=str(tmp_path / "no-binaries"))
    result = subprocess.run(
        [sys.executable, str(script), *flags],
        env=env,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "usable TXT files=1; PDF not required" in result.stdout
    assert article.read_text().startswith("Example article")


@pytest.mark.parametrize("kind", ["inference", "simulation"])
def test_existing_text_sidecar_does_not_require_extractor(
    tmp_path: Path, kind: str
) -> None:
    papers = tmp_path / "toy/papers"
    papers.mkdir(parents=True)
    (papers / "source.PDF").write_bytes(b"unused PDF evidence")
    (papers / "article.txt").write_text("Reviewed article text.\n")
    if kind == "inference":
        script = REPO_ROOT / "wrappers/inference_tools/scripts/prepare_tool_papers.py"
        flags = ["--tool-sources-root", str(tmp_path), "--tool", "toy"]
    else:
        script = (
            REPO_ROOT
            / "wrappers/simulation_data_tools/scripts/prepare_simulator_papers.py"
        )
        flags = ["--simulator-evidence-root", str(tmp_path), "--simulator", "toy"]
    result = subprocess.run(
        [sys.executable, str(script), *flags],
        env=dict(os.environ, PATH=""),
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "skipped=1" in result.stdout
    assert (papers / "article.txt").read_text() == "Reviewed article text.\n"


@pytest.mark.parametrize("kind", ["inference", "simulation"])
def test_extraction_failure_preserves_existing_txt(
    tmp_path: Path, kind: str, monkeypatch
) -> None:
    module = paper_module(kind)
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"placeholder")
    output = tmp_path / "article.txt"
    output.write_text("Reviewed article text.\n")

    def failed_extract(command, **_kwargs):
        Path(command[-1]).write_text("partial corrupt extraction")
        return subprocess.CompletedProcess(command, 1, "", "Cannot parse PDF")

    monkeypatch.setattr(module.subprocess, "run", failed_extract)
    ok, error = module.extract_pdf_to_text(
        pdf_path=pdf, output_path=output, pdftotext_bin="fake"
    )
    assert not ok and "Cannot parse PDF" in error
    assert output.read_text() == "Reviewed article text.\n"
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.parametrize("kind", ["inference", "simulation"])
def test_empty_extraction_is_rejected_and_existing_txt_preserved(
    tmp_path: Path, kind: str, monkeypatch
) -> None:
    module = paper_module(kind)
    output = tmp_path / "article.txt"
    output.write_text("Reviewed article text.\n")
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, "", ""),
    )
    ok, error = module.extract_pdf_to_text(
        pdf_path=tmp_path / "source.pdf", output_path=output, pdftotext_bin="fake"
    )
    assert not ok and "Empty paper text" in error
    assert output.read_text() == "Reviewed article text.\n"


@pytest.mark.parametrize("kind", ["inference", "simulation"])
def test_successful_extraction_atomically_replaces_prior_text(
    tmp_path: Path, kind: str, monkeypatch
) -> None:
    module = paper_module(kind)
    output = tmp_path / "article.txt"
    output.write_text("Prior text.\n")

    def successful_extract(command, **_kwargs):
        Path(command[-1]).write_text("Complete extracted article text.\n")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(module.subprocess, "run", successful_extract)
    ok, error = module.extract_pdf_to_text(
        pdf_path=tmp_path / "source.pdf", output_path=output, pdftotext_bin="fake"
    )
    assert ok and error is None
    assert output.read_text() == "Complete extracted article text.\n"
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.parametrize("kind", ["inference", "simulation"])
def test_paper_destination_collision_is_rejected(tmp_path: Path, kind: str) -> None:
    module = paper_module(kind)
    (tmp_path / "source.PDF").write_bytes(b"placeholder")
    (tmp_path / "article.pdf").write_bytes(b"placeholder")
    with pytest.raises(RuntimeError, match="destination collision"):
        module.discover_paper_files(tmp_path)


@pytest.mark.parametrize("kind", ["inference", "simulation"])
def test_missing_extractor_is_reported_without_traceback(
    tmp_path: Path, kind: str
) -> None:
    module = paper_module(kind)
    ok, error = module.extract_pdf_to_text(
        pdf_path=tmp_path / "source.pdf",
        output_path=tmp_path / "article.txt",
        pdftotext_bin=str(tmp_path / "missing-pdftotext"),
    )
    assert not ok
    assert "missing-pdftotext" in error
