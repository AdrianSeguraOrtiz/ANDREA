"""Declarative validation helpers for ANDREA input specs."""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class InputValidationResult:
    status: str
    errors: list[str]
    warnings: list[str]
    rows: int
    columns: int
    values_by_column: dict[str, set[str]] = field(default_factory=dict)
    first_column_values: set[str] = field(default_factory=set)
    line_values: list[str] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, int]:
        return {"rows": self.rows, "columns": self.columns}

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "summary": self.summary,
        }


def read_tsv_column_values(path: Path, spec: dict[str, Any], column: str) -> set[str]:
    delimiter = str(spec.get("delimiter", "\t"))
    has_header = bool(spec.get("header", True))
    if not has_header:
        raise ValueError("referenced input does not define a header row")

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter=delimiter)
        header_row = next(reader, None)
        fieldnames = [str(value).strip() for value in header_row] if header_row else []
        if not fieldnames:
            raise ValueError("referenced input is missing a header row")
        try:
            column_index = fieldnames.index(column)
        except ValueError as exc:
            raise ValueError(
                f"referenced input is missing required column '{column}'"
            ) from exc

        values: set[str] = set()
        expected_cols = len(fieldnames)
        for row in reader:
            if not row or len(row) != expected_cols:
                continue
            raw_value = str(row[column_index]).strip()
            if raw_value:
                values.add(raw_value)
    return values


def validate_tsv_file_with_spec(
    *,
    key: str,
    path: Path,
    spec: dict[str, Any],
    expression_genes: set[str],
    expression_columns: set[str],
    expression_columns_count: int,
    extra_column_lookup: Any = None,
    unknown_cross_check: str = "warning",
) -> InputValidationResult:
    delimiter = str(spec.get("delimiter", "\t"))
    has_header = bool(spec.get("header", True))
    min_rows = int(spec.get("min_rows", 0) or 0)
    min_columns = int(spec.get("min_columns", 1) or 1)
    required_columns = [
        c for c in spec.get("required_columns", []) if isinstance(c, str)
    ]
    column_types = spec.get("column_types", {})
    if not isinstance(column_types, dict):
        column_types = {}
    first_column_role = str(spec.get("first_column_role", "none") or "none").strip()
    unique_first_column = bool(spec.get("unique_first_column", False))
    first_column_disallowed_names = {
        str(x).strip().lower()
        for x in spec.get("first_column_disallowed_names", [])
        if isinstance(x, str) and str(x).strip()
    }
    if first_column_role == "gene_id" and not first_column_disallowed_names:
        first_column_disallowed_names = {
            "cell",
            "cells",
            "sample",
            "samples",
            "timepoint",
            "timepoints",
            "perturbation",
            "perturbations",
            "cluster",
            "pseudotime",
        }
    data_columns_type = str(spec.get("data_columns_type", "any") or "any").strip()
    if data_columns_type not in {"any", "float"}:
        data_columns_type = "any"
    data_numeric_min_fraction = float(spec.get("data_numeric_min_fraction", 1.0) or 1.0)
    data_numeric_min_fraction = max(0.0, min(1.0, data_numeric_min_fraction))
    cross_checks = spec.get("cross_checks", [])
    if not isinstance(cross_checks, list):
        cross_checks = []

    errors: list[str] = []
    warnings: list[str] = []
    row_count = 0
    columns_count = 0
    values_by_column: dict[str, set[str]] = {}
    first_column_seen: set[str] = set()
    first_column_values: set[str] = set()
    data_column_names: set[str] = set()
    data_cells_total = 0
    data_cells_numeric = 0
    data_non_numeric_samples: list[str] = []

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader_raw = csv.reader(handle, delimiter=delimiter)
        if has_header:
            header_row = next(reader_raw, None)
            fieldnames = [str(value).strip() for value in header_row] if header_row else []
            columns_count = len(fieldnames)
            if not fieldnames:
                errors.append(f"{path}: expected header row with column names")
            if columns_count and columns_count < min_columns:
                errors.append(
                    f"{path}: expected at least {min_columns} column(s) for '{key}', got {columns_count}"
                )
            if any(not name for name in fieldnames):
                errors.append(f"{path}: header contains empty column names")
            if columns_count > 1:
                data_column_names = {
                    str(name).strip() for name in fieldnames[1:] if str(name).strip()
                }
            if first_column_role == "gene_id" and fieldnames:
                first_header = str(fieldnames[0]).strip().lower()
                if first_header in first_column_disallowed_names:
                    errors.append(
                        f"{path}: first column header '{fieldnames[0]}' is not valid for expression genes"
                    )
            missing_required = [c for c in required_columns if c not in fieldnames]
            if missing_required:
                errors.append(
                    f"{path}: missing required columns for '{key}': {missing_required}"
                )
            tracked_columns = {
                str(col).strip()
                for col in list(column_types.keys())
                + [
                    str(rule.get("column", "")).strip()
                    for rule in cross_checks
                    if isinstance(rule, dict)
                ]
                if isinstance(col, str) and str(col).strip()
            }
            index_by_name = {name: idx for idx, name in enumerate(fieldnames)}

            for line_idx, row in enumerate(reader_raw, start=2):
                if not row:
                    continue
                if len(row) != columns_count:
                    errors.append(
                        f"{path}: inconsistent number of columns at line {line_idx} "
                        f"(expected {columns_count}, got {len(row)})"
                    )
                    continue
                row_count += 1
                _track_first_column(
                    errors=errors,
                    first_column_role=first_column_role,
                    unique_first_column=unique_first_column,
                    first_column_seen=first_column_seen,
                    first_column_values=first_column_values,
                    row=row,
                    columns_count=columns_count,
                    path=path,
                    line_idx=line_idx,
                )
                data_cells_total, data_cells_numeric = _track_float_data_columns(
                    data_columns_type=data_columns_type,
                    columns_count=columns_count,
                    row=row,
                    line_idx=line_idx,
                    data_non_numeric_samples=data_non_numeric_samples,
                    data_cells_total=data_cells_total,
                    data_cells_numeric=data_cells_numeric,
                )
                _track_named_columns(
                    errors=errors,
                    values_by_column=values_by_column,
                    column_types=column_types,
                    tracked_columns=tracked_columns,
                    index_by_name=index_by_name,
                    row=row,
                    path=path,
                    line_idx=line_idx,
                )
        else:
            expected_cols: Optional[int] = None
            for line_idx, row in enumerate(reader_raw, start=1):
                if not row:
                    continue
                if expected_cols is None:
                    expected_cols = len(row)
                    columns_count = len(row)
                    if columns_count < min_columns:
                        errors.append(
                            f"{path}: expected at least {min_columns} column(s) for '{key}', got {columns_count}"
                        )
                elif len(row) != expected_cols:
                    errors.append(
                        f"{path}: inconsistent number of columns at line {line_idx} "
                        f"(expected {expected_cols}, got {len(row)})"
                    )
                    continue
                row_count += 1
                _track_first_column(
                    errors=errors,
                    first_column_role=first_column_role,
                    unique_first_column=unique_first_column,
                    first_column_seen=first_column_seen,
                    first_column_values=first_column_values,
                    row=row,
                    columns_count=columns_count,
                    path=path,
                    line_idx=line_idx,
                )
                data_cells_total, data_cells_numeric = _track_float_data_columns(
                    data_columns_type=data_columns_type,
                    columns_count=columns_count,
                    row=row,
                    line_idx=line_idx,
                    data_non_numeric_samples=data_non_numeric_samples,
                    data_cells_total=data_cells_total,
                    data_cells_numeric=data_cells_numeric,
                )

    if row_count < min_rows:
        errors.append(
            f"{path}: expected at least {min_rows} row(s) for '{key}', got {row_count}"
        )
    if columns_count and columns_count < min_columns:
        errors.append(
            f"{path}: expected at least {min_columns} column(s) for '{key}', got {columns_count}"
        )
    if data_columns_type == "float" and columns_count > 1:
        if data_cells_total <= 0:
            errors.append(
                f"{path}: expected numeric values in data columns for '{key}', but found none"
            )
        else:
            numeric_fraction = data_cells_numeric / data_cells_total
            if numeric_fraction < data_numeric_min_fraction:
                errors.append(
                    f"{path}: only {numeric_fraction:.3f} of data cells are numeric for '{key}' "
                    f"(required >= {data_numeric_min_fraction:.3f}); "
                    f"examples: {data_non_numeric_samples[:5]}"
                )

    _apply_cross_checks(
        key=key,
        path=path,
        cross_checks=cross_checks,
        row_count=row_count,
        values_by_column=values_by_column,
        first_column_values=first_column_values,
        data_column_names=data_column_names,
        expression_genes=expression_genes,
        expression_columns=expression_columns,
        expression_columns_count=expression_columns_count,
        extra_column_lookup=extra_column_lookup,
        unknown_cross_check=unknown_cross_check,
        errors=errors,
        warnings=warnings,
    )

    status = "error" if errors else "warning" if warnings else "ok"
    return InputValidationResult(
        status=status,
        errors=errors,
        warnings=warnings,
        rows=row_count,
        columns=columns_count,
        values_by_column=values_by_column,
        first_column_values=first_column_values,
    )


def validate_text_list_with_spec(
    *,
    key: str,
    path: Path,
    spec: dict[str, Any],
    expression_genes: set[str],
    unknown_cross_check: str = "warning",
) -> InputValidationResult:
    min_rows = int(spec.get("min_rows", 0) or 0)
    cross_checks = spec.get("cross_checks", [])
    if not isinstance(cross_checks, list):
        cross_checks = []

    values = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    errors: list[str] = []
    warnings: list[str] = []
    if len(values) < min_rows:
        errors.append(
            f"{path}: expected at least {min_rows} line(s) for '{key}', got {len(values)}"
        )

    for rule in cross_checks:
        if not isinstance(rule, dict):
            continue
        kind = str(rule.get("kind", "")).strip()
        if kind == "line_subset_expression_genes":
            unknown = sorted(set(values).difference(expression_genes))
            if unknown:
                errors.append(
                    f"{path}: contains identifiers not present in expression genes: {unknown[:5]}"
                )
        elif kind:
            message = f"[{key}] unknown cross-check ignored: {kind}"
            if unknown_cross_check == "error":
                errors.append(message)
            else:
                warnings.append(message)

    status = "error" if errors else "warning" if warnings else "ok"
    return InputValidationResult(
        status=status,
        errors=errors,
        warnings=warnings,
        rows=len(values),
        columns=1,
        line_values=values,
    )


def _track_first_column(
    *,
    errors: list[str],
    first_column_role: str,
    unique_first_column: bool,
    first_column_seen: set[str],
    first_column_values: set[str],
    row: list[str],
    columns_count: int,
    path: Path,
    line_idx: int,
) -> None:
    if (
        first_column_role not in {"gene_id", "expression_column_id"}
        and not unique_first_column
    ):
        return
    first_value = str(row[0]).strip() if columns_count >= 1 else ""
    if first_column_role == "gene_id":
        label = "gene identifier"
    elif first_column_role == "expression_column_id":
        label = "expression-column identifier"
    else:
        label = "identifier"
    if not first_value:
        errors.append(f"{path}: line {line_idx} has empty {label} in first column")
        return
    if first_column_role in {"gene_id", "expression_column_id"} or unique_first_column:
        first_column_values.add(first_value)
    if unique_first_column:
        if first_value in first_column_seen:
            errors.append(f"{path}: duplicated {label} '{first_value}' in first column")
        else:
            first_column_seen.add(first_value)


def _track_float_data_columns(
    *,
    data_columns_type: str,
    columns_count: int,
    row: list[str],
    line_idx: int,
    data_non_numeric_samples: list[str],
    data_cells_total: int,
    data_cells_numeric: int,
) -> tuple[int, int]:
    if data_columns_type != "float" or columns_count <= 1:
        return data_cells_total, data_cells_numeric
    for col_idx, raw_value in enumerate(row[1:], start=2):
        value = str(raw_value).strip()
        data_cells_total += 1
        if value == "":
            if len(data_non_numeric_samples) < 5:
                data_non_numeric_samples.append(f"line {line_idx}, col {col_idx}: <empty>")
            continue
        try:
            number = float(value)
        except Exception:  # noqa: BLE001
            if len(data_non_numeric_samples) < 5:
                data_non_numeric_samples.append(f"line {line_idx}, col {col_idx}: {value!r}")
        else:
            if math.isfinite(number):
                data_cells_numeric += 1
            elif len(data_non_numeric_samples) < 5:
                data_non_numeric_samples.append(f"line {line_idx}, col {col_idx}: {value!r}")
    return data_cells_total, data_cells_numeric


def _track_named_columns(
    *,
    errors: list[str],
    values_by_column: dict[str, set[str]],
    column_types: dict[str, Any],
    tracked_columns: set[str],
    index_by_name: dict[str, int],
    row: list[str],
    path: Path,
    line_idx: int,
) -> None:
    for column, type_name in column_types.items():
        idx = index_by_name.get(str(column))
        if idx is None:
            continue
        raw_value = row[idx]
        try:
            coerced = _coerce_cell_type(
                value=raw_value if raw_value is not None else "",
                type_name=str(type_name),
                field_name=str(column),
                file_path=path,
                line_idx=line_idx,
            )
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if coerced is not None:
            values_by_column.setdefault(str(column), set()).add(coerced)
    for column in tracked_columns:
        idx = index_by_name.get(column)
        if idx is None:
            continue
        raw_value = str(row[idx]).strip()
        if raw_value:
            values_by_column.setdefault(column, set()).add(raw_value)


def _coerce_cell_type(
    *,
    value: str,
    type_name: str,
    field_name: str,
    file_path: Path,
    line_idx: int,
) -> Optional[str]:
    stripped = str(value or "").strip()
    if stripped == "":
        return None

    if type_name == "string":
        return stripped
    if type_name == "int":
        try:
            int(stripped)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                f"{file_path}: line {line_idx}, field '{field_name}' must be int"
            ) from exc
        return stripped
    if type_name == "float":
        try:
            number = float(stripped)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                f"{file_path}: line {line_idx}, field '{field_name}' must be float"
            ) from exc
        if not math.isfinite(number):
            raise ValueError(
                f"{file_path}: line {line_idx}, field '{field_name}' must be finite"
            )
        return stripped
    if type_name == "bool":
        normalized = stripped.lower()
        if normalized not in {"true", "false", "0", "1"}:
            raise ValueError(
                f"{file_path}: line {line_idx}, field '{field_name}' must be bool"
            )
        return stripped

    raise ValueError(
        f"Unsupported input spec type '{type_name}' for field '{field_name}'"
    )


def _apply_cross_checks(
    *,
    key: str,
    path: Path,
    cross_checks: list[Any],
    row_count: int,
    values_by_column: dict[str, set[str]],
    first_column_values: set[str],
    data_column_names: set[str],
    expression_genes: set[str],
    expression_columns: set[str],
    expression_columns_count: int,
    extra_column_lookup: Any,
    unknown_cross_check: str,
    errors: list[str],
    warnings: list[str],
) -> None:
    for rule in cross_checks:
        if not isinstance(rule, dict):
            continue
        kind = str(rule.get("kind", "")).strip()
        column = str(rule.get("column", "")).strip()
        if kind == "column_subset_expression_genes":
            _append_unknown_subset_error(
                errors=errors,
                path=path,
                label=f"column '{column}'",
                values=values_by_column.get(column, set()),
                allowed=expression_genes,
                target="expression genes",
            )
        elif kind == "column_subset_expression_columns":
            _append_unknown_subset_error(
                errors=errors,
                path=path,
                label=f"column '{column}'",
                values=values_by_column.get(column, set()),
                allowed=expression_columns,
                target="expression columns",
            )
        elif kind == "first_column_subset_expression_columns":
            _append_unknown_subset_error(
                errors=errors,
                path=path,
                label="first column",
                values=first_column_values,
                allowed=expression_columns,
                target="expression columns",
            )
        elif kind == "data_columns_subset_expression_columns":
            _append_unknown_subset_error(
                errors=errors,
                path=path,
                label="data column headers",
                values=data_column_names,
                allowed=expression_columns,
                target="expression columns",
            )
        elif kind == "data_columns_match_expression_columns":
            missing = sorted(expression_columns.difference(data_column_names))[:5]
            extra = sorted(data_column_names.difference(expression_columns))[:5]
            if missing or extra:
                errors.append(
                    f"{path}: data column headers must match expression columns; "
                    f"missing={missing}, extra={extra}"
                )
        elif kind == "column_subset_extra_column":
            other_input = str(rule.get("other_input", "")).strip()
            other_column = str(rule.get("other_column", "")).strip()
            reference_values, lookup_warning = _lookup_cross_check_values(
                extra_column_lookup=extra_column_lookup,
                other_input=other_input,
                other_column=other_column,
            )
            if lookup_warning:
                warnings.append(
                    f"{path}: skipped cross-check for column '{column}' against "
                    f"extra '{other_input}.{other_column}': {lookup_warning}"
                )
                continue
            if reference_values is None:
                continue
            unknown = sorted(values_by_column.get(column, set()).difference(reference_values))
            if unknown:
                errors.append(
                    f"{path}: column '{column}' contains identifiers not present in "
                    f"extra '{other_input}.{other_column}': {unknown[:5]}"
                )
        elif kind == "row_count_matches_expression_columns":
            if row_count != expression_columns_count:
                errors.append(
                    f"{path}: row count ({row_count}) must match expression columns ({expression_columns_count})"
                )
        elif kind:
            message = f"[{key}] unknown cross-check ignored: {kind}"
            if unknown_cross_check == "error":
                errors.append(message)
            else:
                warnings.append(message)


def _lookup_cross_check_values(
    *,
    extra_column_lookup: Any,
    other_input: str,
    other_column: str,
) -> tuple[Optional[set[str]], Optional[str]]:
    if callable(extra_column_lookup):
        result = extra_column_lookup(other_input, other_column)
        if isinstance(result, tuple) and len(result) == 2:
            values, warning = result
            return values, warning
        if isinstance(result, set):
            return result, None
        if result is None:
            return None, None
        return set(result), None
    return None, "cross-check lookup unavailable"


def _append_unknown_subset_error(
    *,
    errors: list[str],
    path: Path,
    label: str,
    values: set[str],
    allowed: set[str],
    target: str,
) -> None:
    unknown = sorted(values.difference(allowed))
    if unknown:
        errors.append(
            f"{path}: {label} contains identifiers not present in {target}: {unknown[:5]}"
        )


__all__ = [
    "InputValidationResult",
    "read_tsv_column_values",
    "validate_text_list_with_spec",
    "validate_tsv_file_with_spec",
]
