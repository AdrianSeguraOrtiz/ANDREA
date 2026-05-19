"""Shared validation for normalized simulator output packages."""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any

from andrea.core.commands.generate_data.shared import (
    required_truth_context_prefixes_for_profile,
)
from andrea.core.shared.input_specs import load_input_specs
from andrea.core.shared.network_context import (
    CELL_CONTEXT_PREFIX,
    GROUP_CONTEXT_PREFIX,
    GLOBAL_CONTEXT,
    normalize_network_context,
    normalize_network_sign,
)

ROOT_PARENT = "__root__"


def validate_simulator_output_package(
    *,
    stage_dir: Path,
    dataset_id: str,
    profile: str,
    simulator_manifest: dict[str, Any],
    input_specs: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate one normalized simulator output tree.

    The validator is intentionally simulator-agnostic. It checks the public
    package contract produced by wrappers and consumed by generate-data,
    infer-network and evaluate-inference.
    """

    if input_specs is None:
        input_specs = load_input_specs()

    expression_path, expression_genes, expression_columns = _validate_expression(
        stage_dir=stage_dir,
        dataset_id=dataset_id,
        simulator_manifest=simulator_manifest,
    )
    truth_paths, contexts = _validate_truth_outputs(
        stage_dir=stage_dir,
        dataset_id=dataset_id,
        profile=profile,
        simulator_manifest=simulator_manifest,
        expression_genes=expression_genes,
        expression_columns=expression_columns,
    )
    extras_summary = _validate_extras(
        stage_dir=stage_dir,
        dataset_id=dataset_id,
        simulator_manifest=simulator_manifest,
        input_specs=input_specs,
        expression_genes=expression_genes,
        expression_columns=expression_columns,
        contexts=contexts,
    )
    return {
        "expression": {
            "path": expression_path.as_posix(),
            "genes": len(expression_genes),
            "columns": len(expression_columns),
        },
        "truth": truth_paths,
        "extras": extras_summary,
        "contexts": sorted(contexts),
    }


def _validate_expression(
    *,
    stage_dir: Path,
    dataset_id: str,
    simulator_manifest: dict[str, Any],
) -> tuple[Path, set[str], set[str]]:
    expression = simulator_manifest.get("expression")
    if not isinstance(expression, dict):
        raise ValueError(f"simulator-output-manifest[{dataset_id}].expression must be an object")
    expression_rel = expression.get("path")
    if not isinstance(expression_rel, str) or not expression_rel.strip():
        raise ValueError(f"simulator-output-manifest[{dataset_id}] is missing expression.path")
    expression_path = stage_dir / expression_rel
    if not expression_path.exists() or not expression_path.is_file():
        raise ValueError(
            f"simulator-output-manifest[{dataset_id}] references missing expression matrix: {expression_rel}"
        )

    genes: list[str] = []
    columns: list[str] = []
    with expression_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        header = next(reader, None)
        if header is None or len(header) < 2:
            raise ValueError(
                f"expression matrix for dataset {dataset_id} must have a header with gene plus at least one column: {expression_rel}"
            )
        columns = [str(value).strip() for value in header[1:]]
        if any(not value for value in columns):
            raise ValueError(
                f"expression matrix for dataset {dataset_id} has empty column identifiers: {expression_rel}"
            )
        if len(set(columns)) != len(columns):
            raise ValueError(
                f"expression matrix for dataset {dataset_id} has duplicated column identifiers: {expression_rel}"
            )
        expected_width = len(header)
        for line_no, row in enumerate(reader, start=2):
            if not row:
                continue
            if len(row) != expected_width:
                raise ValueError(
                    f"expression matrix for dataset {dataset_id} has inconsistent width at line {line_no}: "
                    f"expected {expected_width}, got {len(row)}"
                )
            gene = str(row[0]).strip()
            if not gene:
                raise ValueError(
                    f"expression matrix for dataset {dataset_id} has empty gene identifier at line {line_no}"
                )
            genes.append(gene)
    if not genes:
        raise ValueError(f"expression matrix for dataset {dataset_id} has no gene rows")
    if len(set(genes)) != len(genes):
        raise ValueError(
            f"expression matrix for dataset {dataset_id} has duplicated gene identifiers: {expression_rel}"
        )

    expected_genes = expression.get("genes")
    expected_columns = expression.get("columns")
    if expected_genes != len(genes) or expected_columns != len(columns):
        raise ValueError(
            f"simulator-output-manifest[{dataset_id}].expression dimensions do not match expression.tsv: "
            f"manifest={expected_genes}x{expected_columns}, observed={len(genes)}x{len(columns)}"
        )
    return expression_path, set(genes), set(columns)


def _validate_truth_outputs(
    *,
    stage_dir: Path,
    dataset_id: str,
    profile: str,
    simulator_manifest: dict[str, Any],
    expression_genes: set[str],
    expression_columns: set[str],
) -> tuple[dict[str, str], set[str]]:
    truth = simulator_manifest.get("truth", {})
    if not isinstance(truth, dict):
        raise ValueError(f"simulator-output-manifest[{dataset_id}].truth must be an object")
    gene_universe_rel = truth.get("gene_universe")
    networks_rel = truth.get("networks")
    if not isinstance(gene_universe_rel, str) or not gene_universe_rel.strip():
        raise ValueError(f"simulator-output-manifest[{dataset_id}] is missing truth.gene_universe")
    if not isinstance(networks_rel, str) or not networks_rel.strip():
        raise ValueError(f"simulator-output-manifest[{dataset_id}] is missing truth.networks")

    gene_universe_path = stage_dir / gene_universe_rel
    if not gene_universe_path.exists() or not gene_universe_path.is_file():
        raise ValueError(
            f"simulator-output-manifest[{dataset_id}] references missing truth gene_universe: {gene_universe_rel}"
        )
    gene_universe_values = [
        line.strip()
        for line in gene_universe_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not gene_universe_values:
        raise ValueError(f"truth gene_universe is empty for dataset {dataset_id}: {gene_universe_rel}")
    if len(set(gene_universe_values)) != len(gene_universe_values):
        raise ValueError(f"truth gene_universe contains duplicate gene IDs for dataset {dataset_id}: {gene_universe_rel}")
    gene_universe = set(gene_universe_values)
    if gene_universe != expression_genes:
        missing = sorted(expression_genes.difference(gene_universe))[:8]
        extra = sorted(gene_universe.difference(expression_genes))[:8]
        raise ValueError(
            f"truth gene_universe for dataset {dataset_id} must match expression genes; "
            f"missing_from_universe={missing}, extra_in_universe={extra}"
        )

    networks_path = stage_dir / networks_rel
    if not networks_path.exists() or not networks_path.is_file():
        raise ValueError(
            f"simulator-output-manifest[{dataset_id}] references missing truth networks: {networks_rel}"
        )
    contexts: set[str] = set()
    group_contexts: set[str] = set()
    cell_contexts: set[str] = set()
    with networks_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required_columns = {"source", "target", "score", "sign", "evidence", "context"}
        missing_columns = sorted(required_columns.difference(reader.fieldnames or []))
        if missing_columns:
            raise ValueError(
                f"truth networks for dataset {dataset_id} are missing columns: "
                + ", ".join(missing_columns)
            )
        row_count = 0
        for line_no, row in enumerate(reader, start=2):
            row_count += 1
            source = str(row.get("source", "")).strip()
            target = str(row.get("target", "")).strip()
            if not source or not target:
                raise ValueError(f"truth networks for dataset {dataset_id} contain empty source/target at line {line_no}")
            if source == target:
                raise ValueError(f"truth networks for dataset {dataset_id} contain a self-loop at line {line_no}: {source}")
            unknown_genes = sorted({source, target}.difference(gene_universe))
            if unknown_genes:
                raise ValueError(
                    f"truth networks for dataset {dataset_id} reference genes outside truth/gene_universe.txt at line {line_no}: "
                    + ", ".join(unknown_genes)
                )
            try:
                score = float(str(row.get("score", "")).strip())
            except ValueError as exc:
                raise ValueError(
                    f"truth networks for dataset {dataset_id} contain a non-numeric score at line {line_no}"
                ) from exc
            if not math.isfinite(score) or score <= 0:
                raise ValueError(
                    f"truth networks for dataset {dataset_id} contain non-positive score at line {line_no}"
                )
            normalize_network_sign(row.get("sign", ""), source=f"truth networks for dataset {dataset_id} line {line_no}")
            context = normalize_network_context(
                row.get("context", ""),
                source=f"truth networks for dataset {dataset_id} line {line_no}",
            )
            contexts.add(context)
            if context.startswith(GROUP_CONTEXT_PREFIX):
                group_id = context.removeprefix(GROUP_CONTEXT_PREFIX)
                if not group_id:
                    raise ValueError(f"truth networks for dataset {dataset_id} contain empty group context at line {line_no}")
                group_contexts.add(group_id)
            elif context.startswith(CELL_CONTEXT_PREFIX):
                cell_id = context.removeprefix(CELL_CONTEXT_PREFIX)
                if not cell_id:
                    raise ValueError(f"truth networks for dataset {dataset_id} contain empty cell context at line {line_no}")
                cell_contexts.add(cell_id)
    if row_count == 0:
        raise ValueError(f"truth networks for dataset {dataset_id} contain no edges")

    required_exact, required_prefixes = _required_truth_contexts(profile)
    missing_exact = sorted(required_exact.difference(contexts))
    if missing_exact:
        raise ValueError(
            f"truth networks for dataset {dataset_id} are missing required context(s): "
            + ", ".join(missing_exact)
        )
    for prefix in required_prefixes:
        if not any(context.startswith(prefix) for context in contexts):
            raise ValueError(
                f"truth networks for dataset {dataset_id} are missing required context prefix: {prefix}"
            )
    unknown_cells = sorted(cell_contexts.difference(expression_columns))
    if unknown_cells:
        raise ValueError(
            f"truth networks for dataset {dataset_id} contain cell contexts not present in expression columns: "
            + ", ".join(unknown_cells[:8])
        )
    return {"gene_universe": str(gene_universe_rel), "networks": str(networks_rel)}, contexts


def _required_truth_contexts(profile: str) -> tuple[set[str], list[str]]:
    exact_contexts: set[str] = set()
    context_prefixes: list[str] = []
    for context in required_truth_context_prefixes_for_profile(profile):
        if context.endswith(":"):
            context_prefixes.append(context)
        else:
            exact_contexts.add(context)
    return exact_contexts, context_prefixes


def _validate_extras(
    *,
    stage_dir: Path,
    dataset_id: str,
    simulator_manifest: dict[str, Any],
    input_specs: dict[str, dict[str, Any]],
    expression_genes: set[str],
    expression_columns: set[str],
    contexts: set[str],
) -> dict[str, Any]:
    raw_extras = simulator_manifest.get("extras", {})
    if raw_extras is None:
        raw_extras = {}
    if not isinstance(raw_extras, dict):
        raise ValueError(f"simulator-output-manifest[{dataset_id}].extras must be an object")

    extras: dict[str, Path] = {}
    for key, rel_path in sorted(raw_extras.items()):
        if rel_path is None:
            continue
        if not isinstance(rel_path, str) or not rel_path.strip():
            raise ValueError(
                f"simulator-output-manifest[{dataset_id}].extras.{key} must be a non-empty string or null"
            )
        path = stage_dir / rel_path
        if not path.exists() or not path.is_file():
            raise ValueError(
                f"simulator-output-manifest[{dataset_id}] references missing extra '{key}': {rel_path}"
            )
        extras[key] = path

    extra_columns_cache: dict[tuple[str, str], set[str]] = {}

    def lookup_extra_column(other_input: str, other_column: str) -> set[str]:
        cache_key = (other_input, other_column)
        if cache_key in extra_columns_cache:
            return extra_columns_cache[cache_key]
        other_path = extras.get(other_input)
        if other_path is None:
            raise ValueError(
                f"extra '{other_input}' required for cross-check column '{other_column}' was not generated"
            )
        values = _read_tsv_column_values(
            path=other_path,
            spec=input_specs.get(other_input, {}),
            column=other_column,
            dataset_id=dataset_id,
            input_id=other_input,
        )
        extra_columns_cache[cache_key] = values
        return values

    summary: dict[str, Any] = {}
    for key, path in sorted(extras.items()):
        spec = input_specs.get(key)
        if not isinstance(spec, dict):
            raise ValueError(f"simulator-output-manifest[{dataset_id}] references unknown standardized extra '{key}'")
        file_kind = str(spec.get("file_kind", "tsv"))
        if file_kind == "txt_list":
            row_count, values = _validate_text_list(
                key=key,
                path=path,
                spec=spec,
                dataset_id=dataset_id,
                expression_genes=expression_genes,
            )
            summary[key] = {"rows": row_count, "columns": 1}
            if key == "enrichment_background" and not values:
                raise ValueError(f"extra enrichment_background is empty for dataset {dataset_id}")
        else:
            rows, columns, values_by_column, first_column_values = _validate_tsv_extra(
                key=key,
                path=path,
                spec=spec,
                dataset_id=dataset_id,
                expression_genes=expression_genes,
                expression_columns=expression_columns,
                lookup_extra_column=lookup_extra_column,
            )
            summary[key] = {"rows": rows, "columns": columns}
            if key == "groups":
                if first_column_values != expression_columns:
                    missing = sorted(expression_columns.difference(first_column_values))[:8]
                    extra = sorted(first_column_values.difference(expression_columns))[:8]
                    raise ValueError(
                        f"extra groups must cover expression columns exactly for dataset {dataset_id}; "
                        f"missing={missing}, extra={extra}"
                    )
                _validate_group_contexts(
                    dataset_id=dataset_id,
                    group_values=values_by_column.get("cluster", set()),
                    contexts=contexts,
                )
            if key == "lineage_tree":
                groups_path = extras.get("groups")
                if groups_path is None:
                    raise ValueError(
                        f"extra lineage_tree requires groups.tsv for dataset {dataset_id}"
                    )
                group_values = lookup_extra_column("groups", "cluster")
                _validate_lineage_tree_coverage(
                    dataset_id=dataset_id,
                    path=path,
                    group_values=group_values,
                )
    has_group_contexts = any(
        context.startswith(GROUP_CONTEXT_PREFIX) for context in contexts
    )
    if has_group_contexts and "groups" not in extras:
        raise ValueError(
            f"truth networks for dataset {dataset_id} contain group contexts but simulator-output-manifest is missing extras.groups"
        )
    return summary


def _validate_group_contexts(
    *,
    dataset_id: str,
    group_values: set[str],
    contexts: set[str],
) -> None:
    group_contexts = {
        context.removeprefix(GROUP_CONTEXT_PREFIX)
        for context in contexts
        if context.startswith(GROUP_CONTEXT_PREFIX)
    }
    if not group_contexts:
        return
    missing_contexts = sorted(group_values.difference(group_contexts))
    unknown_contexts = sorted(group_contexts.difference(group_values))
    if missing_contexts:
        raise ValueError(
            f"truth networks for dataset {dataset_id} are missing group context(s) for groups.tsv labels: "
            + ", ".join(missing_contexts[:8])
        )
    if unknown_contexts:
        raise ValueError(
            f"truth networks for dataset {dataset_id} contain group contexts not present in groups.tsv: "
            + ", ".join(unknown_contexts[:8])
        )


def _validate_lineage_tree_coverage(
    *,
    dataset_id: str,
    path: Path,
    group_values: set[str],
) -> None:
    children: set[str] = set()
    parents: set[str] = set()
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for line_no, row in enumerate(reader, start=2):
            if None in row:
                raise ValueError(
                    f"lineage_tree for dataset {dataset_id} has inconsistent width at line {line_no}"
                )
            child = str(row.get("child", "")).strip()
            parent = str(row.get("parent", "")).strip()
            if child:
                children.add(child)
            if parent and parent != ROOT_PARENT:
                parents.add(parent)
            if parent == ROOT_PARENT:
                try:
                    gain = float(str(row.get("gain_rate", "")).strip())
                    loss = float(str(row.get("loss_rate", "")).strip())
                except ValueError as exc:
                    raise ValueError(
                        f"lineage_tree for dataset {dataset_id} root row at line {line_no} must have numeric gain_rate and loss_rate"
                    ) from exc
                if gain != 0.0 or loss != 0.0:
                    raise ValueError(
                        f"lineage_tree for dataset {dataset_id} root row at line {line_no} must have gain_rate=0 and loss_rate=0"
                    )
    missing_children = sorted(group_values.difference(children))
    unknown = sorted(children.union(parents).difference(group_values))
    if missing_children:
        raise ValueError(
            f"lineage_tree for dataset {dataset_id} must include every groups.tsv label as child; missing: "
            + ", ".join(missing_children[:8])
        )
    if unknown:
        raise ValueError(
            f"lineage_tree for dataset {dataset_id} references groups not present in groups.tsv: "
            + ", ".join(unknown[:8])
        )


def _validate_text_list(
    *,
    key: str,
    path: Path,
    spec: dict[str, Any],
    dataset_id: str,
    expression_genes: set[str],
) -> tuple[int, list[str]]:
    values = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    min_rows = int(spec.get("min_rows", 0) or 0)
    if len(values) < min_rows:
        raise ValueError(
            f"extra {key} for dataset {dataset_id} expected at least {min_rows} row(s), got {len(values)}"
        )
    for rule in spec.get("cross_checks", []):
        if not isinstance(rule, dict):
            continue
        kind = str(rule.get("kind", "")).strip()
        if kind == "line_subset_expression_genes":
            unknown = sorted(set(values).difference(expression_genes))
            if unknown:
                raise ValueError(
                    f"extra {key} for dataset {dataset_id} contains identifiers not present in expression genes: "
                    + ", ".join(unknown[:8])
                )
        elif kind:
            raise ValueError(f"extra {key} for dataset {dataset_id} uses unsupported cross-check: {kind}")
    return len(values), values


def _validate_tsv_extra(
    *,
    key: str,
    path: Path,
    spec: dict[str, Any],
    dataset_id: str,
    expression_genes: set[str],
    expression_columns: set[str],
    lookup_extra_column: Any,
) -> tuple[int, int, dict[str, set[str]], set[str]]:
    delimiter = str(spec.get("delimiter", "\t"))
    required_columns = [str(x) for x in spec.get("required_columns", [])]
    column_types = spec.get("column_types", {})
    if not isinstance(column_types, dict):
        column_types = {}
    first_column_role = str(spec.get("first_column_role", "none") or "none")
    unique_first_column = bool(spec.get("unique_first_column", False))
    min_rows = int(spec.get("min_rows", 0) or 0)
    min_columns = int(spec.get("min_columns", 1) or 1)
    values_by_column: dict[str, set[str]] = {}
    first_column_values: set[str] = set()
    seen_first_column: set[str] = set()

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        fieldnames = [str(value).strip() for value in (reader.fieldnames or [])]
        if not fieldnames:
            raise ValueError(f"extra {key} for dataset {dataset_id} is missing a header row")
        if len(fieldnames) < min_columns:
            raise ValueError(
                f"extra {key} for dataset {dataset_id} expected at least {min_columns} column(s), got {len(fieldnames)}"
            )
        missing_required = sorted(set(required_columns).difference(fieldnames))
        if missing_required:
            raise ValueError(
                f"extra {key} for dataset {dataset_id} is missing required column(s): "
                + ", ".join(missing_required)
            )
        row_count = 0
        for line_no, row in enumerate(reader, start=2):
            if None in row:
                raise ValueError(
                    f"extra {key} for dataset {dataset_id} has inconsistent width at line {line_no}"
                )
            row_count += 1
            first_value = str(row.get(fieldnames[0], "")).strip()
            if first_column_role == "expression_column_id":
                if not first_value:
                    raise ValueError(
                        f"extra {key} for dataset {dataset_id} has empty expression-column identifier at line {line_no}"
                    )
                first_column_values.add(first_value)
                if unique_first_column and first_value in seen_first_column:
                    raise ValueError(
                        f"extra {key} for dataset {dataset_id} duplicates expression-column identifier {first_value!r}"
                    )
                seen_first_column.add(first_value)
            for column, raw_type in column_types.items():
                if column not in fieldnames:
                    continue
                raw_value = str(row.get(str(column), "")).strip()
                if not raw_value:
                    continue
                _validate_scalar_type(
                    value=raw_value,
                    type_name=str(raw_type),
                    key=key,
                    column=str(column),
                    dataset_id=dataset_id,
                    line_no=line_no,
                )
                values_by_column.setdefault(str(column), set()).add(raw_value)
    if row_count < min_rows:
        raise ValueError(
            f"extra {key} for dataset {dataset_id} expected at least {min_rows} row(s), got {row_count}"
        )

    for rule in spec.get("cross_checks", []):
        if not isinstance(rule, dict):
            continue
        kind = str(rule.get("kind", "")).strip()
        column = str(rule.get("column", "")).strip()
        if kind == "first_column_subset_expression_columns":
            unknown = sorted(first_column_values.difference(expression_columns))
            if unknown:
                raise ValueError(
                    f"extra {key} for dataset {dataset_id} first column contains identifiers not present in expression columns: "
                    + ", ".join(unknown[:8])
                )
        elif kind == "row_count_matches_expression_columns":
            if row_count != len(expression_columns):
                raise ValueError(
                    f"extra {key} for dataset {dataset_id} row count ({row_count}) must match expression columns ({len(expression_columns)})"
                )
        elif kind == "column_subset_expression_genes":
            present = values_by_column.get(column, set())
            unknown = sorted(present.difference(expression_genes))
            if unknown:
                raise ValueError(
                    f"extra {key} for dataset {dataset_id} column {column!r} contains identifiers not present in expression genes: "
                    + ", ".join(unknown[:8])
                )
        elif kind == "column_subset_extra_column":
            present = values_by_column.get(column, set())
            other_values = lookup_extra_column(
                str(rule.get("other_input", "")).strip(),
                str(rule.get("other_column", "")).strip(),
            )
            unknown = sorted(present.difference(other_values))
            if unknown:
                raise ValueError(
                    f"extra {key} for dataset {dataset_id} column {column!r} contains identifiers not present in referenced extra: "
                    + ", ".join(unknown[:8])
                )
        elif kind:
            raise ValueError(f"extra {key} for dataset {dataset_id} uses unsupported cross-check: {kind}")
    return row_count, len(fieldnames), values_by_column, first_column_values


def _read_tsv_column_values(
    *,
    path: Path,
    spec: dict[str, Any],
    column: str,
    dataset_id: str,
    input_id: str,
) -> set[str]:
    delimiter = str(spec.get("delimiter", "\t"))
    values: set[str] = set()
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        if column not in (reader.fieldnames or []):
            raise ValueError(
                f"extra {input_id} for dataset {dataset_id} is missing required cross-check column {column!r}"
            )
        for row in reader:
            value = str(row.get(column, "")).strip()
            if value:
                values.add(value)
    return values


def _validate_scalar_type(
    *,
    value: str,
    type_name: str,
    key: str,
    column: str,
    dataset_id: str,
    line_no: int,
) -> None:
    if type_name == "string":
        return
    if type_name == "int":
        try:
            int(value)
        except ValueError as exc:
            raise ValueError(
                f"extra {key} for dataset {dataset_id} column {column!r} must be int at line {line_no}"
            ) from exc
        return
    if type_name == "float":
        try:
            number = float(value)
        except ValueError as exc:
            raise ValueError(
                f"extra {key} for dataset {dataset_id} column {column!r} must be float at line {line_no}"
            ) from exc
        if not math.isfinite(number):
            raise ValueError(
                f"extra {key} for dataset {dataset_id} column {column!r} must be finite at line {line_no}"
            )
        return
    raise ValueError(
        f"extra {key} for dataset {dataset_id} uses unsupported column type {type_name!r}"
    )


__all__ = ["ROOT_PARENT", "validate_simulator_output_package"]
