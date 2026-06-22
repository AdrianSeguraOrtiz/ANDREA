"""Dataset parsing and declarative input validation helpers."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Optional

from andrea.core.shared.input_validation import (
    read_tsv_column_values as _shared_read_tsv_column_values,
    validate_text_list_with_spec as _shared_validate_text_list_with_spec,
    validate_tsv_file_with_spec as _shared_validate_tsv_file_with_spec,
)
from andrea.core.shared.input_specs import load_input_specs

from .shared import (
    INPUT_SPECS_DIR,
    DatasetContext,
    SchemaConstraints,
    _load_json_object,
    _resolve_path,
)


def _inspect_expression_tsv(path: Path) -> tuple[int, int]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t")
        header = next(reader, None)
        if header is None or len(header) < 2:
            raise ValueError(
                f"Expression matrix must have header with at least 2 columns: {path}"
            )
        expected_cols = len(header)
        genes = 0
        for line_idx, row in enumerate(reader, start=2):
            if not row:
                continue
            if len(row) != expected_cols:
                raise ValueError(
                    f"Expression matrix has inconsistent number of columns at line {line_idx}: "
                    f"expected {expected_cols}, got {len(row)} ({path})"
                )
            genes += 1

    columns = expected_cols - 1
    if genes <= 0:
        raise ValueError(f"Expression matrix has no gene rows: {path}")
    return genes, columns


def _parse_dataset_context(
    *,
    dataset_manifest_path: Path,
    constraints: SchemaConstraints,
) -> DatasetContext:
    manifest = _load_json_object(dataset_manifest_path, "dataset-manifest")

    manifest_dataset = manifest.get("dataset")
    if not isinstance(manifest_dataset, dict):
        raise ValueError("dataset-manifest must include object field: dataset")

    spec = manifest_dataset.get("spec")
    expr_raw = manifest_dataset.get("expression_matrix")
    if not isinstance(spec, dict):
        raise ValueError("dataset-manifest.dataset.spec must be an embedded object")
    if not isinstance(expr_raw, str) or not expr_raw:
        raise ValueError(
            "dataset-manifest.dataset.expression_matrix must be a non-empty string path"
        )

    manifest_base = dataset_manifest_path.parent.resolve()
    expression_path = _resolve_path(manifest_base, expr_raw)

    if not expression_path.exists():
        raise ValueError(f"Expression matrix file not found: {expression_path}")

    spec_expression = spec.get("expression")
    if not isinstance(spec_expression, dict):
        raise ValueError(
            "dataset-manifest.dataset.spec must include object field: expression"
        )
    organism = spec.get("organism")
    if not isinstance(organism, dict):
        raise ValueError(
            "dataset-manifest.dataset.spec must include object field: organism"
        )
    organism_keys = set(organism)
    expected_organism_keys = {"taxonomic_group", "ncbi_taxon_id"}
    if organism_keys != expected_organism_keys:
        raise ValueError(
            "dataset-manifest.dataset.spec.organism must contain exactly "
            "taxonomic_group and ncbi_taxon_id"
        )

    dataset_id = str(spec.get("id", "")).strip()
    if not dataset_id:
        raise ValueError("dataset-manifest.dataset.spec.id is required")

    column_kind = str(spec_expression.get("column_kind", "")).strip()
    if column_kind not in constraints.column_kinds:
        raise ValueError(
            "dataset-manifest.dataset.spec.expression.column_kind must be one of "
            f"{sorted(constraints.column_kinds)}"
        )

    expression_profile = str(spec_expression.get("expression_profile", "")).strip()
    if expression_profile not in constraints.expression_profiles:
        raise ValueError(
            "dataset-manifest.dataset.spec.expression.expression_profile must be one of "
            f"{sorted(constraints.expression_profiles)}"
        )

    taxonomic_group = str(organism.get("taxonomic_group", "")).strip()
    if taxonomic_group not in constraints.taxonomic_groups:
        raise ValueError(
            "dataset-manifest.dataset.spec.organism.taxonomic_group must be one of "
            f"{sorted(constraints.taxonomic_groups)}"
        )
    ncbi_taxon_id_raw = organism.get("ncbi_taxon_id")
    if ncbi_taxon_id_raw is None:
        ncbi_taxon_id = None
    elif isinstance(ncbi_taxon_id_raw, int) and not isinstance(ncbi_taxon_id_raw, bool):
        ncbi_taxon_id = int(ncbi_taxon_id_raw)
    else:
        raise ValueError(
            "dataset-manifest.dataset.spec.organism.ncbi_taxon_id must be integer or null"
        )
    if taxonomic_group not in {"synthetic", "unknown"} and (
        ncbi_taxon_id is None or ncbi_taxon_id < 1
    ):
        raise ValueError(
            "dataset-manifest.dataset.spec.organism.ncbi_taxon_id must be integer >= 1 for biological taxonomic groups"
        )
    if ncbi_taxon_id is not None and ncbi_taxon_id < 1:
        raise ValueError(
            "dataset-manifest.dataset.spec.organism.ncbi_taxon_id must be integer >= 1 or null"
        )

    try:
        genes_expected = int(spec_expression.get("genes"))
        cols_expected = int(spec_expression.get("columns"))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(
            "dataset-manifest.dataset.spec.expression.genes and columns must be integers >= 1"
        ) from exc
    if genes_expected < 1 or cols_expected < 1:
        raise ValueError(
            "dataset-manifest.dataset.spec.expression.genes and columns must be >= 1"
        )

    genes_observed, cols_observed = _inspect_expression_tsv(expression_path)
    if genes_observed != genes_expected or cols_observed != cols_expected:
        raise ValueError(
            "dataset-manifest.dataset.spec expression dimensions do not match the expression matrix: "
            f"spec={genes_expected}x{cols_expected}, observed={genes_observed}x{cols_observed}"
        )

    raw_extras = manifest.get("extras", {})
    if raw_extras is None:
        raw_extras = {}
    if not isinstance(raw_extras, dict):
        raise ValueError("dataset-manifest.extras must be an object when provided")

    extras: dict[str, Optional[Path]] = {}
    for key in sorted(constraints.extra_input_keys):
        value = raw_extras.get(key)
        if value is None:
            extras[key] = None
            continue
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"dataset-manifest.extras.{key} must be string or null")
        extra_path = _resolve_path(manifest_base, value)
        if not extra_path.exists():
            raise ValueError(
                f"Extra input declared but file not found: extras.{key} -> {extra_path}"
            )
        extras[key] = extra_path

    return DatasetContext(
        dataset_id=dataset_id,
        column_kind=column_kind,
        expression_profile=expression_profile,
        taxonomic_group=taxonomic_group,
        ncbi_taxon_id=ncbi_taxon_id,
        genes=genes_observed,
        columns=cols_observed,
        expression_matrix_path=expression_path,
        extras=extras,
    )


def _load_input_specs() -> dict[str, dict[str, Any]]:
    return load_input_specs(INPUT_SPECS_DIR)


def _read_expression_axes(path: Path) -> tuple[list[str], list[str]]:
    genes: list[str] = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t")
        header = next(reader, None)
        if header is None or len(header) < 2:
            raise ValueError(
                f"Expression matrix must have header with at least 2 columns: {path}"
            )
        columns = [str(x).strip() for x in header[1:]]
        expected_cols = len(header)
        for line_idx, row in enumerate(reader, start=2):
            if not row:
                continue
            if len(row) != expected_cols:
                raise ValueError(
                    f"Expression matrix has inconsistent number of columns at line {line_idx}: "
                    f"expected {expected_cols}, got {len(row)} ({path})"
                )
            gene_id = str(row[0]).strip()
            if not gene_id:
                raise ValueError(
                    f"Expression matrix has empty gene identifier at line {line_idx}: {path}"
                )
            genes.append(gene_id)
    if not genes:
        raise ValueError(f"Expression matrix has no gene rows: {path}")
    return genes, columns


def _load_groups_by_column(
    *,
    groups_path: Path,
    expression_columns: list[str],
) -> tuple[list[str], dict[str, list[str]]]:
    sample_to_group: dict[str, str] = {}
    header_markers = {"sample", "cell", "column", "observation", "id"}
    group_markers = {"group", "cluster", "cell_type", "label"}

    with groups_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t")
        for line_idx, row in enumerate(reader, start=1):
            if not row or not any(str(cell).strip() for cell in row):
                continue
            if len(row) < 2:
                raise ValueError(
                    f"{groups_path}: line {line_idx} must have at least 2 columns: expression column and group"
                )

            sample = str(row[0]).strip()
            group = str(row[1]).strip()
            if (
                line_idx == 1
                and sample.lower() in header_markers
                and group.lower() in group_markers
            ):
                continue
            if not sample or not group:
                raise ValueError(
                    f"{groups_path}: line {line_idx} must have non-empty expression-column and group values"
                )
            if sample in sample_to_group:
                raise ValueError(
                    f"{groups_path}: duplicate expression-column assignment found: {sample}"
                )
            sample_to_group[sample] = group

    missing = [column for column in expression_columns if column not in sample_to_group]
    if missing:
        preview = ", ".join(missing[:8])
        raise ValueError(
            f"{groups_path}: missing group assignments for expression columns: {preview}"
        )

    extra = [
        sample for sample in sample_to_group if sample not in set(expression_columns)
    ]
    if extra:
        preview = ", ".join(extra[:8])
        raise ValueError(
            f"{groups_path}: contains samples not present in expression matrix: {preview}"
        )

    group_order: list[str] = []
    group_to_columns: dict[str, list[str]] = {}
    for column in expression_columns:
        group = sample_to_group[column]
        if group not in group_to_columns:
            group_to_columns[group] = []
            group_order.append(group)
        group_to_columns[group].append(column)

    return group_order, group_to_columns


def _read_tsv_column_values(path: Path, spec: dict[str, Any], column: str) -> set[str]:
    return _shared_read_tsv_column_values(path, spec, column)


def _validate_tsv_file_with_spec(
    *,
    key: str,
    path: Path,
    spec: dict[str, Any],
    expression_genes: set[str],
    expression_columns: set[str],
    expression_columns_count: int,
    extra_column_lookup: Any = None,
) -> dict[str, Any]:
    return _shared_validate_tsv_file_with_spec(
        key=key,
        path=path,
        spec=spec,
        expression_genes=expression_genes,
        expression_columns=expression_columns,
        expression_columns_count=expression_columns_count,
        extra_column_lookup=extra_column_lookup,
    ).to_public_dict()


def _validate_text_list_with_spec(
    *,
    key: str,
    path: Path,
    spec: dict[str, Any],
    expression_genes: set[str],
) -> dict[str, Any]:
    return _shared_validate_text_list_with_spec(
        key=key,
        path=path,
        spec=spec,
        expression_genes=expression_genes,
    ).to_public_dict()


def _validate_dataset_inputs_by_specs(
    *,
    dataset: DatasetContext,
    input_specs: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[str], list[str]]:
    expr_genes, expr_columns = _read_expression_axes(dataset.expression_matrix_path)
    expression_genes = set(expr_genes)
    expression_columns = set(expr_columns)
    results: dict[str, Any] = {}
    errors: list[str] = []
    warnings: list[str] = []
    extra_column_cache: dict[
        tuple[str, str], tuple[Optional[set[str]], Optional[str]]
    ] = {}

    def lookup_extra_column_values(
        other_input: str,
        other_column: str,
    ) -> tuple[Optional[set[str]], Optional[str]]:
        cache_key = (other_input, other_column)
        if cache_key in extra_column_cache:
            return extra_column_cache[cache_key]

        if not other_input or not other_column:
            result = (
                None,
                "invalid cross-check reference (other_input/other_column required)",
            )
            extra_column_cache[cache_key] = result
            return result

        other_path = dataset.extras.get(other_input)
        if other_path is None:
            result = (None, f"extra '{other_input}' was not provided")
            extra_column_cache[cache_key] = result
            return result

        other_spec = input_specs.get(other_input, {})
        file_kind = (
            str(other_spec.get("file_kind", "tsv"))
            if isinstance(other_spec, dict)
            else "tsv"
        )
        if file_kind != "tsv":
            result = (
                None,
                f"extra '{other_input}' is not a TSV input and cannot be referenced by column",
            )
            extra_column_cache[cache_key] = result
            return result

        try:
            values = _read_tsv_column_values(
                other_path,
                other_spec if isinstance(other_spec, dict) else {},
                other_column,
            )
        except ValueError as exc:
            result = (None, str(exc))
        else:
            result = (values, None)
        extra_column_cache[cache_key] = result
        return result

    expr_spec = input_specs.get("expression_matrix", {})
    expr_result = _validate_tsv_file_with_spec(
        key="expression_matrix",
        path=dataset.expression_matrix_path,
        spec=expr_spec if isinstance(expr_spec, dict) else {},
        expression_genes=expression_genes,
        expression_columns=expression_columns,
        expression_columns_count=len(expr_columns),
        extra_column_lookup=lookup_extra_column_values,
    )
    results["expression_matrix"] = expr_result
    errors.extend(expr_result["errors"])
    warnings.extend(expr_result["warnings"])

    extras_results: dict[str, Any] = {}
    for key, extra_path in sorted(dataset.extras.items()):
        spec = input_specs.get(key, {})
        if extra_path is None:
            extras_results[key] = {
                "status": "missing",
                "errors": [],
                "warnings": [],
                "summary": None,
            }
            continue

        file_kind = (
            str(spec.get("file_kind", "tsv")) if isinstance(spec, dict) else "tsv"
        )
        if file_kind == "txt_list":
            res = _validate_text_list_with_spec(
                key=key,
                path=extra_path,
                spec=spec if isinstance(spec, dict) else {},
                expression_genes=expression_genes,
            )
        else:
            res = _validate_tsv_file_with_spec(
                key=key,
                path=extra_path,
                spec=spec if isinstance(spec, dict) else {},
                expression_genes=expression_genes,
                expression_columns=expression_columns,
                expression_columns_count=len(expr_columns),
                extra_column_lookup=lookup_extra_column_values,
            )
        extras_results[key] = res
        errors.extend(res["errors"])
        warnings.extend(res["warnings"])

    results["extras"] = extras_results
    return results, errors, warnings
