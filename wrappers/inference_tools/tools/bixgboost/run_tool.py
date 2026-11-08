"""Pinned BiXGBoost bidirectional lagged inference on observed bulk courses."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import importlib.metadata
import json
import math
import os
import sys
import time
from pathlib import Path

UPSTREAM_COMMIT = "e01c00dda5d447e68d476d82b51bccb54f2f3c23"
UPSTREAM_SHA256 = "722f62742d0536d6f57793b95c93d3e6f17c6036b2a68c93b52f7b7f669be25b"
UPSTREAM_URL = f"https://raw.githubusercontent.com/zrq0123/BiXGBoost/{UPSTREAM_COMMIT}/BiXGBoost.py"
XGBOOST_COMMIT = "4a8d63b6c8711fb839c71e26c659936252df1eb5"
XGBOOST_ARCHIVES = (
    (
        "xgboost",
        "dmlc/xgboost",
        XGBOOST_COMMIT,
        "708257ec1c023246169a6f23a59c1aec230e68ed8fc49806dc11745df0ca585c",
        "",
    ),
    (
        "dmlc-core",
        "dmlc/dmlc-core",
        "d8d4dccb8bd89af9f57aa19e7523f0b755563ca4",
        "292a0050d2a15d939ca10df19e88e52c42c9aacbad18ed3acd06bac10fe74308",
        "dmlc-core",
    ),
    (
        "rabit",
        "dmlc/rabit",
        "2dd7476ad7e4afe97eb579d39aab226ff74400a6",
        "5132d0f033dd6ece8dd72e9eaf7b460e3083e51ac03e8415af30602a8b05003c",
        "rabit",
    ),
)


def prepare_xgboost(*, output_dir: Path, archives_dir: Path | None = None):
    """Prepare exact0.60 source/submodules and mechanical Python API fixes."""
    import io
    import tarfile
    import urllib.request

    output_dir.mkdir(parents=True, exist_ok=True)
    receipt = {"xgboost_commit": XGBOOST_COMMIT, "archives": {}, "python_patches": {}}
    for name, repo, commit, expected_hash, subdir in XGBOOST_ARCHIVES:
        filename = f"{name}-{commit}.tar.gz"
        raw = (
            (archives_dir / filename).read_bytes()
            if archives_dir is not None
            else urllib.request.urlopen(
                f"https://codeload.github.com/{repo}/tar.gz/{commit}", timeout=60
            ).read()
        )
        if hashlib.sha256(raw).hexdigest() != expected_hash:
            raise ValueError(
                f"Unexpected {name} source archive: pinned SHA-256 is required"
            )
        target_root = output_dir / subdir
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
            for member in archive.getmembers():
                parts = Path(member.name).parts
                if len(parts) < 2:
                    continue
                relative = Path(*parts[1:])
                if (
                    relative.is_absolute()
                    or ".." in relative.parts
                    or member.issym()
                    or member.islnk()
                ):
                    raise ValueError(
                        "Source archives must contain safe regular files/directories"
                    )
                target = target_root / relative
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.extractfile(member).read())
                    target.chmod(member.mode & 0o777)
                else:
                    raise ValueError("Unsupported source archive entry")
        receipt["archives"][name] = {"commit": commit, "sha256": expected_hash}
    patches = (
        (
            "xgboost/compat.py",
            "3106f41491d88b8523f1ca2a0a4f072f11f379db4eebdb72089a89198b9b97b1",
            (
                (
                    "from sklearn.cross_validation import KFold, StratifiedKFold",
                    "from sklearn.model_selection import KFold, StratifiedKFold",
                ),
            ),
        ),
        (
            "xgboost/core.py",
            "a32945898ca68a0fff15318a0a9c1fc88f4b9a4c7f5a38bca0c9a9631f815034",
            (
                (
                    "import collections\n",
                    "import collections\nimport collections.abc\n",
                ),
                ("collections.Mapping", "collections.abc.Mapping"),
            ),
        ),
        (
            "setup.py",
            "826961df9f7b340fc4c8d53245c8678bcf55e47401d95839ebd38b02705effe7",
            (
                (
                    "LIB_PATH = libpath['find_lib_path']()",
                    "LIB_PATH = [os.path.relpath(libpath['find_lib_path']()[0], CURRENT_DIR)]",
                ),
            ),
        ),
        (
            "xgboost/sklearn.py",
            "4d53b5c92c2828764ed654a75d02cee84704633791a77f5f4703edc9763f9069",
            (),
        ),
    )
    originals = output_dir / "andrea-original-python"
    originals.mkdir(exist_ok=True)
    for relative, expected_hash, replacements in patches:
        path = output_dir / "python-package" / relative
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected_hash:
            raise ValueError(f"Unexpected XGBoost Python source: {relative}")
        (originals / Path(relative).name).write_bytes(raw)
        source = raw.decode("utf-8")
        for old, new in replacements:
            if source.count(old) != 1:
                raise ValueError(
                    f"XGBoost compatibility patch context changed: {relative}"
                )
            source = source.replace(old, new)
        if relative == "xgboost/sklearn.py":
            # The0.6 regressor lacks the property used by BiXGBoost. Reuse the
            # original classifier's identical booster weight implementation.
            source += "\n# ANDREA: share the original v0.60 weight property with its regressor.\nXGBRegressor.feature_importances_ = XGBClassifier.feature_importances_\n"
        compile(source, relative, "exec")
        path.write_text(source, encoding="utf-8")
        receipt["python_patches"][relative] = {
            "original_sha256": expected_hash,
            "patched_sha256": hashlib.sha256(source.encode()).hexdigest(),
        }
    (output_dir / "andrea-source-receipt.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    return receipt


def patched_source(raw: bytes) -> str:
    """Adapt public APIs and observe real fits without changing native scores."""
    if hashlib.sha256(raw).hexdigest() != UPSTREAM_SHA256:
        raise ValueError("Unexpected BiXGBoost source: pinned SHA-256 is required")
    text = raw.decode("utf-8")

    def replace(old, new, count=1):
        nonlocal text
        if text.count(old) != count:
            raise ValueError(f"BiXGBoost patch context changed: {old!r}")
        text = text.replace(old, new)

    replace("res_col=pd.Series()", "res_col=pd.Series(dtype=float)")
    replace(
        "res_col=res_col.append(tmp,ignore_index=True)",
        "res_col=pd.concat([res_col,tmp],ignore_index=True)",
    )
    replace(
        "pd.read_csv(expressionFile,'\\t')",
        "pd.read_csv(expressionFile,sep='\\t',float_precision='round_trip')",
    )
    replace(
        "def mainRun(expressionFile,samples,outputfile,p_lambda=0,p_alpha=1,timelag=2):",
        "def mainRun(expressionFile,samples,outputfile,p_lambda=0,p_alpha=1,timelag=2,*,andrea_seed=None,andrea_threads=1,andrea_fit_callback=None):",
    )
    replace(
        "seed=random.randint(1, 100)",
        "seed=random.randint(1, 100) if andrea_seed is None else andrea_seed",
    )
    replace(
        "colsample_bytree=0.6,seed=seed)",
        "colsample_bytree=0.6,seed=seed,nthread=andrea_threads)",
        2,
    )
    replace(
        "        err_1=mean_squared_error(clfx.predict(x_c), y_normal)",
        "        err_1=mean_squared_error(clfx.predict(x_c), y_normal)\n        if andrea_fit_callback is not None:\n            andrea_fit_callback(2*index+1,index,'in',clfx,err_1)",
    )
    replace(
        "        err_2=mean_squared_error(clf.predict(x_c), y_normal)",
        "        err_2=mean_squared_error(clf.predict(x_c), y_normal)\n        if andrea_fit_callback is not None:\n            andrea_fit_callback(2*index+2,index,'out',clf,err_2)",
    )
    # New sklearn versions may return a Python float rather than np.float64.
    # Explicit arrays preserve the original elementwise arithmetic in both cases.
    replace(
        "score_1=1-score_1/sum(score_1)", "score_1=1-np.asarray(score_1)/sum(score_1)"
    )
    replace(
        "score_2 =1-score_2 / sum(score_2)",
        "score_2 =1-np.asarray(score_2) / sum(score_2)",
    )
    text += "\n    return all_df\n"
    compile(text, "BiXGBoost.py", "exec")
    return text


def prepare_upstream(*, output_dir: Path, source: Path | None = None):
    if source is None:
        import urllib.request

        raw = urllib.request.urlopen(UPSTREAM_URL, timeout=60).read()
    else:
        raw = source.read_bytes()
    patched = patched_source(raw).encode("utf-8")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "BiXGBoost.original.py").write_bytes(raw)
    (output_dir / "BiXGBoost.py").write_bytes(patched)
    return {
        "upstream_commit": UPSTREAM_COMMIT,
        "original_source_sha256": hashlib.sha256(raw).hexdigest(),
        "patched_source_sha256": hashlib.sha256(patched).hexdigest(),
    }


if __name__ == "__main__" and sys.argv[1:2] == ["--prepare-xgboost"]:
    parser = argparse.ArgumentParser(
        description="Prepare exact XGBoost0.60 source; no fitting"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--archives-dir", type=Path)
    args = parser.parse_args(sys.argv[2:])
    print(
        json.dumps(
            prepare_xgboost(output_dir=args.output_dir, archives_dir=args.archives_dir),
            sort_keys=True,
        )
    )
    raise SystemExit(0)


if __name__ == "__main__" and sys.argv[1:2] == ["--prepare-upstream"]:
    parser = argparse.ArgumentParser(
        description="Prepare verified pinned BiXGBoost source; no fitting"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    args = parser.parse_args(sys.argv[2:])
    print(
        json.dumps(
            prepare_upstream(output_dir=args.output_dir, source=args.source),
            sort_keys=True,
        )
    )
    raise SystemExit(0)


import numpy as np

from _column_metadata import load_column_descriptors, summarize_temporal_metadata
from _run_tool_common import (
    load_execution_mode,
    load_params,
    validate_runtime_inputs,
    write_progress,
)

DEFAULTS = {"p_lambda": 0, "p_alpha": 1, "timelag": 2, "seed": 0}
FIXED_MODEL_PARAMS = {
    "max_depth": 3,
    "n_estimators": 1000,
    "learning_rate": 0.0001,
    "subsample": 0.8,
    "colsample_bylevel": 0.6,
    "colsample_bytree": 0.6,
    "objective": "reg:linear",
    "importance_type": "weight",
}


def resolve_params(raw):
    unknown = set(raw) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown BiXGBoost parameters: {sorted(unknown)}")
    params = {**DEFAULTS, **raw}
    for name in ("p_lambda", "p_alpha"):
        value = params[name]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError(f"{name} must be a finite nonnegative number")
    for name, minimum, maximum in (("timelag", 1, None), ("seed", 0, 2**31 - 1)):
        value = params[name]
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < minimum
            or maximum is not None
            and value > maximum
        ):
            raise ValueError(
                f"{name} must be an integer from {minimum}"
                + (f" to {maximum}" if maximum is not None else "")
            )
    return params


def read_matrix(path):
    with Path(path).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle, delimiter="\t"))
    if not rows or len(rows[0]) < 2 or len(rows) < 3:
        raise ValueError(
            "BiXGBoost needs a genes-by-observations TSV with at least two genes"
        )
    header, columns = rows[0], rows[0][1:]
    if any(not item or item != item.strip() for item in header) or len(
        set(header)
    ) != len(header):
        raise ValueError(
            "Expression headings must be unique, exact, nonempty identifiers"
        )
    genes, values = [], []
    for number, row in enumerate(rows[1:], 2):
        if not row:
            continue
        if len(row) != len(header):
            raise ValueError(f"Malformed expression row {number}")
        gene = row[0]
        if not gene or gene != gene.strip() or gene in genes:
            raise ValueError(
                "Expression gene IDs must be unique, exact, nonempty identifiers"
            )
        genes.append(gene)
        values.append([float(value) for value in row[1:]])
    matrix = np.asarray(values, dtype=np.float64)
    if len(genes) < 2 or not np.isfinite(matrix).all():
        raise ValueError("BiXGBoost requires at least two genes and finite expression")
    return genes, columns, matrix


def prepare_series(matrix, columns, extra_dir, timelag):
    """Form equal independent blocks; lags never cross biological courses."""
    descriptors = load_column_descriptors(
        Path(extra_dir) / "column_descriptors.tsv", columns
    )
    temporal = summarize_temporal_metadata(descriptors)
    if temporal["sampling_design"] != "longitudinal":
        raise ValueError("BiXGBoost requires longitudinal observed bulk time series")
    groups = {}
    observation_ids = set()
    unused = []
    for position, column in enumerate(columns):
        row = descriptors[column]
        if row["observation_type"] != "time_series":
            unused.append(column)
            continue
        if row["observation_id"] in observation_ids:
            raise ValueError(
                "BiXGBoost does not implicitly average technical replicates"
            )
        observation_ids.add(row["observation_id"])
        groups.setdefault(row["series_id"], []).append(
            (float(row["timepoint"]), position, row)
        )
    ordered, series, expected_length, common_interval = [], [], None, None
    for series_id, entries in sorted(groups.items()):
        entries.sort(key=lambda value: value[0])
        times = [value[0] for value in entries]
        conditions = {value[2]["condition"] for value in entries}
        units = {value[2]["replicate"] for value in entries}
        if len(conditions) != 1 or len(units) != 1:
            raise ValueError(
                "One series must identify one condition and biological replicate"
            )
        if len(times) <= timelag:
            raise ValueError("Each series needs more observed timepoints than timelag")
        intervals = np.diff(times)
        interval = float(intervals[0])
        if (
            not math.isfinite(interval)
            or interval <= 0
            or any(
                not math.isfinite(float(value))
                or not math.isclose(float(value), interval, rel_tol=1e-9, abs_tol=1e-12)
                for value in intervals
            )
        ):
            raise ValueError(
                "BiXGBoost requires strictly increasing regular observed times"
            )
        if expected_length is not None and len(times) != expected_length:
            raise ValueError(
                "BiXGBoost requires the same number of timepoints in every series"
            )
        if common_interval is not None and not math.isclose(
            interval, common_interval, rel_tol=1e-9, abs_tol=1e-12
        ):
            raise ValueError(
                "BiXGBoost requires one common sampling interval across series"
            )
        expected_length, common_interval = len(times), interval
        positions = [value[1] for value in entries]
        ordered.append(matrix[:, positions].T.copy())
        series.append(
            {
                "series_id": series_id,
                "condition": next(iter(conditions)),
                "replicate": next(iter(units)),
                "columns": [columns[index] for index in positions],
                "timepoints": times,
            }
        )
    if not ordered:
        raise ValueError("BiXGBoost requires at least one time series")
    effective_rows = sum(len(values) - timelag for values in ordered)
    if effective_rows < 2:
        raise ValueError(
            "BiXGBoost target normalization needs at least two pooled lagged observations"
        )
    # DMatrix0.60 converts every predictor to float32 before native training.
    # Reject overflow here instead of passing infinity into the old C++ engine.
    with np.errstate(over="ignore", invalid="ignore"):
        native_predictors = np.concatenate(ordered).astype(np.float32)
    if not np.isfinite(native_predictors).all():
        raise ValueError(
            "Temporal expression must remain finite at native XGBoost float32 precision"
        )
    for label, values in (
        ("in", np.concatenate([part[timelag:] for part in ordered])),
        ("out", np.concatenate([part[:-timelag] for part in ordered])),
    ):
        with np.errstate(over="ignore", invalid="ignore"):
            means, scales = np.mean(values, axis=0), np.std(values, axis=0)
            normalized = (values - means) / scales
        if (
            not np.isfinite(scales).all()
            or not np.all(scales > 0)
            or not np.isfinite(normalized).all()
        ):
            raise ValueError(
                f"Every {label}-model target must have finite positive pooled lagged variance"
            )
    return (
        np.concatenate(ordered),
        descriptors,
        {
            "temporal": temporal,
            "series": series,
            "unused_steady_state_columns": unused,
            "timepoints_per_series": expected_length,
            "sampling_interval": common_interval,
            "physical_lag_durations": [
                index * common_interval for index in range(1, timelag + 1)
            ],
            "effective_training_rows": effective_rows,
            "effective_predictor_columns": matrix.shape[0] * timelag,
        },
    )


def _write_json(path, payload):
    Path(path).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def export_network(path, all_df, genes, aliases):
    inverse = dict(zip(aliases, genes))
    rows = []
    expected = {
        (source, target) for source in aliases for target in aliases if source != target
    }
    seen = set()
    for key, row in all_df.iterrows():
        parts = key.split("\t")
        if len(parts) != 2 or tuple(parts) not in expected or tuple(parts) in seen:
            raise ValueError(
                "Native BiXGBoost returned an invalid or duplicate interaction"
            )
        seen.add(tuple(parts))
        score = float(row["total"])
        if not math.isfinite(score) or score < 0:
            raise ValueError(
                "Native BiXGBoost confidence must be finite and nonnegative"
            )
        if score > 0:
            rows.append((inverse[parts[0]], inverse[parts[1]], score))
    if seen != expected:
        raise ValueError("Native BiXGBoost did not return every non-self interaction")
    rows.sort(key=lambda row: (-row[2], row[0], row[1]))
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["source", "target", "score", "sign", "evidence", "context"])
        writer.writerows(
            (source, target, repr(score), "?", "association", "global")
            for source, target, score in rows
        )
    return len(rows)


def _resource_snapshot(model):
    from threadpoolctl import threadpool_info

    booster = model.booster()
    model_params = model.get_params(deep=False)
    # The original estimator's default missing sentinel is IEEE NaN. Preserve
    # its meaning as an explicitly labelled JSON representation, while leaving
    # the real estimator parameters untouched and disallowing invalid JSON NaN.
    for key, value in list(model_params.items()):
        if isinstance(value, (float, np.floating)) and not math.isfinite(value):
            model_params[key] = {
                "native_nonfinite_float": (
                    "NaN"
                    if math.isnan(value)
                    else "Infinity" if value > 0 else "-Infinity"
                )
            }
    result = {
        "pid": os.getpid(),
        "native_pools": threadpool_info(),
        "model_params": model_params,
        "observed_tree_count": len(booster.get_dump()),
        "cpu_affinity": (
            sorted(os.sched_getaffinity(0))
            if hasattr(os, "sched_getaffinity")
            else None
        ),
        "cgroups": {},
    }
    for name in ("cpu.max", "memory.max", "cpu.stat", "memory.current", "memory.peak"):
        path = Path("/sys/fs/cgroup") / name
        if path.is_file():
            result["cgroups"][name] = path.read_text().strip()
    return result


def run(*, input_path, params_path, extra_dir, output_dir, threads, upstream=None):
    validate_runtime_inputs(
        input_path=input_path,
        params_path=params_path,
        extra_dir=extra_dir,
        threads=threads,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    raw = output_dir / "provenance"
    raw.mkdir(exist_ok=True)
    completed, total, last_percent = 0, None, 0

    def progress(*, status="running", phase, message, percent, error=None):
        nonlocal last_percent
        last_percent = max(last_percent, percent)
        write_progress(
            output_dir / "progress.json",
            status=status,
            phase=phase,
            message=message,
            percent=last_percent,
            completed=completed,
            total=total,
            error=error,
        )
        event = json.loads((output_dir / "progress.json").read_text())
        event["observed_monotonic_ns"] = time.monotonic_ns()
        with (raw / "progress-events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    progress(
        phase="validate", message="Validating BiXGBoost temporal inputs", percent=0
    )
    try:
        load_execution_mode(params_path, supported_modes={"global"})
        genes, columns, expression = read_matrix(input_path)
        params = resolve_params(load_params(params_path))
        table, descriptors, prepared = prepare_series(
            expression, columns, extra_dir, params["timelag"]
        )
        total = 2 * len(genes)
        aliases = [f"g{index:08d}" for index in range(len(genes))]
        native_expression = raw / "native-expression.tsv"
        with native_expression.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t")
            writer.writerow(aliases)
            writer.writerows(table.tolist())
        resolved = {
            "params": params,
            "fixed_model_params": FIXED_MODEL_PARAMS,
            "genes": genes,
            "gene_aliases": dict(zip(aliases, genes)),
            **prepared,
            "samples_argument": len(prepared["series"]),
            "threads": threads,
            "native_nthread_argument": threads,
            "input_sha256": {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (
                    input_path,
                    extra_dir / "column_descriptors.tsv",
                    params_path,
                )
            },
            "target_normalization": "Each pooled lagged target is centered and scaled by population standard deviation separately for incoming/outgoing models; predictors remain unscaled.",
        }
        _write_json(raw / "resolved.json", resolved)
        implementation = (
            upstream if upstream is not None else importlib.import_module("BiXGBoost")
        )
        progress(
            phase="inference",
            message="Fitting pinned incoming and outgoing models",
            percent=5,
        )

        def fitted(count, target_index, direction, model, mse):
            nonlocal completed
            completed = count
            snapshot = _resource_snapshot(model)
            snapshot.update(
                {
                    "target_gene": genes[target_index],
                    "direction": direction,
                    "completed_fits": completed,
                    "training_mean_squared_error": float(mse),
                }
            )
            _write_json(raw / f"model-{target_index:08d}-{direction}.json", snapshot)
            progress(
                phase="inference",
                message=f"Fitted {direction} model for {genes[target_index]}",
                percent=5 + 85 * completed // total,
            )

        # XGBoost v0.60 controls its OpenMP pool through native nthread. Other
        # numerical libraries stay serial; no target-level worker pool is added.
        from threadpoolctl import threadpool_limits

        with threadpool_limits(limits=1, user_api="blas"):
            all_df = implementation.mainRun(
                str(native_expression),
                len(prepared["series"]),
                str(raw / "native-network.tsv"),
                p_lambda=params["p_lambda"],
                p_alpha=params["p_alpha"],
                timelag=params["timelag"],
                andrea_seed=params["seed"],
                andrea_threads=threads,
                andrea_fit_callback=fitted,
            )
        if completed != total:
            raise ValueError(
                "BiXGBoost returned before every native model fit was observed"
            )
        progress(
            phase="export",
            message="Exporting native bidirectional confidence",
            percent=95,
        )
        all_df.to_csv(
            raw / "bidirectional-scores.tsv", sep="\t", index_label="interaction"
        )
        count = export_network(output_dir / "network.csv", all_df, genes, aliases)
        resolved["network_edge_count"] = count
        _write_json(raw / "resolved.json", resolved)
        session = {
            "upstream_commit": UPSTREAM_COMMIT,
            "xgboost_commit": XGBOOST_COMMIT,
            "packages": {},
            "patches": "Modern pandas API; explicit error arrays; ANDREA seed/nthread and completed-fit observation; original v0.60 weight importance is retained.",
        }
        for package in (
            "numpy",
            "pandas",
            "scipy",
            "scikit-learn",
            "xgboost",
            "threadpoolctl",
        ):
            try:
                session["packages"][package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                session["packages"][
                    package
                ] = "unavailable in recording test environment"
        if getattr(implementation, "__file__", None):
            session["patched_source_sha256"] = hashlib.sha256(
                Path(implementation.__file__).read_bytes()
            ).hexdigest()
        source_receipt = Path("/app/xgboost-source-receipt.json")
        if source_receipt.is_file():
            session["xgboost_source_receipt"] = json.loads(source_receipt.read_text())
            backend = importlib.import_module("xgboost")
            session["native_libraries"] = {
                str(path): hashlib.sha256(Path(path).read_bytes()).hexdigest()
                for path in backend.libpath.find_lib_path()
            }
        _write_json(raw / "session.json", session)
        progress(
            status="completed",
            phase="done",
            message="BiXGBoost network exported",
            percent=100,
        )
        return resolved
    except Exception as error:
        progress(
            status="failed",
            phase="failed",
            message="BiXGBoost failed",
            percent=last_percent,
            error=str(error),
        )
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--params", required=True, type=Path)
    parser.add_argument("--extra", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--threads", required=True, type=int)
    args = parser.parse_args()
    run(
        input_path=args.input,
        params_path=args.params,
        extra_dir=args.extra,
        output_dir=args.output_dir,
        threads=args.threads,
    )


if __name__ == "__main__":
    main()
