"""Scenario-to-simulator capability evaluation for generate-data."""

from __future__ import annotations

from typing import Any

from andrea.core.shared.container_runtime import ensure_docker_cli
from andrea.core.shared.issues import make_issue

from .catalog import _load_simulator_catalog, get_profile_capability
from .request import (
    _resolve_simulator_params,
    _supported_requested_artifacts,
    collect_simulator_compatibility_rule_issues,
    simulator_input_warnings,
    validate_truth_parameter_requirements,
    validate_simulator_inputs,
)
from .scenario import validate_scenario_request
from .shared import (
    ResolvedScenarioRequest,
    _validate_json_instance,
    primary_truth_output_for_profile,
    required_truth_context_prefixes_for_profile,
    required_truth_outputs_for_profile,
)


def _evaluate_runtime_requirements(_simulator_id: str) -> list[str]:
    try:
        ensure_docker_cli(check_daemon=True)
    except RuntimeError as exc:
        return [str(exc)]
    return []


def evaluate_simulator_for_scenario(
    *,
    simulator_id: str,
    spec: dict[str, Any],
    scenario: ResolvedScenarioRequest,
) -> dict[str, Any]:
    profile_capability = get_profile_capability(spec, scenario.profile)
    issues: list[dict[str, Any]] = []

    if profile_capability is None:
        issues.append(
            make_issue(
                severity="block",
                code="unsupported_profile",
                message=f"profile '{scenario.profile}' is not supported",
                simulator_id=simulator_id,
            )
        )
        native = set()
        derivable = set()
        truth_outputs = {
            "global": "none",
            "group": "none",
            "cell": "none",
        }
        supported_effective_extras: list[str] = []
    else:
        resolved_params = _resolve_simulator_params(
            simulator_id=simulator_id,
            user_params={},
            spec_params=spec.get("params", {}),
        )
        native, derivable = _supported_requested_artifacts(profile_capability)
        truth_outputs = dict(profile_capability.get("truth_outputs", {}))
        missing_truth = [
            output_id
            for output_id in required_truth_outputs_for_profile(scenario.profile)
            if truth_outputs.get(output_id) not in {"native", "derivable"}
        ]
        if missing_truth:
            required_outputs = list(required_truth_outputs_for_profile(scenario.profile))
            required_contexts = list(
                required_truth_context_prefixes_for_profile(scenario.profile)
            )
            missing_contexts = [
                required_contexts[required_outputs.index(output_id)]
                for output_id in missing_truth
                if output_id in required_outputs
            ]
            issues.append(
                make_issue(
                    severity="block",
                    code="unsupported_truth_outputs",
                    message="profile requires truth context(s) not supported by this simulator: "
                    + ", ".join(missing_contexts or missing_truth),
                    simulator_id=simulator_id,
                )
            )
        primary_truth_output = primary_truth_output_for_profile(scenario.profile)
        primary_truth_status = truth_outputs.get(primary_truth_output)
        if primary_truth_status == "derivable":
            primary_context = {
                "global": "global",
                "group": "group:",
                "cell": "cell:",
            }.get(primary_truth_output, primary_truth_output)
            issues.append(
                make_issue(
                    severity="warn",
                    code="primary_truth_context_derived",
                    message=(
                        f"profile '{scenario.profile}' uses canonical truth context "
                        f"'{primary_context}', which this simulator derives rather than "
                        "produces natively"
                    ),
                    simulator_id=simulator_id,
                    context=primary_context,
                )
            )
        truth_parameter_errors = validate_truth_parameter_requirements(
            profile_capability=profile_capability,
            profile=scenario.profile,
            requested_extras=scenario.requested_extras,
            simulator_params=resolved_params,
        )
        issues.extend(
            make_issue(
                severity="block",
                code="invalid_truth_output_parameters",
                message=message,
                simulator_id=simulator_id,
            )
            for message in truth_parameter_errors
        )
        compatibility_blocks, compatibility_warnings, compatibility_errors = (
            collect_simulator_compatibility_rule_issues(
                simulator_id=simulator_id,
                simulator_spec=spec,
                profile=scenario.profile,
                requested_extras=scenario.requested_extras,
                simulator_params=resolved_params,
                native_outputs=[],
                resolved_input_paths=scenario.resolved_input_paths,
            )
        )
        issues.extend(
            make_issue(
                severity="block",
                code="compatibility_rule",
                message=message,
                simulator_id=simulator_id,
            )
            for message in compatibility_blocks
        )
        issues.extend(
            make_issue(
                severity="warn",
                code="compatibility_rule",
                message=message,
                simulator_id=simulator_id,
            )
            for message in compatibility_warnings
        )
        issues.extend(
            make_issue(
                severity="block",
                code="invalid_compatibility_rule",
                message=f"invalid compatibility rule: {message}",
                simulator_id=simulator_id,
            )
            for message in compatibility_errors
        )
        supported_effective_extras = sorted(
            set(scenario.effective_extras).intersection(native.union(derivable))
        )
        unsupported_requested = sorted(
            set(scenario.effective_extras).difference(native.union(derivable))
        )
        if unsupported_requested:
            issues.append(
                make_issue(
                    severity="block",
                    code="unsupported_extras",
                    message="unsupported extras for this profile: "
                    + ", ".join(unsupported_requested),
                    simulator_id=simulator_id,
                )
            )
        issues.extend(
            make_issue(
                severity="block",
                code="invalid_inputs",
                message=message,
                simulator_id=simulator_id,
            )
            for message in validate_simulator_inputs(
                simulator_id=simulator_id,
                simulator_spec=spec,
                profile=scenario.profile,
                requested_extras=scenario.requested_extras,
                simulator_params=resolved_params,
                native_outputs=[],
                input_ids=set(scenario.inputs),
            )
        )
        issues.extend(
            make_issue(
                severity="warn",
                code="optional_input_missing",
                message=message,
                simulator_id=simulator_id,
            )
            for message in simulator_input_warnings(
                simulator_spec=spec,
                input_ids=set(scenario.inputs),
            )
        )
        issues.extend(
            make_issue(
                severity="block",
                code="runtime_unavailable",
                message=message,
                simulator_id=simulator_id,
            )
            for message in _evaluate_runtime_requirements(simulator_id)
        )

    if any(issue["severity"] == "block" for issue in issues):
        status = "blocked"
    elif any(issue["severity"] == "warn" for issue in issues):
        status = "warning"
    else:
        status = "eligible"

    native_used = sorted(set(supported_effective_extras).intersection(native))
    derived_used = sorted(set(supported_effective_extras).intersection(derivable))
    return {
        "simulator_id": simulator_id,
        "name": spec["name"],
        "requested_profile": scenario.profile,
        "requested_extras": list(scenario.requested_extras),
        "effective_extras": list(scenario.effective_extras),
        "inputs_used": sorted(scenario.inputs),
        "native_extras_used": native_used,
        "derived_extras_used": derived_used,
        "truth_outputs": truth_outputs,
        "status": status,
        "issues": issues,
    }


def preflight_generate_data_scenario(scenario_path: Path) -> dict[str, Any]:
    scenario = validate_scenario_request(scenario_path)
    schemas, catalog = _load_simulator_catalog()

    entries = [
        evaluate_simulator_for_scenario(
            simulator_id=simulator_id,
            spec=spec,
            scenario=scenario,
        )
        for simulator_id, spec in sorted(catalog.items())
    ]
    eligible = [entry for entry in entries if entry["status"] == "eligible"]
    warning = [entry for entry in entries if entry["status"] == "warning"]
    blocked = [entry for entry in entries if entry["status"] == "blocked"]

    report = {
        "schema_version": "1.0",
        "scenario": {
            "id": scenario.request_id,
            "profile": scenario.profile,
            "organism": scenario.organism,
            "requested_extras": scenario.requested_extras,
            "effective_extras": scenario.effective_extras,
            "inputs": scenario.inputs,
            "base_seed": scenario.base_seed,
        },
        "catalog_summary": {
            "total": len(entries),
            "eligible": len(eligible),
            "warning": len(warning),
            "blocked": len(blocked),
        },
        "eligible": eligible,
        "warning": warning,
        "blocked": blocked,
    }
    _validate_json_instance(
        instance=report,
        schema=schemas["preflight_report"],
        label=f"preflight-report[{scenario.request_id}]",
    )
    return report
