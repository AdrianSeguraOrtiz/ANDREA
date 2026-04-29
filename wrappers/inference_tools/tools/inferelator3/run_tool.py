"""Inferelator 3.0 wrapper for the inference_tools execution contract."""

from __future__ import annotations

import argparse
import contextlib
import csv
import json
import math
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import joblib
import numpy as np
import pandas as pd

from _run_tool_common import (
    load_params,
    require_param_keys,
    validate_runtime_inputs,
    warn_unknown_params,
    write_progress,
)


EXPECTED_PARAMS = {
    "regression",
    "num_bootstraps",
    "random_seed",
    "bsr_feature_num",
    "prior_weight",
    "no_prior_weight",
    "clr_only",
}


@dataclass(frozen=True)
class ResolvedParams:
    regression: str
    num_bootstraps: int
    random_seed: int
    bsr_feature_num: int
    prior_weight: float
    no_prior_weight: float
    clr_only: bool


@dataclass(frozen=True)
class PreparedInputs:
    expression_file: Path
    tf_file: Path
    prior_file: Path
    metadata_file: Optional[Path]
    target_count: int
    regulator_count: int
    group_count: int


@dataclass(frozen=True)
class ResolvedExecution:
    mode: str


class ProgressTracker:
    def __init__(
        self,
        *,
        progress_path: Path,
        mi_total: int,
        bbsr_total: int,
    ) -> None:
        self.progress_path = progress_path
        self.mi_total = max(1, int(mi_total))
        self.bbsr_total = max(1, int(bbsr_total))
        self.mi_completed = 0
        self.bbsr_completed = 0

    @staticmethod
    def _scaled_percent(done: int, total: int, start: int, end: int) -> int:
        frac = min(max(done / max(1, total), 0.0), 1.0)
        return start + int(frac * (end - start))

    def update(self, function_name: str) -> None:
        if function_name == "_mi_wrapper":
            self.mi_completed += 1
            write_progress(
                self.progress_path,
                status="running",
                percent=self._scaled_percent(self.mi_completed, self.mi_total, 20, 45),
                phase="mutual_information",
                message="Calculating mutual information and CLR scores",
                completed=self.mi_completed,
                total=self.mi_total,
            )
        elif function_name == "_bbsr_regression_wrapper":
            self.bbsr_completed += 1
            write_progress(
                self.progress_path,
                status="running",
                percent=self._scaled_percent(
                    self.bbsr_completed, self.bbsr_total, 45, 90
                ),
                phase="target_genes",
                message="Regressing target genes with BBSR",
                completed=self.bbsr_completed,
                total=self.bbsr_total,
            )


def _append_log(log_path: Path, message: str) -> None:
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(message.rstrip())
        fh.write("\n")


def _require_int(value: Any, name: str, *, minimum: Optional[int] = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer.")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}.")
    return value


def _require_float(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric.")
    out = float(value)
    if not math.isfinite(out):
        raise ValueError(f"{name} must be finite.")
    return out


def _resolve_params(raw_params: dict[str, Any]) -> ResolvedParams:
    require_param_keys(raw_params, EXPECTED_PARAMS)
    warn_unknown_params(raw_params, EXPECTED_PARAMS)

    regression = raw_params["regression"]
    if not isinstance(regression, str):
        raise ValueError("regression must be a string.")
    regression = regression.strip()
    if regression not in {"auto", "bbsr", "amusr", "bbsr-by-task", "elasticnet-by-task"}:
        raise ValueError(
            "regression must be one of: auto, bbsr, amusr, bbsr-by-task, elasticnet-by-task."
        )

    clr_only = raw_params["clr_only"]
    if not isinstance(clr_only, bool):
        raise ValueError("clr_only must be a boolean.")

    return ResolvedParams(
        regression=regression,
        num_bootstraps=_require_int(
            raw_params["num_bootstraps"], "num_bootstraps", minimum=1
        ),
        random_seed=_require_int(raw_params["random_seed"], "random_seed"),
        bsr_feature_num=_require_int(
            raw_params["bsr_feature_num"], "bsr_feature_num", minimum=1
        ),
        prior_weight=_require_float(raw_params["prior_weight"], "prior_weight"),
        no_prior_weight=_require_float(
            raw_params["no_prior_weight"], "no_prior_weight"
        ),
        clr_only=clr_only,
    )


def _load_execution(params_path: Path) -> ResolvedExecution:
    execution_path = params_path.parent / "execution.json"
    if not execution_path.exists():
        return ResolvedExecution(mode="global")
    with execution_path.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, dict):
        raise ValueError("execution.json must be a JSON object.")
    mode = str(raw.get("mode", "global")).strip() or "global"
    if mode not in {"global", "group_native", "group_emulated"}:
        raise ValueError("execution.mode must be one of: global, group_native, group_emulated.")
    return ResolvedExecution(mode=mode)


def _read_expression_tsv(expr_path: Path) -> pd.DataFrame:
    df = pd.read_csv(expr_path, sep="\t", header=0)
    if df.shape[1] < 2:
        raise ValueError(
            "expression.tsv must have at least 2 columns: gene + >=1 observation."
        )

    gene_col = df.columns[0]
    genes = df[gene_col].astype(str)
    if genes.str.len().eq(0).any():
        raise ValueError("expression.tsv contains an empty gene identifier.")
    if genes.duplicated().any():
        duplicated = sorted(genes[genes.duplicated()].unique().tolist())
        raise ValueError(f"expression.tsv contains duplicated genes: {duplicated}")

    numeric = df.drop(columns=[gene_col]).apply(pd.to_numeric, errors="raise")
    if not pd.Series(numeric.to_numpy().ravel()).map(math.isfinite).all():
        raise ValueError("expression.tsv contains non-finite expression values.")

    numeric.index = genes
    numeric.index.name = "gene"
    return numeric


def _read_tf_list(tf_path: Path, expression_genes: set[str]) -> list[str]:
    tf_names: list[str] = []
    seen: set[str] = set()
    for line in tf_path.read_text(encoding="utf-8").splitlines():
        token = line.strip()
        if not token or token.startswith("#"):
            continue
        if token in seen:
            raise ValueError(f"tf_list.txt contains duplicated TF: {token}")
        seen.add(token)
        tf_names.append(token)

    if not tf_names:
        raise ValueError("tf_list.txt does not contain any regulators.")

    missing = sorted(set(tf_names).difference(expression_genes))
    if missing:
        raise ValueError(f"tf_list.txt contains TFs not present in expression: {missing}")
    return tf_names


def _read_prior_grn(
    prior_path: Path,
    *,
    genes: list[str],
    tf_names: list[str],
) -> pd.DataFrame:
    prior_edges = pd.read_csv(prior_path, sep="\t", header=0)
    required = {"source", "target", "score"}
    missing = sorted(required.difference(prior_edges.columns))
    if missing:
        raise ValueError(f"prior_grn.tsv is missing required columns: {missing}")

    gene_set = set(genes)
    tf_set = set(tf_names)
    prior = pd.DataFrame(0.0, index=genes, columns=tf_names)
    seen_edges: set[tuple[str, str]] = set()
    nonzero_edges = 0

    for line_no, row in enumerate(prior_edges.to_dict(orient="records"), start=2):
        source = str(row["source"]).strip()
        target = str(row["target"]).strip()
        try:
            score = float(row["score"])
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                f"Invalid prior_grn.tsv score at line {line_no}: {row['score']!r}"
            ) from exc

        if not source or not target:
            raise ValueError(f"prior_grn.tsv has an empty source/target at line {line_no}.")
        if source not in tf_set:
            raise ValueError(
                f"prior_grn.tsv source {source!r} at line {line_no} is not in tf_list.txt."
            )
        if target not in gene_set:
            raise ValueError(
                f"prior_grn.tsv target {target!r} at line {line_no} is not in expression.tsv."
            )
        if not math.isfinite(score):
            raise ValueError(f"prior_grn.tsv score at line {line_no} is not finite.")

        edge = (source, target)
        if edge in seen_edges:
            raise ValueError(f"prior_grn.tsv contains duplicated edge: {source}->{target}")
        seen_edges.add(edge)

        prior.loc[target, source] = score
        if score != 0.0:
            nonzero_edges += 1

    if nonzero_edges == 0:
        raise ValueError("prior_grn.tsv must contain at least one non-zero edge.")

    return prior


def _read_groups_tsv(groups_path: Path, expression_columns: list[str]) -> pd.DataFrame:
    groups = pd.read_csv(groups_path, sep="\t", header=0)
    if groups.shape[1] < 2:
        raise ValueError("groups.tsv must have at least 2 columns.")
    if "cluster" not in groups.columns:
        raise ValueError("groups.tsv must include a 'cluster' column.")

    sample_col = groups.columns[0]
    groups[sample_col] = groups[sample_col].astype(str)
    groups["cluster"] = groups["cluster"].astype(str)
    if groups[sample_col].str.len().eq(0).any() or groups["cluster"].str.len().eq(0).any():
        raise ValueError("groups.tsv contains empty sample or cluster values.")
    if groups[sample_col].duplicated().any():
        duplicated = sorted(groups.loc[groups[sample_col].duplicated(), sample_col].unique().tolist())
        raise ValueError(f"groups.tsv contains duplicated expression columns: {duplicated}")

    expression_set = set(expression_columns)
    missing = sorted(set(groups[sample_col]).difference(expression_set))
    if missing:
        raise ValueError(f"groups.tsv contains samples not present in expression.tsv: {missing}")
    unassigned = sorted(expression_set.difference(set(groups[sample_col])))
    if unassigned:
        raise ValueError(f"groups.tsv does not assign all expression columns: {unassigned}")
    if groups["cluster"].nunique() < 2:
        raise ValueError("group_native execution requires at least two groups in groups.tsv.")

    return groups[[sample_col, "cluster"]].rename(columns={sample_col: "condName"})


def _prepare_upstream_inputs(
    *,
    input_path: Path,
    tf_path: Path,
    prior_path: Path,
    groups_path: Optional[Path],
    runtime_dir: Path,
) -> PreparedInputs:
    runtime_dir.mkdir(parents=True, exist_ok=True)

    expression = _read_expression_tsv(input_path)
    genes = expression.index.astype(str).tolist()
    tf_names = _read_tf_list(tf_path, set(genes))
    prior = _read_prior_grn(prior_path, genes=genes, tf_names=tf_names)

    upstream_expr = expression.transpose()
    upstream_expr.index.name = "sample"

    expression_file = runtime_dir / "expression_samples_by_genes.tsv"
    tf_file = runtime_dir / "tf_list.txt"
    prior_file = runtime_dir / "prior_genes_by_tfs.tsv"
    metadata_file: Optional[Path] = None
    group_count = 0

    upstream_expr.to_csv(expression_file, sep="\t")
    tf_file.write_text("\n".join(tf_names) + "\n", encoding="utf-8")
    prior.to_csv(prior_file, sep="\t")
    if groups_path is not None:
        groups = _read_groups_tsv(groups_path, upstream_expr.index.astype(str).tolist())
        groups["isTs"] = True
        groups["prevCol"] = "NA"
        groups["del.t"] = "NA"
        groups = groups[["condName", "isTs", "prevCol", "del.t", "cluster"]]
        metadata_file = runtime_dir / "metadata.tsv"
        groups.to_csv(metadata_file, sep="\t", index=False)
        group_count = int(groups["cluster"].nunique())

    return PreparedInputs(
        expression_file=expression_file,
        tf_file=tf_file,
        prior_file=prior_file,
        metadata_file=metadata_file,
        target_count=len(genes),
        regulator_count=len(tf_names),
        group_count=group_count,
    )


def _make_progress_controller(progress: ProgressTracker):
    from inferelator.distributed import AbstractController

    class ProgressJoblibController(AbstractController):
        _controller_name = "joblib-progress"
        _require_initialization = False
        processes = 1
        tracker = progress

        @classmethod
        def connect(cls, *args: Any, **kwargs: Any) -> bool:
            return True

        @classmethod
        def set_processes(cls, process_count: int) -> None:
            cls.processes = _require_int(process_count, "process_count", minimum=1)

        @classmethod
        def map(cls, func: Any, *args: Any, scatter: Any = None, **kwargs: Any) -> list[Any]:
            _ = scatter
            jobs = list(zip(*args))
            if not jobs:
                return []

            function_name = getattr(func, "__name__", "")
            results: list[Any] = []
            with joblib.parallel_config(backend="loky", inner_max_num_threads=1):
                generator = joblib.Parallel(
                    n_jobs=cls.processes,
                    return_as="generator",
                )(joblib.delayed(func)(*job_args, **kwargs) for job_args in jobs)
                for result in generator:
                    results.append(result)
                    cls.tracker.update(function_name)
            return results

        @classmethod
        def shutdown(cls) -> bool:
            return True

    return ProgressJoblibController


def _install_numpy_compatibility_shims() -> None:
    if hasattr(np, "isdtype"):
        return

    dtype_kinds = {
        "bool": np.bool_,
        "complex floating": np.complexfloating,
        "floating": np.floating,
        "integral": np.integer,
        "numeric": np.number,
        "real floating": np.floating,
        "signed integer": np.signedinteger,
        "unsigned integer": np.unsignedinteger,
    }

    def _isdtype(dtype: object, kind: object) -> bool:
        if isinstance(kind, tuple):
            return any(_isdtype(dtype, subkind) for subkind in kind)
        if isinstance(kind, str):
            kind = dtype_kinds.get(kind, kind)
        return np.issubdtype(np.dtype(dtype), kind)

    np.isdtype = _isdtype  # type: ignore[attr-defined]


def _resolve_upstream_regression(params: ResolvedParams, execution: ResolvedExecution) -> str:
    if execution.mode == "group_native":
        if params.regression == "auto":
            return "amusr"
        if params.regression not in {"amusr", "bbsr-by-task", "elasticnet-by-task"}:
            raise ValueError(
                "execution.mode=group_native requires regression to be auto, amusr, "
                "bbsr-by-task, or elasticnet-by-task."
            )
        return params.regression

    if params.regression in {"auto", "bbsr"}:
        return "bbsr"
    raise ValueError(
        "global and group_emulated execution currently support regression=auto or regression=bbsr."
    )


def _set_common_output_names(worker: Any) -> None:
    worker.set_output_file_names(
        network_file_name="network.tsv.gz",
        confidence_file_name="combined_confidences.tsv.gz",
        nonzero_coefficient_file_name="model_coefficients.tsv.gz",
        pdf_curve_file_name=None,
        curve_data_file_name=None,
        model_h5_file_name="inferelator_model.h5ad",
    )


def _set_regression_parameters(
    *,
    worker: Any,
    upstream_regression: str,
    params: ResolvedParams,
) -> None:
    if upstream_regression in {"bbsr", "bbsr-by-task"}:
        worker.set_regression_parameters(
            prior_weight=params.prior_weight,
            no_prior_weight=params.no_prior_weight,
            bsr_feature_num=params.bsr_feature_num,
            clr_only=params.clr_only,
        )
    elif upstream_regression == "amusr":
        worker.set_regression_parameters(prior_weight=params.prior_weight)


def _run_inferelator(
    *,
    prepared: PreparedInputs,
    params: ResolvedParams,
    execution: ResolvedExecution,
    raw_dir: Path,
    progress_path: Path,
    threads: int,
    log_path: Path,
) -> None:
    _install_numpy_compatibility_shims()

    from inferelator import inferelator_workflow
    from inferelator.distributed.inferelator_mp import MPControl
    from inferelator.utils import inferelator_verbose_level

    raw_dir.mkdir(parents=True, exist_ok=True)

    inferelator_verbose_level(0, log_to_stderr=False)
    upstream_regression = _resolve_upstream_regression(params, execution)

    # Each bootstrap plus the final full model runs MI/CLR and then BBSR.
    regression_runs = params.num_bootstraps + 1
    tracker = ProgressTracker(
        progress_path=progress_path,
        mi_total=(prepared.target_count + prepared.regulator_count) * regression_runs,
        bbsr_total=prepared.target_count * regression_runs,
    )
    controller = _make_progress_controller(tracker)
    MPControl.set_multiprocess_engine(controller, processes=threads)
    MPControl.connect()

    if execution.mode == "group_native":
        if prepared.metadata_file is None:
            raise ValueError("execution.mode=group_native requires groups.tsv.")
        worker = inferelator_workflow(regression=upstream_regression, workflow="multitask")
        worker.set_file_paths(
            input_dir=str(prepared.expression_file.parent),
            output_dir=str(raw_dir),
            tf_names_file=prepared.tf_file.name,
            priors_file=prepared.prior_file.name,
        )
        task = worker.create_task(
            task_name="groups",
            input_dir=str(prepared.expression_file.parent),
            expression_matrix_file=prepared.expression_file.name,
            meta_data_file=prepared.metadata_file.name,
            tf_names_file=prepared.tf_file.name,
            priors_file=prepared.prior_file.name,
            workflow_type="single-cell",
            tasks_from_metadata=True,
            meta_data_task_column="cluster",
        )
        task.set_file_properties(expression_matrix_columns_are_genes=True)
    else:
        worker = inferelator_workflow(regression=upstream_regression, workflow="tfa")
        worker.set_file_paths(
            input_dir=str(prepared.expression_file.parent),
            output_dir=str(raw_dir),
            expression_matrix_file=prepared.expression_file.name,
            tf_names_file=prepared.tf_file.name,
            priors_file=prepared.prior_file.name,
        )
        worker.set_file_properties(expression_matrix_columns_are_genes=True)

    worker.set_network_data_flags(use_no_gold_standard=True)
    _set_common_output_names(worker)
    worker.set_run_parameters(
        num_bootstraps=params.num_bootstraps,
        random_seed=params.random_seed,
    )
    _set_regression_parameters(
        worker=worker,
        upstream_regression=upstream_regression,
        params=params,
    )

    _append_log(
        log_path,
        (
            f"Running inferelator_workflow(regression='{upstream_regression}', "
            f"workflow='{'multitask' if execution.mode == 'group_native' else 'tfa'}') "
            f"with {threads} worker process(es)"
        ),
    )
    write_progress(
        progress_path,
        status="running",
        percent=20,
        phase="inference",
        message="Running Inferelator workflow",
    )
    try:
        with log_path.open("a", encoding="utf-8") as log_fh:
            with contextlib.redirect_stdout(log_fh), contextlib.redirect_stderr(log_fh):
                worker.run()
    finally:
        MPControl.shutdown()

    _append_log(log_path, f"Raw Inferelator output written under {raw_dir}")


def _edge_sign(row: pd.Series) -> str:
    for col in ("model_coefficient", "beta.sign.sum"):
        if col not in row or pd.isna(row[col]):
            continue
        try:
            value = float(row[col])
        except Exception:  # noqa: BLE001
            continue
        if value > 0:
            return "+"
        if value < 0:
            return "-"
    return "?"


def _network_rows_from_raw(raw_network_path: Path, *, context: str) -> list[dict[str, Any]]:
    if not raw_network_path.exists() or raw_network_path.stat().st_size <= 0:
        raise FileNotFoundError(f"Raw Inferelator network not found: {raw_network_path}")

    raw = pd.read_csv(raw_network_path, sep="\t")
    required = {"target", "regulator", "combined_confidences"}
    missing = sorted(required.difference(raw.columns))
    if missing:
        raise ValueError(f"Raw Inferelator network is missing columns: {missing}")

    rows: list[dict[str, Any]] = []
    for line_no, row in raw.iterrows():
        score = float(row["combined_confidences"])
        if not math.isfinite(score):
            raise ValueError(
                f"Raw Inferelator network has non-finite score at row {line_no + 2}."
            )
        if score == 0.0:
            continue
        rows.append(
            {
                "source": str(row["regulator"]),
                "target": str(row["target"]),
                "score": score,
                "sign": _edge_sign(row),
                "evidence": "association",
                "context": context,
            }
        )

    return rows


def _convert_network(
    *,
    raw_dir: Path,
    network_csv_path: Path,
    execution: ResolvedExecution,
) -> int:
    if execution.mode == "group_native":
        raw_network_paths = [
            path
            for path in sorted(raw_dir.glob("*/network.tsv.gz"))
            if path.parent.name
        ]
        rows: list[dict[str, Any]] = []
        for raw_network_path in raw_network_paths:
            rows.extend(
                _network_rows_from_raw(
                    raw_network_path,
                    context=f"group:{raw_network_path.parent.name}",
                )
            )
        if not raw_network_paths:
            raise FileNotFoundError(
                f"Raw Inferelator task networks not found under: {raw_dir}"
            )
    else:
        rows = _network_rows_from_raw(raw_dir / "network.tsv.gz", context="global")

    if not rows:
        raise ValueError("Inferelator produced no non-zero network edges.")
    rows.sort(key=lambda item: item["score"], reverse=True)

    with network_csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["source", "target", "score", "sign", "evidence", "context"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--params", required=True, type=Path)
    parser.add_argument("--extra", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--threads", required=True, type=int)
    args = parser.parse_args()

    tf_path = args.extra / "tf_list.txt"
    prior_path = args.extra / "prior_grn.tsv"
    groups_path = args.extra / "groups.tsv"
    validate_runtime_inputs(
        input_path=args.input,
        params_path=args.params,
        extra_dir=args.extra,
        threads=args.threads,
        required_paths=[tf_path, prior_path],
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)

    progress_path = args.output_dir / "progress.json"
    log_path = args.output_dir / "inferelator.log"
    raw_dir = args.output_dir / "raw"
    runtime_dir = args.output_dir / "runtime"

    write_progress(
        progress_path,
        status="running",
        percent=0,
        phase="init",
        message="Initializing Inferelator wrapper",
    )
    _append_log(log_path, "Inferelator 3.0 wrapper initialized")

    try:
        raw_params = load_params(args.params)
        params = _resolve_params(raw_params)
        execution = _load_execution(args.params)
        if execution.mode == "group_native" and not groups_path.exists():
            raise FileNotFoundError("groups.tsv is required for execution.mode=group_native.")

        write_progress(
            progress_path,
            status="running",
            percent=5,
            phase="load_input",
            message="Preparing expression, TF list, prior GRN and metadata",
        )
        prepared = _prepare_upstream_inputs(
            input_path=args.input,
            tf_path=tf_path,
            prior_path=prior_path,
            groups_path=groups_path if execution.mode == "group_native" else None,
            runtime_dir=runtime_dir,
        )
        _append_log(
            log_path,
            (
                f"Prepared upstream inputs with {prepared.target_count} target genes "
                f"and {prepared.regulator_count} regulators "
                f"(execution.mode={execution.mode})"
            ),
        )

        _run_inferelator(
            prepared=prepared,
            params=params,
            execution=execution,
            raw_dir=raw_dir,
            progress_path=progress_path,
            threads=args.threads,
            log_path=log_path,
        )

        write_progress(
            progress_path,
            status="running",
            percent=95,
            phase="write_output",
            message="Converting Inferelator network to network.csv",
        )
        row_count = _convert_network(
            raw_dir=raw_dir,
            network_csv_path=args.output_dir / "network.csv",
            execution=execution,
        )
        _append_log(log_path, f"Wrote network.csv with {row_count} non-zero edges")

        write_progress(
            progress_path,
            status="completed",
            percent=100,
            phase="done",
            message="Inference finished",
            completed=row_count,
            total=row_count,
        )
    except Exception as exc:
        _append_log(log_path, traceback.format_exc())
        write_progress(
            progress_path,
            status="failed",
            percent=100,
            phase="failed",
            message="Inference failed",
            error=str(exc),
        )
        raise


if __name__ == "__main__":
    main()
