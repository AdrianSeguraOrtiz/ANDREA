"""Check an integration dossier and hashed receipts without running experiments.

This is an evidence gate, not an automatic judgement of scientific correctness.
Paths are relative to the dossier. Keep evidence caches and raw runs unversioned.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

FUNCTIONAL = {
    "scientific_audit",
    "catalog_contract",
    "functional_modes",
    "conditional_inputs",
    "scientific_outputs",
    "identifiers",
    "parameter_defaults",
    "repeatability",
    "resources",
    "core",
    "cli",
    "gui",
    "catalog_maps",
}
CALIBRATED = FUNCTIONAL | {"cost_structure", "cost_domain", "cost_predictions"}
STAGES = {
    "functional": FUNCTIONAL,
    "calibrated": CALIBRATED,
    "published": FUNCTIONAL | {"registry_image"},
}
CALIBRATION_ROLES = {
    "cost",
    "calibration_plan",
    "prediction_plan",
    "prediction_report",
    "estimator",
}
PENDING = {"", "pending", "todo", "tbd", "unknown"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def meaningful(value) -> bool:
    return isinstance(value, str) and value.strip().lower() not in PENDING


def context_sha256(data: dict) -> str:
    """Seal scientific claims as well as files; registry publication is separate."""
    context = {
        key: data.get(key)
        for key in (
            "schema_version",
            "kind",
            "id",
            "evidence",
            "entrypoints",
            "parameters",
            "limitations",
        )
    }
    context["installation"] = {
        key: value
        for key, value in data.get("installation", {}).items()
        if key != "registry_digest"
    }
    return hashlib.sha256(
        json.dumps(
            context, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def subject_sets(data: dict) -> dict:
    subjects = {ref["role"]: ref["sha256"] for ref in data.get("artifacts", [])}
    return {
        "functional_subjects": {
            key: value
            for key, value in subjects.items()
            if key not in CALIBRATION_ROLES
        },
        "calibration_subjects": subjects,
    }


def referenced_files(value, base: Path):
    if isinstance(value, dict):
        if isinstance(value.get("path"), str) and "sha256" in value:
            yield (base / value["path"]).resolve()
        for child in value.values():
            yield from referenced_files(child, base)
    elif isinstance(value, list):
        for child in value:
            yield from referenced_files(child, base)


def validate(path: Path, stage: str = "functional") -> dict:
    errors = []
    data = json.loads(path.read_text())
    base = path.resolve().parent

    def require(condition, message):
        if not condition:
            errors.append(message)

    def file_ref(ref, label):
        if not isinstance(ref, dict) or not meaningful(ref.get("path")):
            errors.append(f"{label}: missing file reference")
            return None
        target = base / ref["path"]
        if not target.is_file():
            errors.append(f"{label}: missing file {ref['path']}")
            return None
        if ref.get("sha256") != sha256(target):
            errors.append(f"{label}: SHA-256 mismatch {ref['path']}")
            return None
        return target

    require(data.get("schema_version") == "1.0", "schema_version must be 1.0")
    require(data.get("kind") in {"inference", "simulation"}, "invalid kind")
    require(meaningful(data.get("id")), "id is pending")
    artifacts = data.get("artifacts", [])
    require(
        isinstance(artifacts, list) and bool(artifacts),
        "artifacts must include spec, wrapper and Dockerfile",
    )
    subjects = {}
    spec = None
    roles = set()
    for ref in artifacts:
        target = file_ref(ref, "artifact")
        role = ref.get("role")
        require(meaningful(role), "artifact role missing")
        require(role not in roles, f"duplicate artifact role: {role}")
        roles.add(role)
        subjects[role] = ref.get("sha256")
        if role == "spec" and target:
            spec = json.loads(target.read_text())
    require(
        {"spec", "wrapper", "dockerfile"} <= roles,
        "missing spec/wrapper/dockerfile artifact",
    )
    if stage == "calibrated":
        require(
            CALIBRATION_ROLES <= roles,
            "calibrated stage requires cost/calibration_plan/prediction_plan/prediction_report/estimator artifacts",
        )
        cost_refs = [ref for ref in artifacts if ref.get("role") == "cost"]
        require(
            bool(cost_refs) and Path(cost_refs[0]["path"]).name == "cost.json",
            "calibration cost artifact must be named cost.json",
        )
    if spec:
        require(spec.get("id") == data.get("id"), "spec id differs from dossier")

    evidence = data.get("evidence", [])
    evidence_ids = set()
    evidence_roles = set()
    for ref in evidence:
        require(
            meaningful(ref.get("id")) and ref.get("id") not in evidence_ids,
            "missing/duplicate evidence id",
        )
        evidence_ids.add(ref.get("id"))
        evidence_roles.add(ref.get("role"))
        file_ref(ref, "evidence")
        require(
            meaningful(ref.get("citation")), "evidence needs citation/source location"
        )
        require(
            meaningful(ref.get("limitations")),
            "evidence needs explicit limitations (or 'none identified')",
        )
    require(
        {"paper", "upstream"} <= evidence_roles,
        "paper TXT and upstream evidence are required",
    )

    def references(row, label):
        refs = row.get("evidence", [])
        require(
            isinstance(refs, list) and bool(refs) and set(refs) <= evidence_ids,
            f"{label}: missing or unknown evidence ids",
        )

    install = data.get("installation", {})
    require(meaningful(install.get("preference")), "installation preference missing")
    require(
        bool(re.match(r"https://[^/]+/.+", install.get("public_source", ""))),
        "installation requires a public HTTPS source",
    )
    require(
        meaningful(install.get("pin")),
        "installation requires a verified version/commit and dependency lock",
    )
    require(
        bool(
            re.fullmatch(r"(?:[^\s]+@)?sha256:[0-9a-f]{64}", install.get("image", ""))
        ),
        "immutable image ID/digest required",
    )
    if stage == "published":
        require(
            bool(
                re.fullmatch(
                    r"[^\s]+@sha256:[0-9a-f]{64}", install.get("registry_digest", "")
                )
            ),
            "published stage requires a registry digest",
        )
    references(install, "installation")

    entrypoints = data.get("entrypoints", [])
    require(bool(entrypoints), "entrypoint audit missing")
    included = set()
    for row in entrypoints:
        require(meaningful(row.get("name")), "entrypoint name missing")
        references(row, "entrypoint")
        require(
            row.get("disposition") in {"included", "excluded"},
            "entrypoint disposition invalid",
        )
        require(meaningful(row.get("reason")), "entrypoint rationale missing")
        if row.get("disposition") == "included":
            require(
                meaningful(row.get("mapping")), "included entrypoint mapping missing"
            )
            included.add(row.get("name"))
    require(bool(included), "no implemented entrypoint")

    public = set(spec.get("params", {})) if spec else set()
    exposed = set()
    for row in data.get("parameters", []):
        for key in (
            "upstream",
            "type",
            "domain",
            "dependencies",
            "argument",
            "reason",
            "test",
            "cost",
            "default_rule",
        ):
            require(
                meaningful(row.get(key)),
                f"parameter {row.get('upstream')}: missing {key}",
            )
        require("default" in row, "parameter default missing")
        require("upstream_default" in row, "parameter upstream_default missing")
        if isinstance(row.get("upstream_default"), str):
            require(
                meaningful(row["upstream_default"]),
                "parameter upstream_default is pending",
            )
        references(row, "parameter")
        disposition = row.get("disposition")
        require(
            disposition in {"exposed", "excluded", "resource"},
            "parameter disposition invalid",
        )
        if disposition == "exposed":
            name = row.get("andrea")
            require(
                name in public and name not in exposed,
                f"unknown/duplicate ANDREA parameter {name}",
            )
            exposed.add(name)
            if spec and name in public:
                require(
                    json.dumps(row.get("default"), sort_keys=True, allow_nan=False)
                    == json.dumps(
                        spec["params"][name].get("default"),
                        sort_keys=True,
                        allow_nan=False,
                    ),
                    f"default mismatch for {name}",
                )
    require(
        exposed == public,
        f"public params missing from traceability matrix: {sorted(public - exposed)}",
    )

    checks = data.get("checks", [])
    by_name = {}
    for check in checks:
        name = check.get("name")
        require(name not in by_name, f"duplicate check {name}")
        by_name[name] = check
    for name in sorted(STAGES[stage]):
        check = by_name.get(name, {})
        if name == "functional_modes":
            require(
                set(check.get("covers", [])) >= included,
                "functional_modes: missing included entrypoint coverage",
            )
        if name == "parameter_defaults":
            require(
                set(check.get("covers", [])) >= public,
                "parameter_defaults: missing public parameter coverage",
            )
        if check.get("status") == "not_applicable":
            require(
                name in {"conditional_inputs", "repeatability"},
                f"{name} cannot be waived",
            )
            require(
                meaningful(check.get("reason")), f"{name}: waiver rationale missing"
            )
            references(check, name)
            continue
        require(check.get("status") == "passed", f"{name}: pending or failed")
        target = file_ref(check.get("receipt"), name)
        if target:
            receipt = json.loads(target.read_text())
            require(
                receipt.get("status") == "passed" and receipt.get("exit_code") == 0,
                f"{name}: receipt failed",
            )
            require(
                receipt.get("kind") == data.get("kind")
                and receipt.get("id") == data.get("id"),
                f"{name}: receipt identity differs",
            )
            expected_subjects = subject_sets(data)[
                (
                    "calibration_subjects"
                    if name.startswith("cost_")
                    else "functional_subjects"
                )
            ]
            require(
                receipt.get("subjects") == expected_subjects,
                f"{name}: stale artifact subjects",
            )
            require(
                receipt.get("context_sha256") == context_sha256(data),
                f"{name}: stale scientific context",
            )
            require(
                receipt.get("check") == name
                and receipt.get("covers", []) == check.get("covers", []),
                f"{name}: receipt check/coverage differs",
            )
            require(
                receipt.get("image") == install.get("image"), f"{name}: image differs"
            )
            if name == "registry_image":
                require(
                    receipt.get("registry_digest") == install.get("registry_digest"),
                    "registry_image: registry digest differs",
                )
            require(
                meaningful(receipt.get("command")),
                f"{name}: command/review procedure missing",
            )
            outputs = receipt.get("outputs", [])
            require(bool(outputs), f"{name}: retained output evidence missing")
            for output in outputs:
                file_ref(output, f"{name} output")

    return {
        "kind": data.get("kind"),
        "id": data.get("id"),
        "stage": stage,
        "status": "failed" if errors else "passed",
        "errors": errors,
        "scope": "Evidence integrity and required gates only; scientific adequacy and experimental independence require the recorded review.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dossier", type=Path)
    parser.add_argument("--stage", choices=STAGES, default="functional")
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--context-only",
        action="store_true",
        help="Print context/subject hashes for receipts; does not certify any gate.",
    )
    args = parser.parse_args()
    if args.report and args.report.resolve() == args.dossier.resolve():
        parser.error("Report must not overwrite the dossier or its evidence")
    try:
        data = json.loads(args.dossier.read_text())
        protected = {
            args.dossier.resolve(),
            *referenced_files(data, args.dossier.resolve().parent),
        }
        if args.report and args.report.resolve() in protected:
            parser.error("Report must not overwrite the dossier or its evidence")
        for check in data.get("checks", []):
            receipt = check.get("receipt", {})
            if "path" in receipt:
                target = args.dossier.resolve().parent / receipt["path"]
                if target.is_file():
                    try:
                        protected.update(
                            referenced_files(
                                json.loads(target.read_text()),
                                args.dossier.resolve().parent,
                            )
                        )
                    except (OSError, ValueError):
                        pass  # Validation below reports invalid receipts.
        if args.report and args.report.resolve() in protected:
            parser.error("Report must not overwrite the dossier or its evidence")
        if args.context_only:
            print(
                json.dumps(
                    {"context_sha256": context_sha256(data), **subject_sets(data)},
                    indent=2,
                )
            )
            return 0
        result = validate(args.dossier, args.stage)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        result = {"status": "failed", "errors": [str(exc)]}
    rendered = json.dumps(result, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered)
    print(rendered, end="")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
