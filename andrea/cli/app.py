"""Top-level CLI for ANDREA."""

from __future__ import annotations

import json
import multiprocessing
from pathlib import Path
from typing import Optional

import typer
from rich import print
from rich.markup import escape

from andrea.config import __version__
from andrea.core.commands.evaluate_inference import (
    evaluate_inference as core_evaluate_inference,
)
from andrea.core.commands.generate_data import (
    execute_generate_data as core_execute_generate_data,
)
from andrea.core.commands.generate_data import (
    plan_generate_data_request as core_plan_generate_data_request,
)
from andrea.core.commands.generate_data import (
    preflight_generate_data_scenario as core_preflight_generate_data_scenario,
)
from andrea.core.commands.generate_data import (
    run_generate_data as core_run_generate_data,
)
from andrea.core.commands.infer_network import infer_network as core_infer_network
from andrea.core.commands.infer_network import (
    plan_infer_network as core_plan_infer_network,
)
from andrea.core.commands.infer_network import (
    preflight_infer_network as core_preflight_infer_network,
)
from andrea.core.commands.infer_network import (
    run_infer_network_plan as core_run_infer_network_plan,
)


def _version_callback(value: bool) -> None:
    if value:
        print(f"ANDREA {__version__}")
        raise typer.Exit()


def _run_core(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ValueError as exc:
        print(f"[bold red]Error:[/bold red] {escape(str(exc))}")
        raise typer.Exit(code=1)


app = typer.Typer(
    no_args_is_help=True,
    rich_markup_mode="rich",
    help="Platform workflows for network inference, simulation, and benchmarking.",
)

infer_network_app = typer.Typer(
    no_args_is_help=True,
    help="ToolSpec-driven infer-network pipeline commands.",
)
generate_data_app = typer.Typer(
    no_args_is_help=True,
    help="Simulation and benchmark generation workflows.",
)
gui_app = typer.Typer(
    no_args_is_help=True,
    help="Graphical interfaces for ANDREA workflows.",
)

app.add_typer(infer_network_app, name="infer-network", rich_help_panel="Workflows")
app.add_typer(generate_data_app, name="generate-data", rich_help_panel="Workflows")
app.add_typer(gui_app, name="gui", rich_help_panel="Interfaces")


@app.callback()
def root(
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        help="Show ANDREA version and exit.",
        callback=_version_callback,
        is_eager=True,
    )
) -> None:
    """ANDREA command line interface."""


@app.command("evaluate-inference", rich_help_panel="Workflows")
def evaluate_inference_command(
    run_report: Path = typer.Option(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        help="Path to run_report.json. The raw merged network is read from this report.",
    ),
    ground_truth_manifest: Path = typer.Option(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        help="Path to ground-truth-manifest.json.",
    ),
    output_dir: Path = typer.Option(
        Path("./evaluations"),
        help="Root directory where a timestamped evaluation package will be created.",
    ),
    plots: bool = typer.Option(
        True,
        "--plots/--no-plots",
        help="Write SVG evaluation heatmaps.",
    ),
):
    """Evaluate inferred GRNs against benchmark ground truth."""
    report = _run_core(
        core_evaluate_inference,
        run_report_path=run_report,
        ground_truth_manifest_path=ground_truth_manifest,
        output_dir=output_dir,
        generate_plots=plots,
    )
    metrics = report.get("metrics", [])
    evaluated = sum(1 for row in metrics if row.get("status") in {"ok", "partial"})
    not_applicable = sum(1 for row in metrics if row.get("status") == "not_applicable")
    print("[bold green]inference evaluation completed[/bold green]")
    print(f"  evaluated metrics: {evaluated}")
    print(f"  not applicable metrics: {not_applicable}")
    print(f"  evaluation: {report['outputs']['evaluation_dir']}")
    print(f"  report: {report['outputs']['evaluation_report']}")
    print(f"  metrics: {report['outputs']['metrics_csv']}")
    if report["outputs"].get("plots_dir"):
        print(f"  plots: {report['outputs']['plots_dir']}")


@infer_network_app.command("preflight")
def infer_network_preflight(
    dataset_manifest: Path = typer.Option(
        ...,
        exists=True,
        file_okay=True,
        help="Path to dataset-manifest.json (includes embedded dataset spec).",
    ),
    tools_params: Optional[Path] = typer.Option(
        None,
        exists=True,
        file_okay=True,
        help=(
            "Optional tools_params.json to pre-validate requested runs "
            "({'runs': [{'run_id': ..., 'tool_id': ..., 'params': ..., "
            "'execution': {'mode': 'global|group_native|group_emulated'}}, ...]})."
        ),
    ),
    output_json: Optional[Path] = typer.Option(
        None,
        help="Optional output path to persist preflight report JSON.",
    ),
    strict: bool = typer.Option(
        False,
        help="If true, incompatible tools/params raise an error during preflight.",
    ),
):
    """Validate dataset inputs and compute tool eligibility before planning."""
    report = _run_core(
        core_preflight_infer_network,
        dataset_manifest_path=dataset_manifest,
        tools_params_path=tools_params,
        strict=strict,
    )
    if output_json is not None:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(
            json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
        )
        print(f"[bold green]preflight report written[/bold green]: {output_json}")
    else:
        eligible = len(report.get("catalog", {}).get("eligible", []))
        warning = len(report.get("catalog", {}).get("warning", []))
        blocked = len(report.get("catalog", {}).get("blocked", []))
        selected = len(report.get("runs", {}).get("selected", []))
        skipped = len(report.get("runs", {}).get("skipped", {}))
        requirement_issues = report.get("runs", {}).get("requirement_issues", {})
        requirement_issue_runs = (
            len(requirement_issues) if isinstance(requirement_issues, dict) else 0
        )
        print("[bold green]infer-network preflight completed[/bold green]")
        print(f"  eligible tools: {eligible}")
        print(f"  warning tools: {warning}")
        print(f"  blocked tools: {blocked}")
        print(f"  selected runs: {selected}")
        print(f"  skipped runs: {skipped}")
        print(f"  runs with conditional input issues: {requirement_issue_runs}")
        if isinstance(requirement_issues, dict):
            for run_id in sorted(requirement_issues):
                messages = requirement_issues.get(run_id, [])
                if not isinstance(messages, list):
                    continue
                for message in messages:
                    print(f"    - {escape(f'[{run_id}] {message}')}")


@infer_network_app.command("plan")
def infer_network_plan(
    dataset_manifest: Path = typer.Option(
        ...,
        exists=True,
        file_okay=True,
        help="Path to dataset-manifest.json (includes embedded dataset spec).",
    ),
    tools_params: Path = typer.Option(
        ...,
        exists=True,
        file_okay=True,
        help=(
            "Path to tools_params.json in runs format: "
            "{'runs': [{'run_id': ..., 'tool_id': ..., 'params': ..., "
            "'execution': {'mode': 'global|group_native|group_emulated'}}, ...]}."
        ),
    ),
    output_dir: Path = typer.Option(
        Path("./inferred_networks"),
        help="Output root directory for this orchestration run.",
    ),
    max_cores: int = typer.Option(
        multiprocessing.cpu_count(),
        help="Maximum number of CPU cores available to the execution planner.",
    ),
    max_ram_gb: Optional[float] = typer.Option(
        None,
        help="Maximum RAM (GB) available to the execution planner. If omitted, host RAM is used.",
    ),
    planner: str = typer.Option(
        "auto",
        help="Planning strategy: auto, cp_sat, heuristic.",
    ),
    planner_time_limit_seconds: float = typer.Option(
        10.0,
        help="Time limit in seconds for cp_sat planning attempts.",
    ),
    strict: bool = typer.Option(
        False,
        help="If true, incompatible tools/params raise an error.",
    ),
):
    """Generate a frozen run directory and plan.json without executing containers."""
    _run_core(
        core_plan_infer_network,
        dataset_manifest_path=dataset_manifest,
        tools_params_path=tools_params,
        output_dir=output_dir,
        max_cores=max_cores,
        max_ram_gb=max_ram_gb,
        planner=planner,
        planner_time_limit_seconds=planner_time_limit_seconds,
        strict=strict,
    )


@infer_network_app.command("run")
def infer_network_run(
    run_dir: Path = typer.Option(
        ...,
        exists=True,
        file_okay=False,
        dir_okay=True,
        help="Path to a frozen run directory produced by infer-network plan.",
    ),
    progress_poll_seconds: float = typer.Option(
        0.5,
        help="Polling interval in seconds for reading per-tool progress.json during execution.",
    ),
    strict: bool = typer.Option(
        False,
        help="If true, runtime tool failures raise an error.",
    ),
):
    """Execute a previously generated plan from run_dir."""
    _run_core(
        core_run_infer_network_plan,
        run_dir=run_dir,
        progress_poll_seconds=progress_poll_seconds,
        strict=strict,
    )


@infer_network_app.command("execute")
def infer_network_execute(
    dataset_manifest: Path = typer.Option(
        ...,
        exists=True,
        file_okay=True,
        help="Path to dataset-manifest.json (includes embedded dataset spec).",
    ),
    tools_params: Path = typer.Option(
        ...,
        exists=True,
        file_okay=True,
        help=(
            "Path to tools_params.json in runs format: "
            "{'runs': [{'run_id': ..., 'tool_id': ..., 'params': ..., "
            "'execution': {'mode': 'global|group_native|group_emulated'}}, ...]}."
        ),
    ),
    output_dir: Path = typer.Option(
        Path("./inferred_networks"),
        help="Output root directory for this orchestration run.",
    ),
    max_cores: int = typer.Option(
        multiprocessing.cpu_count(),
        help="Maximum number of CPU cores available to the execution planner.",
    ),
    max_ram_gb: Optional[float] = typer.Option(
        None,
        help="Maximum RAM (GB) available to the execution planner. If omitted, host RAM is used.",
    ),
    planner: str = typer.Option(
        "auto",
        help="Planning strategy: auto, cp_sat, heuristic.",
    ),
    planner_time_limit_seconds: float = typer.Option(
        10.0,
        help="Time limit in seconds for cp_sat planning attempts.",
    ),
    progress_poll_seconds: float = typer.Option(
        0.5,
        help="Polling interval in seconds for reading per-tool progress.json during execution.",
    ),
    strict: bool = typer.Option(
        False,
        help="If true, incompatible tools/params and runtime tool failures raise an error.",
    ),
):
    """End-to-end execution wrapper (preflight + plan + run)."""
    _run_core(
        core_infer_network,
        dataset_manifest_path=dataset_manifest,
        tools_params_path=tools_params,
        output_dir=output_dir,
        max_cores=max_cores,
        max_ram_gb=max_ram_gb,
        planner=planner,
        planner_time_limit_seconds=planner_time_limit_seconds,
        progress_poll_seconds=progress_poll_seconds,
        strict=strict,
    )


@generate_data_app.callback()
def generate_data_root() -> None:
    """Generate-data namespace bootstrap."""


@generate_data_app.command("preflight")
def generate_data_preflight(
    scenario: Path = typer.Option(
        ...,
        exists=True,
        file_okay=True,
        help="Path to scenario-request.json.",
    ),
    output_json: Optional[Path] = typer.Option(
        None,
        help="Optional output path to persist preflight report JSON.",
    ),
):
    """Classify simulators for a scenario-first generate-data request."""
    report = _run_core(core_preflight_generate_data_scenario, scenario)
    if output_json is not None:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(
            json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
        )
        print(f"[bold green]preflight report written[/bold green]: {output_json}")
    summary = report["catalog_summary"]
    print("[bold green]generate-data scenario preflight[/bold green]")
    print(f"  scenario id: {report['scenario']['id']}")
    print(f"  profile: {report['scenario']['profile']}")
    print(
        f"  requested extras: {', '.join(report['scenario']['requested_extras']) or '(none)'}"
    )
    print(
        f"  effective extras: {', '.join(report['scenario']['effective_extras']) or '(none)'}"
    )
    print(f"  input files: {', '.join(report['scenario']['input_files']) or '(none)'}")
    print(
        f"  catalog summary: total={summary['total']} "
        f"eligible={summary['eligible']} warning={summary['warning']} blocked={summary['blocked']}"
    )
    for bucket in ("eligible", "warning", "blocked"):
        entries = report[bucket]
        if not entries:
            continue
        print(f"  {bucket}:")
        for entry in entries:
            suffix = ""
            reasons = (
                entry["blocking_reasons"] if bucket == "blocked" else entry["warnings"]
            )
            if reasons:
                suffix = " - " + "; ".join(reasons)
            print(f"    - {entry['simulator_id']}{suffix}")


@generate_data_app.command("plan")
def generate_data_plan(
    scenario: Path = typer.Option(
        ...,
        exists=True,
        file_okay=True,
        help="Path to scenario-request.json.",
    ),
    simulator_runs: Path = typer.Option(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        help="Path to simulator-runs.json.",
    ),
    out: Path = typer.Option(
        ...,
        help="Path where the resolved simulation-plan.json will be written.",
    ),
    max_parallel_tasks: int = typer.Option(
        multiprocessing.cpu_count(),
        min=1,
        help="Maximum simulator tasks to run concurrently when this plan is executed.",
    ),
):
    """Resolve a scenario request into a runnable simulation-plan.json."""
    output_path = _run_core(
        core_plan_generate_data_request,
        scenario_request_path=scenario,
        simulator_runs_path=simulator_runs,
        output_path=out,
        max_parallel_tasks=max_parallel_tasks,
    )
    print(f"[bold green]simulation plan written[/bold green]: {output_path}")


@generate_data_app.command("run")
def generate_data_run(
    plan: Path = typer.Option(
        ...,
        exists=True,
        file_okay=True,
        help="Path to simulation-plan.json.",
    ),
    output_dir: Path = typer.Option(
        Path("./benchmarks"),
        help="Root directory where the benchmark package will be created.",
    ),
    max_parallel_tasks: Optional[int] = typer.Option(
        None,
        min=1,
        help="Optional override for the plan's max_parallel_tasks.",
    ),
    progress_poll_seconds: float = typer.Option(
        0.5,
        help="Polling interval in seconds for reading per-simulator progress.json.",
    ),
):
    """Generate a benchmark package from a resolved simulation-plan.json."""
    benchmark_root = _run_core(
        core_run_generate_data,
        plan_path=plan,
        output_dir=output_dir,
        max_parallel_tasks=max_parallel_tasks,
        progress_poll_seconds=progress_poll_seconds,
    )
    print(f"[bold green]benchmark written[/bold green]: {benchmark_root}")


@generate_data_app.command("execute")
def generate_data_execute(
    scenario: Path = typer.Option(
        ...,
        exists=True,
        file_okay=True,
        help="Path to scenario-request.json.",
    ),
    simulator_runs: Path = typer.Option(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        help="Path to simulator-runs.json.",
    ),
    output_dir: Path = typer.Option(
        Path("./benchmarks"),
        help="Root directory where the benchmark package will be created.",
    ),
    max_parallel_tasks: int = typer.Option(
        multiprocessing.cpu_count(),
        min=1,
        help="Maximum simulator tasks to run concurrently.",
    ),
    progress_poll_seconds: float = typer.Option(
        0.5,
        help="Polling interval in seconds for reading per-simulator progress.json.",
    ),
):
    """End-to-end simulation-data wrapper (preflight + plan + run)."""
    benchmark_root = _run_core(
        core_execute_generate_data,
        scenario_request_path=scenario,
        simulator_runs_path=simulator_runs,
        output_dir=output_dir,
        max_parallel_tasks=max_parallel_tasks,
        progress_poll_seconds=progress_poll_seconds,
    )
    print(f"[bold green]benchmark written[/bold green]: {benchmark_root}")


@gui_app.command("infer-network")
def gui_infer_network(
    host: str = typer.Option(
        "127.0.0.1",
        help="Host address for the local GUI server.",
    ),
    port: int = typer.Option(
        8765,
        min=1,
        max=65535,
        help="Port for the local GUI server.",
    ),
    open_browser: bool = typer.Option(
        False,
        "--open-browser/--no-open-browser",
        help=(
            "Automatically open the GUI in your default browser. "
            "Disabled by default to avoid SSH/remote session confusion."
        ),
    ),
):
    """Launch the local graphical interface for infer-network."""
    from andrea.gui.infer_network.server import run_server

    run_server(host=host, port=port, open_browser=open_browser)


@gui_app.command("generate-data")
def gui_generate_data(
    host: str = typer.Option(
        "127.0.0.1",
        help="Host address for the local GUI server.",
    ),
    port: int = typer.Option(
        8766,
        min=1,
        max=65535,
        help="Port for the local GUI server.",
    ),
    open_browser: bool = typer.Option(
        False,
        "--open-browser/--no-open-browser",
        help=(
            "Automatically open the GUI in your default browser. "
            "Disabled by default to avoid SSH/remote session confusion."
        ),
    ),
):
    """Launch the local graphical interface for generate-data."""
    from andrea.gui.generate_data.server import run_server

    run_server(host=host, port=port, open_browser=open_browser)


@gui_app.callback()
def gui_root() -> None:
    """GUI namespace bootstrap."""
