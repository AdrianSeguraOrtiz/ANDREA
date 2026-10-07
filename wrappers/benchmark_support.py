"""Shared evidence and persistence helpers for maintenance benchmarks."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import platform
import shlex
import stat
import subprocess
import sys
import tempfile
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
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


def collect_provenance(
    *, root: Path, spec_path: Path, image: str, seed: int, source_dir: Path | None = None,
) -> dict:
    """Resolve the image once; callers run its immutable ID throughout a campaign."""
    def output(command: list[str]) -> str:
        return subprocess.check_output(command, cwd=root, text=True).strip()

    inspected = json.loads(output(["docker", "image", "inspect", image]))[0]
    cpu_model = platform.processor() or platform.machine()
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        cpu_model = next(
            (line.split(":", 1)[1].strip() for line in cpuinfo.read_text().splitlines()
             if line.startswith("model name")), cpu_model
        )
    memory = None
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        memory = next((int(line.split()[1]) * 1024 for line in meminfo.read_text().splitlines()
                       if line.startswith("MemTotal:")), None)
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root
    ).split(b"\0")
    source = hashlib.sha256()
    # Exclude generated measurements and artwork: regenerating another tool's
    # costs must not change the source identity of this benchmark.
    for raw in sorted(set(tracked)):
        if not raw:
            continue
        name = os.fsdecode(raw)
        if not name.startswith(("andrea/", "wrappers/", "scripts/")) or name.endswith("/cost.json"):
            continue
        path = root / name
        if path.is_file():
            source.update(raw + b"\0" + path.read_bytes() + b"\0")
    return {
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": output(["git", "rev-parse", "HEAD"]),
        "git_dirty": bool(output(["git", "status", "--porcelain"])),
        "source_sha256": source.hexdigest(),
        "spec_sha256": hashlib.sha256(spec_path.read_bytes()).hexdigest(),
        "build_context_sha256": build_context_fingerprint(
            root=root, identifier=spec_path.parent.name,
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
        "docker_server": json.loads(output(["docker", "info", "--format", "{{json .}}"])).get("ServerVersion"),
        "seed": seed,
        "timing_scope": "docker run wall time including container startup and output writing",
    }


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
    replacements = {profile["profile_id"]: profile for profile in measured_payload["profiles"]}
    profiles = [replacements.pop(profile["profile_id"], profile) for profile in existing["profiles"]]
    profiles.extend(replacements.values())
    return {**measured_payload, "profiles": profiles}


def provenance_errors(
    payload: dict, spec_path: Path, *, required: bool = False,
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
                errors.append(f"{label}: missing benchmark provenance; regenerate this profile.")
            continue
        if provenance.get("spec_sha256") != spec_hash:
            errors.append(f"{label}: spec changed since calibration; regenerate this profile.")
        if required and provenance.get("image_reference") != spec.get("docker_image"):
            errors.append(f"{label}: release calibration must use --catalog-images.")
        if required and config.get("repeats", 0) < 3:
            errors.append(f"{label}: release calibration requires at least three timing repetitions.")
        if required and build_context_sha256 is not None and provenance.get("build_context_sha256") != build_context_sha256:
            errors.append(f"{label}: container build inputs changed or were not recorded; rebuild the image and regenerate this profile.")
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
    *, root: Path, kind: str, identifier: str, source_dir: Path | None = None,
) -> str:
    """Hash the Dockerfile and local COPY inputs, including injected templates.

    We deliberately exclude package metadata, other integrations and generated
    costs. Unsupported COPY syntax fails closed instead of issuing a misleading
    fingerprint. Image IDs separately identify downloaded/compiled dependencies.
    """
    virtual: dict[str, tuple[bytes, int]] = {}
    if kind == "inference":
        scripts = root / "wrappers/inference_tools/scripts"
        builder = load_script(scripts / "build_tool_images.py", "andrea_cost_build_tools")
        mapping = json.loads((scripts / "template_map.json").read_text())
        config = builder.validate_template_map(mapping, {key: Path(key) for key in mapping["tools"]})[identifier]
        virtual["run_tool.sh"] = (builder.render_entrypoint(config.runtime).encode(), 0o755)
        for relative in config.templates:
            path = scripts / "templates" / relative
            virtual[path.name] = (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
        context = source_dir or root / "wrappers/inference_tools/tools" / identifier
        dockerfile = context / "Dockerfile"
    elif kind == "simulation":
        context = root
        dockerfile = (source_dir or root / "wrappers/simulation_data_tools/simulators" / identifier) / "Dockerfile"
    else:
        raise RuntimeError(f"Unknown benchmark kind: {kind}")

    files: dict[str, tuple[bytes, int]] = {
        "Dockerfile": (dockerfile.read_bytes(), stat.S_IMODE(dockerfile.stat().st_mode)),
    }
    for ignore in (context / ".dockerignore", Path(str(dockerfile) + ".dockerignore")):
        if ignore.is_file():
            files["ignore:" + ignore.name] = (ignore.read_bytes(), stat.S_IMODE(ignore.stat().st_mode))
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
                raise RuntimeError(f"Cannot fingerprint COPY/ADD flag {flag!r} in {dockerfile}")
            flags.append(flag)
            arguments = arguments.lstrip()
        if any(flag.startswith("--from=") for flag in flags):
            continue
        tokens = json.loads(arguments) if arguments.startswith("[") else shlex.split(arguments)
        if len(tokens) < 2:
            raise RuntimeError(f"Cannot fingerprint malformed COPY/ADD in {dockerfile}")
        for token in tokens[:-1]:
            if not isinstance(token, str) or any(char in token for char in "$*?[") or "://" in token:
                raise RuntimeError(f"Cannot fingerprint dynamic/remote COPY/ADD input {token!r} in {dockerfile}")
            relative = Path(token)
            if relative.is_absolute() or ".." in relative.parts:
                raise RuntimeError(f"COPY/ADD input escapes build context: {token!r}")
            key = relative.as_posix()
            if key in virtual:
                files[key] = virtual[key]
            else:
                path = context / relative
                if not path.is_file():
                    raise RuntimeError(f"Cannot fingerprint COPY/ADD input {path}; expected a local file.")
                files[key] = (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
    digest = hashlib.sha256()
    for name, (data, mode) in sorted(files.items()):
        digest.update(name.encode() + b"\0" + str(mode).encode() + b"\0" + data + b"\0")
    return digest.hexdigest()


def profile_fingerprint(profile: Any, *, root: Path, kind: str) -> str:
    """Fingerprint a resolved profile and its fixture/timing implementation."""
    def canonical(value):
        if is_dataclass(value):
            return canonical(asdict(value))
        if isinstance(value, Path):
            if value.is_file():
                return {"file_sha256": hashlib.sha256(value.read_bytes()).hexdigest()}
            if value.is_dir():
                return {p.relative_to(value).as_posix(): canonical(p) for p in sorted(value.rglob("*")) if p.is_file()}
            raise RuntimeError(f"Missing benchmark fixture: {value}")
        if isinstance(value, dict):
            return {key: canonical(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [canonical(item) for item in value]
        return value

    scripts = root / "wrappers" / ("inference_tools" if kind == "inference" else "simulation_data_tools") / "scripts"
    recipe_paths = [scripts / "benchmark_costs.py"]
    if kind == "inference":
        recipe_paths.append(scripts / "shared/benchmark_inputs.py")
    value = {
        "resolved_profile": canonical(profile),
        "recipes": {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in recipe_paths},
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def release_profile_errors(payload: dict, expected: dict[str, str]) -> list[str]:
    """Require every current profile and complete, successful timing matrices."""
    profiles = payload.get("profiles", [])
    observed = {profile.get("profile_id") for profile in profiles if isinstance(profile, dict)}
    errors = []
    if len(observed) != len(profiles):
        errors.append("Duplicate or malformed benchmark profiles; regenerate the full tool/simulator cost.json.")
    missing = sorted(set(expected) - observed)
    obsolete = sorted(str(item) for item in observed - set(expected))
    if missing:
        errors.append(f"Missing current benchmark profiles: {', '.join(missing)}; regenerate these profiles.")
    if obsolete:
        errors.append(f"Obsolete benchmark profiles: {', '.join(obsolete)}; regenerate the full tool/simulator cost.json.")
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        label = profile.get("profile_id", "<unknown>")
        config = profile.get("benchmark_config", {})
        if not isinstance(config, dict):
            continue
        if label in expected and config.get("profile_sha256") != expected[label]:
            errors.append(f"{label}: benchmark profile/fixture recipe changed or was not recorded; regenerate this profile.")
        sizes = config.get("sizes", [])
        column = "cells" if sizes and isinstance(sizes[0], dict) and "cells" in sizes[0] else "columns"
        try:
            expected_points = {
                (size["genes"], size[column], thread, ram)
                for size in sizes for thread in config["threads_tested"] for ram in config["ram_gb_tested"]
            }
            points = profile.get("runtime_points", [])
            actual_points = [(p["genes"], p[column], p["threads"], p["ram_gb"]) for p in points]
        except (KeyError, TypeError):
            errors.append(f"{label}: malformed measurement matrix; regenerate this profile.")
            continue
        if not expected_points or set(actual_points) != expected_points or len(actual_points) != len(expected_points):
            errors.append(f"{label}: runtime_points do not cover the declared matrix exactly; regenerate this profile.")
        repeats = config.get("repeats", 0)
        for point in points:
            if (point.get("status") != "ok" or point.get("repeats_total") != repeats
                    or point.get("repeats_ok") != repeats or point.get("repeats_failed") != 0
                    or point.get("ok_rate") != 1 or any(point.get("failure_breakdown", {}).values())
                    or not isinstance(repeats, int) or repeats < 3):
                errors.append(f"{label}: every point requires all declared repetitions successful (minimum three); regenerate this profile.")
                break
    return errors
