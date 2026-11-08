"""Shared evidence and persistence helpers for maintenance benchmarks."""

from __future__ import annotations

import hashlib
import ast
import math
import importlib.util
import json
import os
import platform
import shlex
import stat
import symtable
import subprocess
import sys
import tempfile
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, ensure_ascii=True)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def is_generated_cost_artifact(name: str) -> bool:
    """Recognize cost outputs and atomic writer files, not arbitrary dotfiles.

    atomic_json creates .cost.json.<random> beside a catalog cost.json. Those
    short-lived files must not enter a source seal while costs are published.
    """
    path = PurePosixPath(name)
    if path.name == "cost.json":
        return True
    parts = path.parts
    return (
        len(parts) == 5
        and parts[0] == "andrea"
        and (parts[1], parts[2])
        in {
            ("catalog_inference_tools", "tools"),
            ("catalog_simulation_data_tools", "simulators"),
        }
        and parts[4].startswith(".cost.json.")
        and bool(parts[4][len(".cost.json.") :])
    )


def collect_provenance(
    *,
    root: Path,
    spec_path: Path,
    image: str,
    seed: int,
    source_dir: Path | None = None,
) -> dict:
    """Resolve the image once; callers run its immutable ID throughout a campaign."""

    def output(command: list[str]) -> str:
        return subprocess.check_output(command, cwd=root, text=True).strip()

    inspected = json.loads(output(["docker", "image", "inspect", image]))[0]
    cpu_model = platform.processor() or platform.machine()
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        cpu_model = next(
            (
                line.split(":", 1)[1].strip()
                for line in cpuinfo.read_text().splitlines()
                if line.startswith("model name")
            ),
            cpu_model,
        )
    memory = None
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        memory = next(
            (
                int(line.split()[1]) * 1024
                for line in meminfo.read_text().splitlines()
                if line.startswith("MemTotal:")
            ),
            None,
        )
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
    ).split(b"\0")
    source = hashlib.sha256()
    # Exclude generated measurements and artwork: regenerating another tool's
    # costs must not change the source identity of this benchmark.
    for raw in sorted(set(tracked)):
        if not raw:
            continue
        name = os.fsdecode(raw)
        if not name.startswith(
            ("andrea/", "wrappers/", "scripts/")
        ) or is_generated_cost_artifact(name):
            continue
        path = root / name
        if path.is_file():
            source.update(raw + b"\0" + path.read_bytes() + b"\0")
    result = {
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": output(["git", "rev-parse", "HEAD"]),
        "git_dirty": bool(output(["git", "status", "--porcelain"])),
        "source_sha256": source.hexdigest(),
        "spec_sha256": hashlib.sha256(spec_path.read_bytes()).hexdigest(),
        "build_context_sha256": build_context_fingerprint(
            root=root,
            identifier=spec_path.parent.name,
            kind="inference" if spec_path.name == "toolspec.json" else "simulation",
            source_dir=source_dir,
        ),
        "image_reference": image,
        "image_id": inspected["Id"],
        "image_repo_digests": inspected.get("RepoDigests") or [],
        "platform": platform.platform(),
        "cpu_model": cpu_model,
        "logical_cpus": os.cpu_count() or 1,
        "host_memory_bytes": memory,
        "docker_server": json.loads(
            output(["docker", "info", "--format", "{{json .}}"])
        ).get("ServerVersion"),
        "seed": seed,
        "timing_scope": "docker run wall time including container startup and output writing",
    }
    if spec_path.name == "toolspec.json":
        result["measurement_kernel_sha256"] = measurement_kernel_fingerprint(root)
    return result


def persistent_workdir(results_dir: Path, identifier: str) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"{identifier}-", dir=results_dir.resolve()))


def write_process_logs(directory: Path, result: Any) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for attribute in ("stdout", "stderr"):
        value = getattr(result, attribute, None) or ""
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")
        (directory / f"{attribute}.log").write_text(value, encoding="utf-8")


def merge_profiles(cost_path: Path, measured_payload: dict) -> dict:
    if not cost_path.exists():
        return measured_payload
    existing = json.loads(cost_path.read_text(encoding="utf-8"))
    replacements = {
        profile["profile_id"]: profile for profile in measured_payload["profiles"]
    }
    profiles = [
        replacements.pop(profile["profile_id"], profile)
        for profile in existing["profiles"]
    ]
    profiles.extend(replacements.values())
    return {**measured_payload, "profiles": profiles}


def provenance_errors(
    payload: dict,
    spec_path: Path,
    *,
    required: bool = False,
    build_context_sha256: str | None = None,
) -> list[str]:
    """Check recorded costs against the spec/image they purport to measure."""
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec_hash = hashlib.sha256(spec_path.read_bytes()).hexdigest()
    errors = []
    for profile in payload.get("profiles", []):
        label = profile.get("profile_id", "<unknown>")
        config = profile.get("benchmark_config", {})
        provenance = config.get("provenance")
        if not isinstance(provenance, dict):
            if required:
                errors.append(
                    f"{label}: missing benchmark provenance; regenerate this profile."
                )
            continue
        if provenance.get("spec_sha256") != spec_hash:
            errors.append(
                f"{label}: spec changed since calibration; regenerate this profile."
            )
        if required and provenance.get("image_reference") != spec.get("docker_image"):
            errors.append(f"{label}: release calibration must use --catalog-images.")
        if required and config.get("repeats", 0) < 3:
            errors.append(
                f"{label}: release calibration requires at least three timing repetitions."
            )
        if (
            required
            and build_context_sha256 is not None
            and provenance.get("build_context_sha256") != build_context_sha256
        ):
            errors.append(
                f"{label}: container build inputs changed or were not recorded; rebuild the image and regenerate this profile."
            )
    return errors


def load_script(path: Path, name: str):
    """Load a maintenance script under an unambiguous module name."""
    if name in sys.modules:
        return sys.modules[name]
    module_spec = importlib.util.spec_from_file_location(name, path)
    if module_spec is None or module_spec.loader is None:
        raise RuntimeError(f"Cannot load maintenance script: {path}")
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[name] = module
    module_spec.loader.exec_module(module)
    return module


def build_context_fingerprint(
    *,
    root: Path,
    kind: str,
    identifier: str,
    source_dir: Path | None = None,
) -> str:
    """Hash the Dockerfile and local COPY inputs, including injected templates.

    We deliberately exclude package metadata, other integrations and generated
    costs. Unsupported COPY syntax fails closed instead of issuing a misleading
    fingerprint. Image IDs separately identify downloaded/compiled dependencies.
    """
    virtual: dict[str, tuple[bytes, int]] = {}
    if kind == "inference":
        scripts = root / "wrappers/inference_tools/scripts"
        builder = load_script(
            scripts / "build_tool_images.py", "andrea_cost_build_tools"
        )
        mapping = json.loads((scripts / "template_map.json").read_text())
        config = builder.validate_template_map(
            mapping, {key: Path(key) for key in mapping["tools"]}
        )[identifier]
        virtual["run_tool.sh"] = (
            builder.render_entrypoint(config.runtime, config.native_threads).encode(),
            0o755,
        )
        for relative in config.templates:
            path = builder.resolve_template_source(relative, repo_root=root)
            virtual[Path(relative).name] = (
                path.read_bytes(),
                stat.S_IMODE(path.stat().st_mode),
            )
        context = source_dir or root / "wrappers/inference_tools/tools" / identifier
        dockerfile = context / "Dockerfile"
    elif kind == "simulation":
        context = root
        dockerfile = (
            source_dir
            or root / "wrappers/simulation_data_tools/simulators" / identifier
        ) / "Dockerfile"
    else:
        raise RuntimeError(f"Unknown benchmark kind: {kind}")

    files: dict[str, tuple[bytes, int]] = {
        "Dockerfile": (
            dockerfile.read_bytes(),
            stat.S_IMODE(dockerfile.stat().st_mode),
        ),
    }
    for ignore in (context / ".dockerignore", Path(str(dockerfile) + ".dockerignore")):
        if ignore.is_file():
            files["ignore:" + ignore.name] = (
                ignore.read_bytes(),
                stat.S_IMODE(ignore.stat().st_mode),
            )
    logical = dockerfile.read_text().replace("\\\n", " ")
    for line in logical.splitlines():
        pieces = line.strip().split(None, 1)
        if not pieces or pieces[0].upper() not in {"COPY", "ADD"}:
            continue
        if len(pieces) != 2:
            raise RuntimeError(f"Cannot fingerprint malformed COPY/ADD in {dockerfile}")
        arguments = pieces[1]
        flags = []
        while arguments.startswith("--"):
            flag, separator, arguments = arguments.partition(" ")
            if not separator or "=" not in flag:
                raise RuntimeError(
                    f"Cannot fingerprint COPY/ADD flag {flag!r} in {dockerfile}"
                )
            flags.append(flag)
            arguments = arguments.lstrip()
        if any(flag.startswith("--from=") for flag in flags):
            continue
        tokens = (
            json.loads(arguments)
            if arguments.startswith("[")
            else shlex.split(arguments)
        )
        if len(tokens) < 2:
            raise RuntimeError(f"Cannot fingerprint malformed COPY/ADD in {dockerfile}")
        for token in tokens[:-1]:
            if (
                not isinstance(token, str)
                or any(char in token for char in "$*?[")
                or "://" in token
            ):
                raise RuntimeError(
                    f"Cannot fingerprint dynamic/remote COPY/ADD input {token!r} in {dockerfile}"
                )
            relative = Path(token)
            if relative.is_absolute() or ".." in relative.parts:
                raise RuntimeError(f"COPY/ADD input escapes build context: {token!r}")
            key = relative.as_posix()
            if key in virtual:
                files[key] = virtual[key]
            else:
                path = context / relative
                if not path.is_file():
                    raise RuntimeError(
                        f"Cannot fingerprint COPY/ADD input {path}; expected a local file."
                    )
                files[key] = (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
    digest = hashlib.sha256()
    for name, (data, mode) in sorted(files.items()):
        digest.update(name.encode() + b"\0" + str(mode).encode() + b"\0" + data + b"\0")
    return digest.hexdigest()


def _inference_kernel(root: Path) -> dict[str, str]:
    """Hash executed input/timing code without hashing campaign orchestration.

    Resolve local dependencies from the actual measurement functions, rather
    than copying their implementation or treating every driver edit as a new
    experiment. Imports are recorded per name so adding an unrelated recovery
    import cannot change the kernel identity. Fixture generation remains sealed
    independently by its complete source; retained input bytes are also checked
    before an observation can be reused.
    """
    driver = root / "wrappers/inference_tools/scripts/benchmark_costs.py"
    tree = ast.parse(driver.read_text())

    class WithoutDocstrings(ast.NodeTransformer):
        def visit_FunctionDef(self, node):
            self.generic_visit(node)
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                node.body = node.body[1:]
            return node

        visit_AsyncFunctionDef = visit_FunctionDef
        visit_ClassDef = visit_FunctionDef

    tree = WithoutDocstrings().visit(tree)
    definitions = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            definitions[node.name] = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    definitions[target.id] = node
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for item in node.names:
                name = item.asname or (
                    item.name.split(".")[0]
                    if isinstance(node, ast.Import)
                    else item.name
                )
                definitions[name] = (
                    ast.Import(names=[item])
                    if isinstance(node, ast.Import)
                    else ast.ImportFrom(
                        module=node.module, names=[item], level=node.level
                    )
                )
    pending = {
        "run_container_once",
        "run_cmd",
        "prepare_io_dir",
        "percentile",
        "aggregate_runtime_points",
        "build_feature_vector",
    }
    missing = pending - definitions.keys()
    if missing:
        raise RuntimeError(f"Missing inference measurement kernel: {sorted(missing)}")
    selected = {}
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        node = definitions[name]
        selected[name] = ast.dump(node, include_attributes=False)
        # Python's symbol table resolves lexical scope (including nested
        # functions/comprehensions). A local variable named ``run`` must not
        # pull the unrelated top-level campaign runner into the kernel.
        symbols = symtable.symtable(ast.unparse(node), str(driver), "exec")
        tables = [symbols]
        while tables:
            table = tables.pop()
            pending.update(
                symbol.get_name()
                for symbol in table.get_symbols()
                if symbol.is_referenced()
                and (table.get_type() == "module" or symbol.is_global())
                and symbol.get_name() in definitions
                and symbol.get_name() not in selected
            )
            tables.extend(table.get_children())
    # params.json/execution.json use the shared atomic serializer. Hash that
    # function, not the unrelated provenance/release/recovery helpers beside it.
    support = ast.parse((root / "wrappers/benchmark_support.py").read_text())
    serializer = next(
        node
        for node in support.body
        if isinstance(node, ast.FunctionDef) and node.name == "atomic_json"
    )
    selected["benchmark_support.atomic_json"] = ast.dump(
        serializer, include_attributes=False
    )
    inputs = root / "wrappers/inference_tools/scripts/shared/benchmark_inputs.py"
    return {
        "input_generator": hashlib.sha256(inputs.read_bytes()).hexdigest(),
        "measurement_kernel": hashlib.sha256(
            json.dumps(selected, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def measurement_kernel_fingerprint(root: Path) -> str:
    """Identify the scientific inference input/timing implementation only."""
    return hashlib.sha256(
        json.dumps(
            _inference_kernel(root), sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def verify_measurement_source(
    root: Path, expected_source_sha256: str, source_files_sha256: dict[str, str]
) -> str:
    """Prove a retained source snapshot before deriving a legacy kernel identity.

    The global source digest is compared with the original observation's
    provenance, not with a manually allowed recipe hash. Nothing is imported or
    executed from the old source. This proof does not replace input/context and
    output integrity checks on each individual retained observation.
    """
    if not source_files_sha256 or not isinstance(expected_source_sha256, str):
        raise ValueError(
            "An original source digest and nonempty source manifest are required"
        )
    required = {
        "wrappers/benchmark_support.py",
        "wrappers/inference_tools/scripts/benchmark_costs.py",
        "wrappers/inference_tools/scripts/shared/benchmark_inputs.py",
    }
    if not required.issubset(source_files_sha256):
        raise ValueError(
            "Source proof does not include the complete inference measurement kernel"
        )
    root = root.resolve()
    source = hashlib.sha256()
    for name, expected in sorted(source_files_sha256.items()):
        relative = PurePosixPath(name)
        path = (root / name).resolve()
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not path.is_relative_to(root)
            or not name.startswith(("andrea/", "wrappers/", "scripts/"))
            or is_generated_cost_artifact(name)
        ):
            raise ValueError(f"Invalid scientific source manifest path: {name}")
        if not path.is_file():
            raise ValueError(f"Missing retained measurement source: {name}")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError(f"Retained measurement source changed: {name}")
        source.update(name.encode() + b"\0" + content + b"\0")
    if source.hexdigest() != expected_source_sha256:
        raise ValueError(
            "Retained source manifest does not match original measurement provenance"
        )
    return measurement_kernel_fingerprint(root)


def profile_fingerprint(profile: Any, *, root: Path, kind: str) -> str:
    """Fingerprint resolved scientific inputs and the executed measurement kernel.

    Runner/control-flow source remains provenance. It is not a cache key for
    inference observations. Simulator fingerprints retain their existing
    contract until that separate workflow is reviewed.
    """

    def canonical(value):
        if is_dataclass(value):
            return canonical(asdict(value))
        if isinstance(value, Path):
            if value.is_file():
                return {"file_sha256": hashlib.sha256(value.read_bytes()).hexdigest()}
            if value.is_dir():
                return {
                    p.relative_to(value).as_posix(): canonical(p)
                    for p in sorted(value.rglob("*"))
                    if p.is_file()
                }
            raise RuntimeError(f"Missing benchmark fixture: {value}")
        if isinstance(value, dict):
            return {key: canonical(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [canonical(item) for item in value]
        return value

    scripts = (
        root
        / "wrappers"
        / ("inference_tools" if kind == "inference" else "simulation_data_tools")
        / "scripts"
    )
    resolved = canonical(profile)
    recipes = (
        _inference_kernel(root)
        if kind == "inference"
        else {
            str((scripts / "benchmark_costs.py").relative_to(root)): hashlib.sha256(
                (scripts / "benchmark_costs.py").read_bytes()
            ).hexdigest()
        }
    )
    value = {"resolved_profile": resolved, "recipes": recipes}
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def runtime_point_errors(
    point: dict, *, repeats: int | None = None, require_attempts: bool = False
) -> list[str]:
    """Validate actual terminal outcomes without turning failures into timings."""
    integer = (
        lambda value: isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
    )
    positive = lambda value: (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )
    total, ok, failed = (
        point.get(key) for key in ("repeats_total", "repeats_ok", "repeats_failed")
    )
    breakdown = point.get("failure_breakdown", {})
    if (
        not all(integer(value) for value in (total, ok, failed))
        or total < 1
        or total != ok + failed
        or not isinstance(breakdown, dict)
        or set(breakdown) != {"oom", "timeout", "error"}
        or not all(integer(value) for value in breakdown.values())
        or sum(breakdown.values()) != failed
    ):
        return ["Terminal attempt counts and failure breakdown must agree."]
    status = (
        "ok"
        if failed == 0
        else (
            "partial"
            if ok
            else (
                "oom"
                if breakdown["oom"]
                else "timeout" if breakdown["timeout"] else "error"
            )
        )
    )
    errors = []
    if point.get("status") != status:
        errors.append(
            "Aggregated status contradicts the actual successful/failed attempts."
        )
    rate = point.get("ok_rate")
    if (
        not isinstance(rate, (int, float))
        or isinstance(rate, bool)
        or not math.isfinite(rate)
        or not math.isclose(rate, ok / total, rel_tol=0, abs_tol=1e-6)
    ):
        errors.append(
            "ok_rate must equal successful attempts divided by actual attempts."
        )
    if ok:
        if not all(positive(point.get(key)) for key in ("seconds_p50", "seconds_p90")):
            errors.append(
                "Successful observations require positive finite timing percentiles."
            )
        elif point["seconds_p90"] < point["seconds_p50"]:
            errors.append("Timing p90 cannot be smaller than the median.")
    elif any(point.get(key) is not None for key in ("seconds_p50", "seconds_p90")):
        errors.append("Timing percentiles must be null when no execution succeeded.")
    evidence = point.get("measurement_evidence", {})
    rows = evidence.get("observations") if isinstance(evidence, dict) else None
    if require_attempts and not isinstance(rows, list):
        errors.append(
            "Retained raw terminal observations are required for the attempt-completion gate."
        )
    if rows is not None:
        if not isinstance(rows, list) or len(rows) != total:
            errors.append(
                "Raw observation count must equal the actual terminal attempt count."
            )
        else:
            actual_ok = 0
            actual_failures = {"oom": 0, "timeout": 0, "error": 0}
            seen = set()
            seconds = []
            for row in rows:
                if (
                    not isinstance(row, dict)
                    or row.get("status") not in {"ok", "oom", "timeout", "error"}
                    or not positive(row.get("seconds"))
                ):
                    errors.append(
                        "Retained observations require a true terminal status and actual finite elapsed time."
                    )
                    continue
                for dimension in ("genes", "columns", "cells", "threads", "ram_gb"):
                    if (
                        dimension in point
                        and (dimension in row or require_attempts)
                        and row.get(dimension) != point[dimension]
                    ):
                        errors.append(f"Raw observation contradicts point {dimension}.")
                key = row.get("run_id") or row.get("task_id") or row.get("io_dir")
                if require_attempts and (not isinstance(key, str) or not key.strip()):
                    errors.append(
                        "Each terminal attempt needs its retained execution identity."
                    )
                if require_attempts and (
                    not integer(row.get("repeat")) or row["repeat"] < 1
                ):
                    errors.append(
                        "Each declared repetition slot must be a positive integer."
                    )
                if isinstance(key, str) and key in seen:
                    errors.append("A retained actual execution is duplicated.")
                if isinstance(key, str):
                    seen.add(key)
                if row["status"] == "ok":
                    actual_ok += 1
                    seconds.append(row["seconds"])
                else:
                    actual_failures[row["status"]] += 1
            if actual_ok != ok or actual_failures != breakdown:
                errors.append(
                    "Aggregated outcome counts contradict retained raw observations."
                )
            if seconds and all(
                positive(point.get(key)) for key in ("seconds_p50", "seconds_p90")
            ):
                values = sorted(seconds)
                for field, fraction in (("seconds_p50", 0.5), ("seconds_p90", 0.9)):
                    index = (len(values) - 1) * fraction
                    low, high = math.floor(index), math.ceil(index)
                    observed = values[low] + (values[high] - values[low]) * (
                        index - low
                    )
                    if not math.isclose(
                        point[field], observed, rel_tol=1e-9, abs_tol=1e-6
                    ):
                        errors.append(
                            f"{field} differs from the retained successful timings."
                        )
            if require_attempts and isinstance(repeats, int):
                slots = {
                    row.get("repeat")
                    for row in rows
                    if isinstance(row, dict)
                    and row.get("status") in {"ok", "oom", "timeout", "error"}
                    and integer(row.get("repeat"))
                    and row["repeat"] >= 1
                }
                if not set(range(1, repeats + 1)).issubset(slots):
                    errors.append(
                        "The declared repetition slots do not all have terminal observations."
                    )
    if require_attempts and (
        not isinstance(repeats, int)
        or isinstance(repeats, bool)
        or repeats < 3
        or total < repeats
    ):
        errors.append(
            "Every planned point requires its declared terminal attempts (minimum three); success is assessed separately."
        )
    return errors


def release_profile_errors(
    payload: dict,
    expected: dict[str, str],
    *,
    require_attempts: bool = True,
    require_successful: bool = True,
) -> list[str]:
    """Validate current profile identity and actual outcomes, including failures.

    The default retains the full-success release policy used by simulators and
    historical callers. Inference explicitly opts into outcome-aware validation:
    checkpoints may omit unattempted points, while a separate attempt gate
    requires all terminal slots. Numerical support remains a distinct check.
    """
    profiles = payload.get("profiles", [])
    observed = {
        profile.get("profile_id") for profile in profiles if isinstance(profile, dict)
    }
    errors = []
    if len(observed) != len(profiles):
        errors.append(
            "Duplicate or malformed benchmark profiles; regenerate the full tool/simulator cost.json."
        )
    missing = sorted(set(expected) - observed)
    obsolete = sorted(str(item) for item in observed - set(expected))
    if missing and require_attempts:
        errors.append(
            f"Missing current benchmark profiles: {', '.join(missing)}; regenerate these profiles."
        )
    if obsolete:
        errors.append(
            f"Obsolete benchmark profiles: {', '.join(obsolete)}; regenerate the full tool/simulator cost.json."
        )
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        label = profile.get("profile_id", "<unknown>")
        config = profile.get("benchmark_config", {})
        if not isinstance(config, dict):
            continue
        if label in expected and config.get("profile_sha256") != expected[label]:
            errors.append(
                f"{label}: benchmark profile/fixture recipe changed or was not recorded; regenerate this profile."
            )
        sizes = config.get("sizes", [])
        column = (
            "cells"
            if sizes and isinstance(sizes[0], dict) and "cells" in sizes[0]
            else "columns"
        )
        try:
            declared_points = config.get("planned_points")
            expected_points = (
                {
                    (p["genes"], p[column], p["threads"], p["ram_gb"])
                    for p in declared_points
                }
                if declared_points is not None
                else {
                    (size["genes"], size[column], thread, ram)
                    for size in sizes
                    for thread in config["threads_tested"]
                    for ram in config["ram_gb_tested"]
                }
            )
            if declared_points is not None and len(expected_points) != len(
                declared_points
            ):
                errors.append(f"{label}: duplicate planned points.")
            points = profile.get("runtime_points", [])
            actual_points = [
                (p["genes"], p[column], p["threads"], p["ram_gb"]) for p in points
            ]
        except (KeyError, TypeError):
            errors.append(
                f"{label}: malformed measurement matrix; regenerate this profile."
            )
            continue
        if (
            not expected_points
            or len(actual_points) != len(set(actual_points))
            or not set(actual_points).issubset(expected_points)
            or (require_attempts and set(actual_points) != expected_points)
        ):
            errors.append(
                f"{label}: runtime_points do not cover the declared matrix exactly; regenerate this profile."
            )
        repeats = config.get("repeats", 0)
        for point in points:
            errors.extend(
                f"{label}: {message}"
                for message in runtime_point_errors(
                    point,
                    repeats=repeats,
                    require_attempts=require_attempts and not require_successful,
                )
            )
            if require_successful and (
                point.get("status") != "ok"
                or point.get("repeats_total") != repeats
                or point.get("repeats_ok") != repeats
                or point.get("repeats_failed") != 0
                or point.get("ok_rate") != 1
                or any(point.get("failure_breakdown", {}).values())
                or not isinstance(repeats, int)
                or isinstance(repeats, bool)
                or repeats < 3
            ):
                errors.append(
                    f"{label}: every point requires all declared repetitions successful (minimum three); regenerate this profile."
                )
    return errors
