"""SimiC wrapper for the inference_tools execution contract."""

from __future__ import annotations

import argparse
import contextlib
import csv
import json
import math
import os
import pickle
import random
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from _run_tool_common import (
    load_params,
    require_extra_file,
    require_param_keys,
    validate_runtime_inputs,
    warn_unknown_params,
    write_progress,
)


EXPECTED_PARAMS = {
    "similarity",
    "lambda1",
    "lambda2",
    "cross_val",
    "num_TFs",
    "num_target_genes",
    "normalization_factor",
    "max_rcd_iter",
    "num_rep",
    "random_seed",
    "wauc_percent_of_target",
    "wauc_sort_by",
    "wauc_adj_r2_threshold",
}

SIMIC_TEST_PROPORTION = 0.2
SIMIC_MIN_CELLS_PER_SPLIT = 2
THREAD_ENV_VARS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "BLIS_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)


@dataclass(frozen=True)
class ResolvedParams:
    similarity: bool
    lambda1: float
    lambda2: float
    cross_val: bool
    num_TFs: int
    num_target_genes: int
    normalization_factor: float
    max_rcd_iter: int
    num_rep: int
    random_seed: Optional[int]
    wauc_percent_of_target: float
    wauc_sort_by: str
    wauc_adj_r2_threshold: float


@dataclass(frozen=True)
class PhenotypeAssignments:
    cell_to_assignment: dict[str, int]
    label_by_assignment: dict[int, str]


@dataclass(frozen=True)
class PreparedInputs:
    expression_pickle: Path
    assignment_file: Path
    tf_pickle: Path
    genes: list[str]
    cells: list[str]
    tf_names: list[str]
    phenotype_labels: list[str]


def _append_log(log_path: Path, message: str) -> None:
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(message.rstrip())
        fh.write("\n")


def _configure_runtime_threads(threads: int) -> None:
    if threads < 1:
        raise ValueError("--threads must be a positive integer.")
    for key in THREAD_ENV_VARS:
        os.environ[key] = str(threads)


def _require_bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean.")
    return value


def _require_int(value: Any, name: str, *, minimum: Optional[int] = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer.")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}.")
    return value


def _require_float(
    value: Any,
    name: str,
    *,
    minimum: Optional[float] = None,
    exclusive_minimum: bool = False,
    maximum: Optional[float] = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric.")
    out = float(value)
    if not math.isfinite(out):
        raise ValueError(f"{name} must be finite.")
    if minimum is not None:
        if exclusive_minimum and out <= minimum:
            raise ValueError(f"{name} must be > {minimum}.")
        if not exclusive_minimum and out < minimum:
            raise ValueError(f"{name} must be >= {minimum}.")
    if maximum is not None and out > maximum:
        raise ValueError(f"{name} must be <= {maximum}.")
    return out


def _resolve_random_seed(value: Any) -> Optional[int]:
    if value is None:
        return None
    return _require_int(value, "random_seed")


def _resolve_params(raw_params: dict[str, Any]) -> ResolvedParams:
    require_param_keys(raw_params, EXPECTED_PARAMS)
    warn_unknown_params(raw_params, EXPECTED_PARAMS)

    sort_by = raw_params["wauc_sort_by"]
    if not isinstance(sort_by, str):
        raise ValueError("wauc_sort_by must be a string.")
    sort_by = sort_by.strip()
    if sort_by not in {"expression", "weight", "adj_r2"}:
        raise ValueError("wauc_sort_by must be one of: expression, weight, adj_r2.")

    return ResolvedParams(
        similarity=_require_bool(raw_params["similarity"], "similarity"),
        lambda1=_require_float(raw_params["lambda1"], "lambda1", minimum=0),
        lambda2=_require_float(raw_params["lambda2"], "lambda2", minimum=0),
        cross_val=_require_bool(raw_params["cross_val"], "cross_val"),
        num_TFs=_require_int(raw_params["num_TFs"], "num_TFs", minimum=-1),
        num_target_genes=_require_int(
            raw_params["num_target_genes"], "num_target_genes", minimum=-1
        ),
        normalization_factor=_require_float(
            raw_params["normalization_factor"],
            "normalization_factor",
            minimum=0,
            exclusive_minimum=True,
        ),
        max_rcd_iter=_require_int(
            raw_params["max_rcd_iter"], "max_rcd_iter", minimum=1
        ),
        num_rep=_require_int(raw_params["num_rep"], "num_rep", minimum=1),
        random_seed=_resolve_random_seed(raw_params["random_seed"]),
        wauc_percent_of_target=_require_float(
            raw_params["wauc_percent_of_target"],
            "wauc_percent_of_target",
            minimum=0,
            exclusive_minimum=True,
            maximum=1,
        ),
        wauc_sort_by=sort_by,
        wauc_adj_r2_threshold=_require_float(
            raw_params["wauc_adj_r2_threshold"],
            "wauc_adj_r2_threshold",
            minimum=0,
            maximum=1,
        ),
    )


def _load_execution(params_path: Path) -> str:
    execution_path = params_path.parent / "execution.json"
    if not execution_path.exists():
        return "group_native"
    with execution_path.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, dict):
        raise ValueError("execution.json must be a JSON object.")
    mode = str(raw.get("mode", "group_native")).strip() or "group_native"
    if mode != "group_native":
        raise ValueError("SimiC supports only execution.mode=group_native.")
    return mode


def _read_expression_tsv(expr_path: Path) -> pd.DataFrame:
    df = pd.read_csv(expr_path, sep="\t", header=0)
    if df.shape[1] < 2:
        raise ValueError(
            "expression.tsv must have at least 2 columns: gene + >=1 cell."
        )

    gene_col = df.columns[0]
    genes = df[gene_col].astype(str).str.strip()
    if genes.eq("").any():
        raise ValueError("expression.tsv contains an empty gene identifier.")
    if genes.duplicated().any():
        duplicated = sorted(genes[genes.duplicated()].unique().tolist())
        raise ValueError(f"expression.tsv contains duplicated genes: {duplicated}")

    cells = [str(col).strip() for col in df.columns[1:]]
    if any(not cell for cell in cells):
        raise ValueError("expression.tsv has an empty cell identifier in the header.")
    if len(set(cells)) != len(cells):
        raise ValueError("expression.tsv contains duplicated cell identifiers.")

    numeric = df.drop(columns=[gene_col]).apply(pd.to_numeric, errors="raise")
    numeric.columns = cells
    if not np.isfinite(numeric.to_numpy(dtype=float)).all():
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
        raise ValueError("tf_list.txt does not contain any TFs.")

    missing = sorted(set(tf_names).difference(expression_genes))
    if missing:
        raise ValueError(f"tf_list.txt contains TFs not present in expression: {missing}")
    return tf_names


def _read_cell_phenotypes(
    phenotypes_path: Path,
    *,
    cells: list[str],
) -> PhenotypeAssignments:
    with phenotypes_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        fieldnames = reader.fieldnames or []
        if len(fieldnames) < 3:
            raise ValueError(
                "cell_phenotypes.tsv must have at least 3 columns: cell, phenotype, order."
            )
        cell_col = fieldnames[0]
        missing_cols = sorted({"phenotype", "order"}.difference(fieldnames))
        if missing_cols:
            raise ValueError(f"cell_phenotypes.tsv is missing columns: {missing_cols}")

        cell_to_raw: dict[str, tuple[str, int]] = {}
        phenotype_to_order: dict[str, int] = {}
        order_to_phenotype: dict[int, str] = {}
        for line_no, row in enumerate(reader, start=2):
            cell = str(row.get(cell_col, "")).strip()
            phenotype = str(row.get("phenotype", "")).strip()
            raw_order = str(row.get("order", "")).strip()
            if not cell or not phenotype or not raw_order:
                raise ValueError(
                    f"cell_phenotypes.tsv has an empty cell, phenotype or order at line {line_no}."
                )
            if cell in cell_to_raw:
                raise ValueError(f"cell_phenotypes.tsv contains duplicate cell: {cell}")
            try:
                order = int(raw_order)
            except ValueError as exc:
                raise ValueError(
                    f"cell_phenotypes.tsv order must be an integer at line {line_no}: {raw_order!r}"
                ) from exc

            previous_order = phenotype_to_order.get(phenotype)
            if previous_order is not None and previous_order != order:
                raise ValueError(
                    f"Phenotype {phenotype!r} is assigned multiple orders: {previous_order}, {order}."
                )
            previous_phenotype = order_to_phenotype.get(order)
            if previous_phenotype is not None and previous_phenotype != phenotype:
                raise ValueError(
                    f"Order {order} is assigned to multiple phenotypes: {previous_phenotype!r}, {phenotype!r}."
                )

            phenotype_to_order[phenotype] = order
            order_to_phenotype[order] = phenotype
            cell_to_raw[cell] = (phenotype, order)

    expected = set(cells)
    observed = set(cell_to_raw)
    missing = sorted(expected.difference(observed))
    extra = sorted(observed.difference(expected))
    if missing:
        raise ValueError(f"cell_phenotypes.tsv is missing cells from expression: {missing}")
    if extra:
        raise ValueError(f"cell_phenotypes.tsv contains cells not in expression: {extra}")

    sorted_orders = sorted(order_to_phenotype)
    if not sorted_orders:
        raise ValueError("cell_phenotypes.tsv contains no phenotype assignments.")

    contiguous_by_order = {order: idx for idx, order in enumerate(sorted_orders)}
    label_by_assignment = {
        contiguous_by_order[order]: order_to_phenotype[order] for order in sorted_orders
    }
    cell_to_assignment = {
        cell: contiguous_by_order[cell_to_raw[cell][1]] for cell in cells
    }
    counts = {label: 0 for label in label_by_assignment}
    for assignment in cell_to_assignment.values():
        counts[assignment] += 1
    min_cells_per_phenotype = SIMIC_MIN_CELLS_PER_SPLIT * 2
    too_small = [
        label_by_assignment[label]
        for label, count in counts.items()
        if count < min_cells_per_phenotype
    ]
    if too_small:
        raise ValueError(
            "Each phenotype must have at least four cells for SimiC's train/test split "
            "to retain at least two train and two test cells per phenotype. "
            f"Too small: {too_small}"
        )
    test_size = int(len(cells) * SIMIC_TEST_PROPORTION)
    min_test_size = SIMIC_MIN_CELLS_PER_SPLIT * len(counts)
    max_test_size = len(cells) - min_test_size
    if test_size < min_test_size:
        raise ValueError(
            "SimiC's upstream 20% test split is too small for the number of phenotypes: "
            f"test_size={test_size}, required_at_least={min_test_size}."
        )
    if test_size > max_test_size:
        raise ValueError(
            "SimiC's upstream 20% test split is too large to keep at least two train cells per phenotype: "
            f"test_size={test_size}, allowed_at_most={max_test_size}."
        )

    return PhenotypeAssignments(
        cell_to_assignment=cell_to_assignment,
        label_by_assignment=label_by_assignment,
    )


def _stratified_split_df_and_assignment(
    df_in: pd.DataFrame,
    assignment: Any,
    test_proportion: float = SIMIC_TEST_PROPORTION,
) -> tuple[pd.DataFrame, pd.DataFrame, np.ndarray, np.ndarray]:
    assignment_array = np.asarray(assignment)
    if len(df_in) != len(assignment_array):
        raise ValueError(
            "SimiC split received mismatched expression rows and phenotype assignments."
        )
    labels = sorted(set(assignment_array.tolist()))
    test_size = int(len(assignment_array) * test_proportion)
    min_test_size = SIMIC_MIN_CELLS_PER_SPLIT * len(labels)
    max_test_size = len(assignment_array) - min_test_size
    if test_size < min_test_size:
        raise ValueError(
            "SimiC's upstream 20% test split is too small for the number of phenotypes: "
            f"test_size={test_size}, required_at_least={min_test_size}."
        )
    if test_size > max_test_size:
        raise ValueError(
            "SimiC's upstream 20% test split is too large to keep at least two train cells per phenotype: "
            f"test_size={test_size}, allowed_at_most={max_test_size}."
        )

    test_indices: list[int] = []
    candidate_extra_indices: list[int] = []
    for label in labels:
        label_indices = np.where(assignment_array == label)[0]
        if len(label_indices) < SIMIC_MIN_CELLS_PER_SPLIT * 2:
            raise ValueError(
                "Each phenotype must have at least four cells for SimiC's train/test split "
                "to retain at least two train and two test cells per phenotype."
            )
        permuted = np.random.permutation(label_indices)
        test_indices.extend(permuted[:SIMIC_MIN_CELLS_PER_SPLIT].tolist())
        candidate_extra_indices.extend(
            permuted[SIMIC_MIN_CELLS_PER_SPLIT:-SIMIC_MIN_CELLS_PER_SPLIT].tolist()
        )

    remaining = test_size - len(test_indices)
    if remaining > len(candidate_extra_indices):
        raise ValueError(
            "SimiC's upstream 20% test split cannot satisfy per-phenotype train/test coverage."
        )
    if remaining:
        extra_permuted = np.random.permutation(candidate_extra_indices)
        test_indices.extend(extra_permuted[:remaining].tolist())

    test_index_set = set(test_indices)
    test_idx = np.array(sorted(test_indices), dtype=int)
    train_idx = np.array(
        [idx for idx in range(len(assignment_array)) if idx not in test_index_set],
        dtype=int,
    )

    train_df = df_in.loc[train_idx]
    train_assign = assignment_array[train_idx]
    test_df = df_in.loc[test_idx]
    test_assign = assignment_array[test_idx]
    return train_df, test_df, train_assign, test_assign


def _prepare_upstream_inputs(
    *,
    input_path: Path,
    tf_path: Path,
    phenotypes_path: Path,
    runtime_dir: Path,
) -> PreparedInputs:
    expression = _read_expression_tsv(input_path)
    genes = [str(gene) for gene in expression.index]
    cells = [str(cell) for cell in expression.columns]
    tf_names = _read_tf_list(tf_path, set(genes))
    phenotype_assignments = _read_cell_phenotypes(phenotypes_path, cells=cells)

    non_tf_genes = [
        gene for gene in genes if gene.lower() not in {tf.lower() for tf in tf_names}
    ]
    if not non_tf_genes:
        raise ValueError("SimiC requires at least one non-TF target gene in expression.")

    runtime_dir.mkdir(parents=True, exist_ok=True)
    expression_pickle = runtime_dir / "expression_cells_by_genes.pickle"
    assignment_file = runtime_dir / "phenotype_assignment.txt"
    tf_pickle = runtime_dir / "tf_list.pickle"

    cells_by_genes = expression.T.copy()
    cells_by_genes["label"] = [
        phenotype_assignments.cell_to_assignment[cell] for cell in cells
    ]
    cells_by_genes.to_pickle(expression_pickle)

    with assignment_file.open("w", encoding="utf-8") as fh:
        for cell in cells:
            fh.write(f"{phenotype_assignments.cell_to_assignment[cell]}\n")

    with tf_pickle.open("wb") as fh:
        pickle.dump(tf_names, fh)

    phenotype_labels = [
        phenotype_assignments.label_by_assignment[idx]
        for idx in sorted(phenotype_assignments.label_by_assignment)
    ]
    return PreparedInputs(
        expression_pickle=expression_pickle,
        assignment_file=assignment_file,
        tf_pickle=tf_pickle,
        genes=genes,
        cells=cells,
        tf_names=tf_names,
        phenotype_labels=phenotype_labels,
    )


def _apply_random_seed(seed: Optional[int], log_path: Path) -> None:
    if seed is None:
        _append_log(log_path, "random_seed=null; preserving upstream runtime-dependent randomness")
        return
    random.seed(seed)
    np.random.seed(seed)
    _append_log(log_path, f"Set Python random and numpy.random seed to {seed}")


def _run_simic(
    *,
    prepared: PreparedInputs,
    params: ResolvedParams,
    raw_dir: Path,
    log_path: Path,
    progress_path: Path,
) -> tuple[Path, Path]:
    import simiclasso.clus_regression as clus_regression
    import simiclasso.evaluation_metric as evaluation_metric
    from simiclasso.weighted_AUC_mat import main_fn
    from sklearn.metrics import r2_score as sklearn_r2_score

    def _r2_score_compat(
        y_true: Any,
        y_pred: Any,
        sample_weight: Any = None,
        *,
        multioutput: Any = "uniform_average",
    ) -> Any:
        return sklearn_r2_score(
            y_true,
            y_pred,
            sample_weight=sample_weight,
            multioutput=multioutput,
        )

    evaluation_metric.r2_score = _r2_score_compat
    clus_regression.split_df_and_assignment = _stratified_split_df_and_assignment

    weights_path = raw_dir / "simic_weights.pickle"
    wauc_path = raw_dir / "simic_wauc_matrices.pickle"

    write_progress(
        progress_path,
        status="running",
        percent=20,
        phase="inference",
        message="Running SimiC fused LASSO workflow",
    )
    with log_path.open("a", encoding="utf-8") as log_fh:
        with contextlib.redirect_stdout(log_fh), contextlib.redirect_stderr(log_fh):
            clus_regression.simicLASSO_op(
                p2df=str(prepared.expression_pickle),
                p2assignment=str(prepared.assignment_file),
                similarity=params.similarity,
                p2tf=str(prepared.tf_pickle),
                p2saved_file=str(weights_path),
                k_cluster=len(prepared.phenotype_labels),
                num_TFs=params.num_TFs,
                num_target_genes=params.num_target_genes,
                _NF=params.normalization_factor,
                lambda1=params.lambda1,
                lambda2=params.lambda2,
                cross_val=params.cross_val,
                num_rep=params.num_rep,
                max_rcd_iter=params.max_rcd_iter,
                df_with_label=True,
            )

    write_progress(
        progress_path,
        status="running",
        percent=75,
        phase="weighted_auc",
        message="Computing SimiC weighted AUC matrices",
    )
    with log_path.open("a", encoding="utf-8") as log_fh:
        with contextlib.redirect_stdout(log_fh), contextlib.redirect_stderr(log_fh):
            main_fn(
                p2df=str(prepared.expression_pickle),
                p2res=str(weights_path),
                p2saved_file=str(wauc_path),
                percent_of_target=params.wauc_percent_of_target,
                sort_by=params.wauc_sort_by,
                adj_r2_threshold=params.wauc_adj_r2_threshold,
            )

    return weights_path, wauc_path


def _convert_weights_to_network(
    *,
    weights_path: Path,
    phenotype_labels: list[str],
    network_csv_path: Path,
) -> int:
    with weights_path.open("rb") as fh:
        payload = pickle.load(fh)

    if not isinstance(payload, dict):
        raise ValueError("SimiC weight artifact must contain a dictionary.")
    weight_dic = payload.get("weight_dic")
    tf_ids = list(payload.get("TF_ids", []))
    target_ids = list(payload.get("query_targets", []))
    if not isinstance(weight_dic, dict) or not tf_ids or not target_ids:
        raise ValueError("SimiC weight artifact is missing weight_dic, TF_ids or query_targets.")

    rows: list[dict[str, str]] = []
    for raw_label, raw_matrix in sorted(weight_dic.items(), key=lambda item: int(item[0])):
        label = int(raw_label)
        if label < 0 or label >= len(phenotype_labels):
            raise ValueError(f"SimiC output contains unknown phenotype assignment: {label}")
        phenotype = phenotype_labels[label]
        matrix = np.asarray(raw_matrix, dtype=float)
        if matrix.ndim != 2:
            raise ValueError(f"SimiC weight matrix for {phenotype!r} is not 2-dimensional.")
        if matrix.shape[0] < len(tf_ids) or matrix.shape[1] < len(target_ids):
            raise ValueError(
                f"SimiC weight matrix for {phenotype!r} has shape {matrix.shape}, "
                f"expected at least ({len(tf_ids)}, {len(target_ids)})."
            )

        coefficient_matrix = matrix[: len(tf_ids), : len(target_ids)]
        for source_idx, source in enumerate(tf_ids):
            for target_idx, target in enumerate(target_ids):
                coefficient = float(coefficient_matrix[source_idx, target_idx])
                if not math.isfinite(coefficient):
                    raise ValueError(
                        f"SimiC produced non-finite coefficient for {source}->{target} in {phenotype}."
                    )
                if coefficient == 0.0:
                    continue
                if str(source) == str(target):
                    continue
                score = abs(coefficient)
                rows.append(
                    {
                        "source": str(source),
                        "target": str(target),
                        "score": repr(score),
                        "sign": "+" if coefficient > 0 else "-",
                        "evidence": "association",
                        "context": f"group:{phenotype}",
                    }
                )

    if not rows:
        raise ValueError("SimiC produced no non-zero network coefficients.")

    rows.sort(key=lambda row: float(row["score"]), reverse=True)

    with network_csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["source", "target", "score", "sign", "evidence", "context"],
        )
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--params", required=True, type=Path)
    parser.add_argument("--extra", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--threads", required=True, type=int)
    args = parser.parse_args()

    _configure_runtime_threads(args.threads)
    global np, pd
    import numpy as np_module  # noqa: PLC0415
    import pandas as pd_module  # noqa: PLC0415

    np = np_module
    pd = pd_module

    args.output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = args.output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir = args.output_dir / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    progress_path = args.output_dir / "progress.json"
    log_path = args.output_dir / "simic.log"
    log_path.write_text("", encoding="utf-8")

    try:
        tf_path = require_extra_file(args.extra, "tf_list.txt", "tf_list")
        phenotypes_path = require_extra_file(
            args.extra, "cell_phenotypes.tsv", "cell_phenotypes"
        )
        validate_runtime_inputs(
            input_path=args.input,
            params_path=args.params,
            extra_dir=args.extra,
            threads=args.threads,
            required_paths=[tf_path, phenotypes_path],
        )

        raw_params = load_params(args.params)
        params = _resolve_params(raw_params)
        execution_mode = _load_execution(args.params)
        _append_log(log_path, f"execution.mode={execution_mode}")
        _append_log(log_path, f"threads={args.threads}")
        _apply_random_seed(params.random_seed, log_path)

        write_progress(
            progress_path,
            status="running",
            percent=5,
            phase="load_input",
            message="Preparing expression, TF list and ordered cell phenotypes",
        )
        prepared = _prepare_upstream_inputs(
            input_path=args.input,
            tf_path=tf_path,
            phenotypes_path=phenotypes_path,
            runtime_dir=runtime_dir,
        )
        _append_log(
            log_path,
            (
                f"Prepared {len(prepared.cells)} cells, {len(prepared.genes)} genes, "
                f"{len(prepared.tf_names)} TFs and {len(prepared.phenotype_labels)} phenotypes"
            ),
        )

        weights_path, wauc_path = _run_simic(
            prepared=prepared,
            params=params,
            raw_dir=raw_dir,
            log_path=log_path,
            progress_path=progress_path,
        )
        _append_log(log_path, f"Wrote raw weights to {weights_path}")
        _append_log(log_path, f"Wrote weighted AUC matrices to {wauc_path}")

        write_progress(
            progress_path,
            status="running",
            percent=90,
            phase="write_output",
            message="Converting SimiC incidence matrices to network.csv",
        )
        row_count = _convert_weights_to_network(
            weights_path=weights_path,
            phenotype_labels=prepared.phenotype_labels,
            network_csv_path=args.output_dir / "network.csv",
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
