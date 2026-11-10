"""Pinned PFBNet inference on observed longitudinal bulk expression courses."""

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

UPSTREAM_COMMIT = "86ec8eb37aa8834645600eaaee51012342ac6324"
UPSTREAM_SOURCE = {
    "path": "Ecoil/PFBNet_Ecoil.py",
    "module": "PFBNet_Ecoil",
    "sha256": "e6ee28b3114c7ef6a0933fb497232026547b6729378ce245967f6f33659b194e",
}

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
    """Prepare exact XGBoost0.60 source and mechanical Python API repairs."""
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
    """Repair fixed data geometry and observe fits, retaining native scoring."""
    if hashlib.sha256(raw).hexdigest() != UPSTREAM_SOURCE["sha256"]:
        raise ValueError("Unexpected PFBNet source: pinned SHA-256 is required")
    text = raw.decode("utf-8")

    def replace(old, new, count=1):
        nonlocal text
        if text.count(old) != count:
            raise ValueError(f"PFBNet patch context changed: {old!r}")
        text = text.replace(old, new)

    controls = (
        ", *, andrea_seed=0, andrea_threads=1, "
        "andrea_fit_callback=None, andrea_phase_callback=None"
    )
    replace(
        "def ecoil_main(data, sample_num, k, alpha, feature_num, iter_num):",
        "def ecoil_main(data, sample_num, k, alpha, feature_num, iter_num"
        + controls
        + "):",
    )
    replace(
        "    data1 = np.hstack((data[0,:,:],data[1,:,:],data[2,:,:]))\n"
        "    data2 = np.zeros((3,1484,int((np.shape(data1)[1])/3)))\n\n"
        "    for i in range(3):\n",
        "    data1 = np.hstack(tuple(data[i,:,:] for i in range(data.shape[0])))\n"
        "    data2 = np.zeros((data.shape[0],data.shape[1],sample_num))\n\n"
        "    for i in range(data.shape[0]):\n",
    )
    replace(
        "np.transpose(np.hstack((kdata[0,:,:],kdata[1,:,:],kdata[2,:,:])))",
        "np.transpose(np.hstack(tuple(kdata[i,:,:] for i in range(data_3d))))",
    )
    replace(
        "vv = xgboost_weight(data_tm, feature_num, iter_num)",
        "vv = xgboost_weight(data_tm, feature_num, iter_num, "
        "andrea_seed=andrea_seed, andrea_threads=andrea_threads, "
        "andrea_fit_callback=andrea_fit_callback)",
    )
    replace(
        "def xgboost_weight(data, feature_num,  iter_num):",
        "def xgboost_weight(data, feature_num, iter_num, *, "
        "andrea_seed=0, andrea_threads=1, andrea_fit_callback=None):",
    )
    replace(
        "    ## Use a statistical technology to refine the inferred GRN.\n",
        "    if andrea_phase_callback is not None:\n"
        "        andrea_phase_callback('aggregation')\n\n"
        "    ## Use a statistical technology to refine the inferred GRN.\n",
    )
    replace(
        "            'silent': 1,",
        "            'silent': 1,\n"
        "            'seed': andrea_seed,\n"
        "            'nthread': andrea_threads,",
    )
    replace(
        "        model = xgb.train(plst, dtrain, iter_num)\n",
        "        model = xgb.train(plst, dtrain, iter_num)\n"
        "        if andrea_fit_callback is not None:\n"
        "            andrea_fit_callback(i + 1, i, model, dict(params))\n",
    )
    replace(
        "            num = re.findall(r'\\d+', importance[j][0])\n"
        "            num = np.array(num)\n"
        "            num = np.core.defchararray.strip(num, '()')\n"
        "            num = int(num)",
        "            num = int(re.findall(r'\\d+', importance[j][0])[0])",
    )
    compile(text, UPSTREAM_SOURCE["path"], "exec")
    return text


def prepare_upstream(*, output_dir: Path, source_dir: Path | None = None):
    """Install the verified expression-only native module and its original."""
    import urllib.request

    output_dir.mkdir(parents=True, exist_ok=True)
    info = UPSTREAM_SOURCE
    raw = (
        (source_dir / info["path"]).read_bytes()
        if source_dir is not None
        else urllib.request.urlopen(
            f"https://raw.githubusercontent.com/ddche/PFBNet/{UPSTREAM_COMMIT}/{info['path']}",
            timeout=60,
        ).read()
    )
    patched = patched_source(raw).encode("utf-8")
    (output_dir / f"{info['module']}.original.py").write_bytes(raw)
    (output_dir / f"{info['module']}.py").write_bytes(patched)
    receipt = {
        "upstream_commit": UPSTREAM_COMMIT,
        "workflow": "expression_only",
        "upstream_path": info["path"],
        "original_source_sha256": hashlib.sha256(raw).hexdigest(),
        "patched_source_sha256": hashlib.sha256(patched).hexdigest(),
    }
    (output_dir / "pfbnet-source-receipt.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    return receipt


if __name__ == "__main__" and sys.argv[1:2] in (
    ["--prepare-upstream"],
    ["--prepare-xgboost"],
):
    operation = sys.argv[1]
    parser = argparse.ArgumentParser(
        description="Prepare verified native source; no fits"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    if operation == "--prepare-upstream":
        parser.add_argument("--source-dir", type=Path)
    else:
        parser.add_argument("--archives-dir", type=Path)
    arguments = parser.parse_args(sys.argv[2:])
    receipt = (
        prepare_upstream(
            output_dir=arguments.output_dir, source_dir=arguments.source_dir
        )
        if operation == "--prepare-upstream"
        else prepare_xgboost(
            output_dir=arguments.output_dir, archives_dir=arguments.archives_dir
        )
    )
    print(json.dumps(receipt, sort_keys=True))
    raise SystemExit(0)


import numpy as np

from _column_metadata import load_column_descriptors, summarize_temporal_metadata
from _run_tool_common import (
    load_execution_mode,
    load_params,
    validate_runtime_inputs,
    write_progress,
)

DEFAULTS = {"k": 2, "alpha": 0.45, "iter_num": 1000, "seed": 0}
FIXED_MODEL_PARAMS = {
    "booster": "gbtree",
    "max_depth": 4,
    "min_child_weight": 4,
    "lambda": 0,
    "subsample": 0.7,
    "colsample_bytree": 0.9,
    "silent": 1,
    "eta": 0.0008,
}


def parse_params(raw):
    if not isinstance(raw, dict):
        raise ValueError("PFBNet parameters must be a JSON object")
    unknown = set(raw) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown PFBNet parameters: {sorted(unknown)}")
    params = {**DEFAULTS, **raw}
    for name, minimum, maximum in (
        ("k", 1, None),
        ("iter_num", 1, None),
        ("seed", 0, 2**31 - 1),
    ):
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
    value = params["alpha"]
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 <= value <= 1
    ):
        raise ValueError("alpha must be a finite number from 0 to 1")
    return params


def read_matrix(path):
    with Path(path).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle, delimiter="\t"))
    if not rows or len(rows[0]) < 2 or len(rows) < 3:
        raise ValueError(
            "PFBNet requires a genes-by-observations TSV with at least two genes"
        )
    header, columns = rows[0], rows[0][1:]
    if any(not item or item != item.strip() for item in header) or len(
        set(header)
    ) != len(header):
        raise ValueError(
            "Expression headings must be unique exact nonempty identifiers"
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
                "Expression genes must be unique exact nonempty identifiers"
            )
        genes.append(gene)
        values.append([float(value) for value in row[1:]])
    expression = np.asarray(values, dtype=np.float64)
    if len(genes) < 2 or not np.isfinite(expression).all():
        raise ValueError("PFBNet requires at least two genes and finite expression")
    return genes, columns, expression


def _read_regulators(path, genes):
    if not path.is_file():
        return list(genes)
    regulators = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if (
        not regulators
        or len(set(regulators)) != len(regulators)
        or not set(regulators) <= set(genes)
    ):
        raise ValueError("tf_list must contain unique exact expression gene IDs")
    if len(regulators) < 2:
        raise ValueError(
            "PFBNet needs at least two candidate regulators because each target excludes itself"
        )
    return regulators


def prepare_data(expression_path, extra_dir, params):
    """Resolve exact column roles and independent regular series without averaging."""
    params = parse_params(params)
    extra_dir = Path(extra_dir)
    genes, columns, expression = read_matrix(expression_path)
    descriptors = load_column_descriptors(extra_dir / "column_descriptors.tsv", columns)
    temporal = summarize_temporal_metadata(descriptors)
    if temporal["sampling_design"] != "longitudinal":
        raise ValueError("PFBNet requires longitudinal observed bulk time series")
    groups, observations, stationary = {}, set(), []
    for position, column in enumerate(columns):
        row = descriptors[column]
        if row["observation_type"] == "steady_state":
            stationary.append(column)
            continue
        if row["observation_id"] in observations:
            raise ValueError(
                "PFBNet does not implicitly average time-series technical replicates"
            )
        observations.add(row["observation_id"])
        groups.setdefault(row["series_id"], []).append(
            (row["timepoint"], position, row)
        )
    blocks, series, expected_length, common_interval = [], [], None, None
    for series_id, entries in sorted(groups.items()):
        entries.sort(key=lambda value: value[0])
        times = [value[0] for value in entries]
        conditions = {value[2]["condition"] for value in entries}
        units = {value[2]["replicate"] for value in entries}
        if len(conditions) != 1 or len(units) != 1:
            raise ValueError(
                "One series must identify one condition and biological replicate"
            )
        if len(times) <= params["k"]:
            raise ValueError("Each series needs more observed timepoints than k")
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
                "PFBNet requires strictly increasing regular observed times"
            )
        if expected_length is not None and len(times) != expected_length:
            raise ValueError(
                "PFBNet requires the same number of timepoints in every series"
            )
        if common_interval is not None and not math.isclose(
            interval, common_interval, rel_tol=1e-9, abs_tol=1e-12
        ):
            raise ValueError("PFBNet requires a common sampling interval across series")
        expected_length, common_interval = len(times), interval
        positions = [value[1] for value in entries]
        blocks.append(expression[:, positions].T.copy())
        series.append(
            {
                "series_id": series_id,
                "condition": next(iter(conditions)),
                "replicate": next(iter(units)),
                "columns": [columns[index] for index in positions],
                "timepoints": times,
            }
        )
    if not blocks:
        raise ValueError("PFBNet requires at least one time series")
    tf_path = extra_dir / "tf_list.txt"
    regulators = _read_regulators(tf_path, genes)
    native_genes = regulators + [gene for gene in genes if gene not in regulators]
    order = [genes.index(gene) for gene in native_genes]
    blocks = [part[:, order].copy() for part in blocks]

    # Validate the same arithmetic domains the unchanged native transforms use.
    # These matrices are validation receipts, not replacements for native code.
    k, alpha = params["k"], params["alpha"]
    transformed, labels = [], []
    with np.errstate(over="ignore", invalid="ignore"):
        for block in blocks:
            predictor = 0
            for lag in range(k + 1):
                predictor = (
                    predictor
                    + pow(alpha, k - lag) * block[lag : expected_length - k + lag]
                )
            transformed.append(predictor)
            labels.append(predictor)
        predictors = np.concatenate(transformed)
        targets = np.concatenate(labels)
        finite_native = all(
            np.isfinite(value).all() and np.isfinite(value.astype(np.float32)).all()
            for value in (predictors, targets)
        )
    if not finite_native:
        raise ValueError(
            "Transformed expression must remain finite at native XGBoost float32 precision"
        )
    return {
        "genes": genes,
        "native_genes": native_genes,
        "columns": columns,
        "expression": expression,
        "time_series": np.concatenate(blocks),
        "expression_only_data": np.stack([part.T for part in blocks]),
        "regulators": regulators,
        "descriptors": descriptors,
        "temporal": temporal,
        "series": series,
        "unused_steady_state_columns": stationary,
        "timepoints_per_series": expected_length,
        "sampling_interval": common_interval,
        "accumulation_offsets": list(range(k + 1)),
        "accumulation_durations": [value * common_interval for value in range(k + 1)],
        "effective_training_rows": len(predictors),
        "transformed_predictors": predictors,
        "transformed_targets": targets,
    }


def _write_json(path, payload):
    Path(path).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _write_matrix(path, values, rows, columns):
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["row", *columns])
        writer.writerows(
            [label, *(repr(float(value)) for value in row)]
            for label, row in zip(rows, values)
        )


def export_network(path, matrix, source_genes, target_genes):
    values = np.asarray(matrix, dtype=np.float64)
    if values.shape != (len(source_genes), len(target_genes)):
        raise ValueError(
            "Native PFBNet matrix dimensions do not match its gene identifiers"
        )
    if not np.isfinite(values).all() or np.any(values < 0):
        raise ValueError("Native PFBNet confidence must be finite and nonnegative")
    rows = [
        (source, target, float(values[i, j]))
        for i, source in enumerate(source_genes)
        for j, target in enumerate(target_genes)
        if source != target and values[i, j] > 0
    ]
    rows.sort(key=lambda row: (-row[2], row[0], row[1]))
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["source", "target", "score", "sign", "evidence", "context"])
        writer.writerows(
            (source, target, repr(score), "?", "association", "global")
            for source, target, score in rows
        )
    return len(rows)


def _resource_snapshot(model, model_params):
    from threadpoolctl import threadpool_info

    result = {
        "pid": os.getpid(),
        "model_params": model_params,
        "native_defaults": {
            "objective": "reg:linear",
            "base_score": 0.5,
            "importance_type": "weight",
        },
        "observed_tree_count": len(model.get_dump()),
        "native_pools": threadpool_info(),
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


def _session(implementation):
    session = {
        "upstream_commit": UPSTREAM_COMMIT,
        "xgboost_commit": XGBOOST_COMMIT,
        "workflow": "expression_only",
        "packages": {},
        "patches": "Guarded expression data geometry; feature-index scalar API; seed/nthread controls and completed-fit/aggregation observation.",
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
            session["packages"][package] = "unavailable in recording test environment"
    source_path = getattr(implementation, "__file__", None)
    if source_path:
        session["patched_source_sha256"] = hashlib.sha256(
            Path(source_path).read_bytes()
        ).hexdigest()
    for name in ("pfbnet-source-receipt.json", "xgboost-source-receipt.json"):
        path = Path("/app") / name
        if path.is_file():
            session[name.removesuffix(".json")] = json.loads(path.read_text())
    if Path("/app/xgboost-source-receipt.json").is_file():
        backend = importlib.import_module("xgboost")
        session["native_libraries"] = {
            str(path): hashlib.sha256(Path(path).read_bytes()).hexdigest()
            for path in backend.libpath.find_lib_path()
        }
    return session


def run(*, input_path, params_path, extra_dir, output_dir, threads, upstream=None):
    input_path, params_path, extra_dir, output_dir = map(
        Path, (input_path, params_path, extra_dir, output_dir)
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    raw = output_dir / "provenance"
    raw.mkdir(exist_ok=True)
    completed, total, last_percent = 0, None, 0

    def progress(*, phase, message, percent, status="running", error=None):
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
            handle.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")

    progress(phase="validate", message="Validating PFBNet temporal inputs", percent=0)
    try:
        validate_runtime_inputs(
            input_path=input_path,
            params_path=params_path,
            extra_dir=extra_dir,
            threads=threads,
        )
        load_execution_mode(params_path, supported_modes={"global"})
        params = parse_params(load_params(params_path))
        data = prepare_data(input_path, extra_dir, params)
        total = len(data["native_genes"])
        observed_columns = [
            column for row in data["series"] for column in row["columns"]
        ]
        _write_matrix(
            raw / "native-timeseries.tsv",
            data["time_series"],
            observed_columns,
            data["native_genes"],
        )
        effective_ids = [
            f"{row['series_id']}:{index}"
            for row in data["series"]
            for index in range(params["k"], data["timepoints_per_series"])
        ]
        for key, filename in (
            ("transformed_predictors", "transformed-predictors.tsv"),
            ("transformed_targets", "transformed-targets.tsv"),
        ):
            _write_matrix(
                raw / filename, data[key], effective_ids, data["native_genes"]
            )
        fixed = {**FIXED_MODEL_PARAMS, "gamma": 0, "alpha": 0}
        input_paths = [input_path, params_path, extra_dir / "column_descriptors.tsv"]
        input_paths.extend(
            path for path in (extra_dir / "tf_list.txt",) if path.is_file()
        )
        resolved = {
            "params": params,
            "fixed_model_params": fixed,
            "genes": data["genes"],
            "native_genes": data["native_genes"],
            "regulators": data["regulators"],
            "temporal": data["temporal"],
            "series": data["series"],
            "column_descriptors": data["descriptors"],
            "unused_steady_state_columns": data["unused_steady_state_columns"],
            "samples_argument": data["timepoints_per_series"],
            "independent_series_count": len(data["series"]),
            "timepoints_per_series": data["timepoints_per_series"],
            "sampling_interval": data["sampling_interval"],
            "accumulation_offsets": data["accumulation_offsets"],
            "accumulation_durations": data["accumulation_durations"],
            "accumulation_reference": "Backward sample offsets from each accumulated window's endpoint, including the contemporaneous observation. These describe preprocessing, not inferred edge-specific delays.",
            "effective_training_rows": data["effective_training_rows"],
            "candidate_regulator_count": len(data["regulators"]),
            "effective_predictor_columns": {
                gene: len(data["regulators"]) - int(gene in data["regulators"])
                for gene in data["native_genes"]
            },
            "threads": threads,
            "native_nthread_argument": threads,
            "input_sha256": {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in input_paths
            },
            "normalization": "Native simultaneous accumulated-expression regression and population-1 row variance multiplier; no prior or L2 normalization.",
        }
        _write_json(raw / "resolved.json", resolved)
        os.environ["OMP_NUM_THREADS"] = str(threads)
        implementation = (
            upstream
            if upstream is not None
            else importlib.import_module(UPSTREAM_SOURCE["module"])
        )
        _write_json(raw / "session.json", _session(implementation))
        progress(
            phase="inference", message="Fitting pinned PFBNet target models", percent=5
        )

        def fitted(count, target_index, model, model_params):
            nonlocal completed
            if count != completed + 1 or target_index != completed or count > total:
                raise ValueError(
                    "Native PFBNet returned inconsistent completed-fit observations"
                )
            completed = count
            snapshot = _resource_snapshot(model, model_params)
            snapshot.update(
                {
                    "target_gene": data["native_genes"][target_index],
                    "completed_fits": completed,
                    "effective_predictor_columns": resolved[
                        "effective_predictor_columns"
                    ][data["native_genes"][target_index]],
                    "predictor_count_basis": "Candidate regulator prefix with the current target excluded when present; derived from resolved input geometry.",
                }
            )
            _write_json(raw / f"model-{target_index:08d}.json", snapshot)
            if snapshot["observed_tree_count"] != params["iter_num"]:
                raise ValueError("Native PFBNet tree count differs from iter_num")
            progress(
                phase="inference",
                message=f"Fitted target model for {data['native_genes'][target_index]}",
                percent=5 + 80 * completed // total,
            )

        def phase_changed(phase):
            if phase != "aggregation" or completed != total:
                raise ValueError(
                    "Native PFBNet aggregation began before all target fits completed"
                )
            progress(
                phase="aggregation",
                message="Combining native PFBNet confidences",
                percent=90,
            )

        from threadpoolctl import threadpool_limits

        controls = {
            "andrea_seed": params["seed"],
            "andrea_threads": threads,
            "andrea_fit_callback": fitted,
            "andrea_phase_callback": phase_changed,
        }
        # Limit both DMatrix conversion and native fitting. The old Booster's
        # explicit nthread controls OpenMP; unrelated BLAS pools stay serial.
        with (
            threadpool_limits(limits=1, user_api="blas"),
            threadpool_limits(limits=threads, user_api="openmp"),
            np.errstate(over="raise", invalid="raise", divide="raise"),
        ):
            matrix = implementation.ecoil_main(
                data["expression_only_data"],
                data["timepoints_per_series"],
                params["k"],
                params["alpha"],
                len(data["regulators"]),
                params["iter_num"],
                **controls,
            )
        if completed != total:
            raise ValueError(
                "PFBNet returned before every native target fit was observed"
            )
        progress(
            phase="export", message="Exporting native PFBNet confidence", percent=95
        )
        matrix = np.asarray(matrix, dtype=np.float64)
        if matrix.shape != (len(data["regulators"]), len(data["native_genes"])):
            raise ValueError(
                "Native PFBNet returned an unexpected confidence matrix shape"
            )
        _write_matrix(
            raw / "native-score-matrix.tsv",
            matrix,
            data["regulators"],
            data["native_genes"],
        )
        resolved["network_edge_count"] = export_network(
            output_dir / "network.csv", matrix, data["regulators"], data["native_genes"]
        )
        _write_json(raw / "resolved.json", resolved)
        progress(
            status="completed",
            phase="done",
            message="PFBNet network exported",
            percent=100,
        )
        return resolved
    except Exception as error:
        progress(
            status="failed",
            phase="failed",
            message="PFBNet failed",
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
    arguments = parser.parse_args()
    run(
        input_path=arguments.input,
        params_path=arguments.params,
        extra_dir=arguments.extra,
        output_dir=arguments.output_dir,
        threads=arguments.threads,
    )


if __name__ == "__main__":
    main()
