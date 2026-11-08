"""Shared validation for normalized simulator output packages."""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any

from andrea.core.shared.input_validation import (
    read_tsv_column_values,
    validate_text_list_with_spec,
    validate_tsv_file_with_spec,
)
from andrea.core.commands.generate_data.semantic import (
    parse_truth_requirements,
    required_truth_context_prefixes,
)
from andrea.core.shared.input_specs import load_input_specs
from andrea.core.shared.network_context import (
    COLUMN_CONTEXT_PREFIX,
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
    data_axes: dict[str, Any],
    truth_requirements: dict[str, Any],
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
    if simulator_manifest.get("data_axes") != data_axes:
        raise ValueError(
            f"simulator-output-manifest[{dataset_id}].data_axes must match the resolved simulation request"
        )
    if simulator_manifest.get("truth_requirements") != truth_requirements:
        raise ValueError(
            f"simulator-output-manifest[{dataset_id}].truth_requirements must match the resolved simulation request"
        )

    expression_path, expression_genes, expression_columns = _validate_expression(
        stage_dir=stage_dir,
        dataset_id=dataset_id,
        simulator_manifest=simulator_manifest,
    )
    truth_paths, contexts = _validate_truth_outputs(
        stage_dir=stage_dir,
        dataset_id=dataset_id,
        truth_requirements=truth_requirements,
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
    truth_requirements: dict[str, Any],
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
    column_contexts: set[str] = set()
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
            evidence = str(row.get("evidence", "")).strip()
            if evidence != "simulated_truth":
                raise ValueError(
                    f"truth networks for dataset {dataset_id} must use evidence='simulated_truth' at line {line_no}"
                )
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
            elif context.startswith(COLUMN_CONTEXT_PREFIX):
                column_id = context.removeprefix(COLUMN_CONTEXT_PREFIX)
                if not column_id:
                    raise ValueError(f"truth networks for dataset {dataset_id} contain empty column context at line {line_no}")
                column_contexts.add(column_id)
    if row_count == 0:
        raise ValueError(f"truth networks for dataset {dataset_id} contain no edges")

    required_exact, required_prefixes = _required_truth_contexts(truth_requirements)
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
    unknown_columns = sorted(column_contexts.difference(expression_columns))
    if unknown_columns:
        raise ValueError(
            f"truth networks for dataset {dataset_id} contain column contexts not present in expression columns: "
            + ", ".join(unknown_columns[:8])
        )
    requested_truth = parse_truth_requirements(truth_requirements)
    if "column" in requested_truth.contexts and column_contexts != expression_columns:
        missing = sorted(expression_columns.difference(column_contexts))[:8]
        extra = sorted(column_contexts.difference(expression_columns))[:8]
        raise ValueError(
            f"truth networks for dataset {dataset_id} must include exactly one column context family covering expression columns; "
            f"missing={missing}, extra={extra}"
        )
    return {"gene_universe": str(gene_universe_rel), "networks": str(networks_rel)}, contexts


def _required_truth_contexts(
    truth_requirements: dict[str, Any],
) -> tuple[set[str], list[str]]:
    exact_contexts: set[str] = set()
    context_prefixes: list[str] = []
    requirements = parse_truth_requirements(truth_requirements)
    for context in required_truth_context_prefixes(requirements):
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
    if raw_extras.get("tf_list") != "extras/tf_list.txt":
        raise ValueError(
            f"simulator-output-manifest[{dataset_id}].extras.tf_list must be "
            "'extras/tf_list.txt'"
        )

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
        values = read_tsv_column_values(
            other_path,
            input_specs.get(other_input, {}),
            other_column,
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
            result = validate_text_list_with_spec(
                key=key,
                path=path,
                spec=spec,
                expression_genes=expression_genes,
                unknown_cross_check="error",
            )
            _raise_input_validation_errors(
                result.errors,
                dataset_id=dataset_id,
                input_id=key,
            )
            summary[key] = result.summary
            if key == "tf_list":
                seen: set[str] = set()
                duplicates: set[str] = set()
                for value in result.line_values:
                    if value in seen:
                        duplicates.add(value)
                    seen.add(value)
                if duplicates:
                    raise ValueError(
                        f"extra tf_list for dataset {dataset_id} contains duplicate "
                        "candidate regulator identifiers: "
                        + ", ".join(sorted(duplicates)[:8])
                    )
            if key == "enrichment_background" and not result.line_values:
                raise ValueError(f"extra enrichment_background is empty for dataset {dataset_id}")
        else:
            result = validate_tsv_file_with_spec(
                key=key,
                path=path,
                spec=spec,
                expression_genes=expression_genes,
                expression_columns=expression_columns,
                expression_columns_count=len(expression_columns),
                extra_column_lookup=lookup_extra_column,
                unknown_cross_check="error",
            )
            _raise_input_validation_errors(
                result.errors,
                dataset_id=dataset_id,
                input_id=key,
            )
            summary[key] = result.summary
            if key == "groups":
                if result.first_column_values != expression_columns:
                    missing = sorted(expression_columns.difference(result.first_column_values))[:8]
                    extra = sorted(result.first_column_values.difference(expression_columns))[:8]
                    raise ValueError(
                        f"extra groups must cover expression columns exactly for dataset {dataset_id}; "
                        f"missing={missing}, extra={extra}"
                    )
                _validate_group_contexts(
                    dataset_id=dataset_id,
                    group_values=result.values_by_column.get("cluster", set()),
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


def _raise_input_validation_errors(
    errors: list[str],
    *,
    dataset_id: str,
    input_id: str,
) -> None:
    if not errors:
        return
    raise ValueError(
        f"extra {input_id} for dataset {dataset_id} failed input-spec validation: "
        + "; ".join(errors[:5])
    )


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


__all__ = ["ROOT_PARENT", "validate_simulator_output_package"]
