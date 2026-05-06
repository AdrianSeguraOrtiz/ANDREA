#!/usr/bin/env python3
"""Run infer-network and evaluate-inference for every dataset in a benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rich import print  # noqa: E402

from andrea.core.commands.evaluate_inference import evaluate_inference  # noqa: E402
from andrea.core.commands.infer_network import infer_network  # noqa: E402
from andrea.core.shared.paths import report_path  # noqa: E402


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def _resolve(root: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    return path if path.is_absolute() else root / path


def _benchmark_datasets(benchmark_dir: Path) -> list[dict[str, Path | str]]:
    manifest = _load_json(benchmark_dir / "benchmark-manifest.json")
    datasets = manifest.get("datasets")
    if not isinstance(datasets, list):
        raise ValueError("benchmark-manifest.json must contain a datasets list")

    resolved: list[dict[str, Path | str]] = []
    for entry in datasets:
        if not isinstance(entry, dict):
            raise ValueError("Every benchmark dataset entry must be an object")
        dataset_id = str(entry["dataset_id"])
        resolved.append(
            {
                "dataset_id": dataset_id,
                "dataset_manifest": _resolve(
                    benchmark_dir, str(entry["dataset_manifest"])
                ),
                "ground_truth_manifest": _resolve(
                    benchmark_dir, str(entry["ground_truth_manifest"])
                ),
            }
        )
    return resolved


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def run_benchmark(
    *,
    benchmark_dir: Path,
    tools_params: Path,
    output_dir: Path,
    max_cores: int,
    max_ram_gb: float | None,
    no_view: bool,
) -> Path:
    benchmark_dir = benchmark_dir.resolve()
    tools_params = tools_params.resolve()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_root = (output_dir / f"benchmark_inference_{timestamp}").resolve()
    inference_root = run_root / "inferred_networks"
    evaluation_root = run_root / "evaluations"
    summary_path = run_root / "benchmark_run_report.json"
    benchmark_manifest = _load_json(benchmark_dir / "benchmark-manifest.json")
    summary_base = {
        "schema_version": "1.0",
        "benchmark": {
            "id": benchmark_manifest.get("id"),
            "profile": benchmark_manifest.get("profile"),
            "organism": benchmark_manifest.get("organism"),
        },
        "tools_params": {
            "source": "provided",
            "sha256": _sha256_file(tools_params),
        },
        "output_dir": ".",
    }
    rows: list[dict[str, Any]] = []

    for index, dataset in enumerate(_benchmark_datasets(benchmark_dir), start=1):
        dataset_id = str(dataset["dataset_id"])
        dataset_manifest = Path(dataset["dataset_manifest"])
        ground_truth_manifest = Path(dataset["ground_truth_manifest"])
        print(f"[bold cyan]{index}.[/bold cyan] {dataset_id}")

        row: dict[str, Any] = {
            "dataset_id": dataset_id,
        }
        try:
            run_dir = infer_network(
                dataset_manifest_path=dataset_manifest,
                tools_params_path=tools_params,
                output_dir=inference_root,
                max_cores=max_cores,
                max_ram_gb=max_ram_gb,
            )
            run_report = run_dir / "run_report.json"
            evaluation = evaluate_inference(
                run_report_path=run_report,
                ground_truth_manifest_path=ground_truth_manifest,
                output_dir=evaluation_root,
                generate_view=not no_view,
            )
            row.update(
                {
                    "status": "completed",
                    "run_dir": report_path(run_dir, base_dir=run_root),
                    "run_report": report_path(run_report, base_dir=run_root),
                    "evaluation_dir": report_path(
                        evaluation_root / str(evaluation["outputs"]["evaluation_dir"]),
                        base_dir=run_root,
                    ),
                    "evaluation_report": report_path(
                        evaluation_root
                        / str(evaluation["outputs"]["evaluation_report"]),
                        base_dir=run_root,
                    ),
                    "metrics_csv": report_path(
                        evaluation_root / str(evaluation["outputs"]["metrics_csv"]),
                        base_dir=run_root,
                    ),
                    "evaluation_view": (
                        report_path(
                            evaluation_root
                            / str(evaluation["outputs"]["evaluation_view"]),
                            base_dir=run_root,
                        )
                        if evaluation["outputs"].get("evaluation_view")
                        else None
                    ),
                }
            )
        except Exception as exc:  # noqa: BLE001 - keep benchmark loops running.
            row.update(
                {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            print(
                f"[bold red]failed:[/bold red] {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )

        rows.append(row)
        _write_json(summary_path, {**summary_base, "datasets": rows})

    _write_json(
        summary_path,
        {**summary_base, "datasets": rows},
    )
    return summary_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark-dir", required=True, type=Path)
    parser.add_argument("--tools-params", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("benchmark_runs"))
    parser.add_argument("--max-cores", type=int, default=multiprocessing.cpu_count())
    parser.add_argument("--max-ram-gb", type=float)
    parser.add_argument("--no-view", action="store_true")
    args = parser.parse_args()

    summary_path = run_benchmark(
        benchmark_dir=args.benchmark_dir,
        tools_params=args.tools_params,
        output_dir=args.output_dir,
        max_cores=args.max_cores,
        max_ram_gb=args.max_ram_gb,
        no_view=args.no_view,
    )
    print(f"[bold green]summary:[/bold green] {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
