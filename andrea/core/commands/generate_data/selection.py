"""Scenario-to-simulator capability evaluation for generate-data."""

from __future__ import annotations

from typing import Any

from andrea.core.shared.container_runtime import ensure_docker_cli
from andrea.core.shared.issues import make_issue

from .catalog import _load_simulator_catalog, get_semantic_capability
from .request import (
    _resolve_simulator_params,
    _supported_requested_artifacts,
    collect_simulator_compatibility_rule_issues,
    simulator_input_warnings,
    validate_truth_parameter_requirements,
    validate_simulator_inputs,
)
from .scenario import validate_scenario_request
from .semantic import (
    SIMULATOR_TRUTH_CONTEXT_FAMILIES,
    context_prefix_for_family,
    parse_truth_requirements,
    primary_truth_context_family,
    truth_output_statuses,
)
from .shared import (
    ResolvedScenarioRequest,
    _validate_json_instance,
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
    capability = get_semantic_capability(
        spec,
        data_axes=scenario.data_axes,
        truth_requirements=scenario.truth_requirements,
    )
    issues: list[dict[str, Any]] = []

    if capability is None:
        issues.append(
            make_issue(
                severity="block",
                code="unsupported_semantic_capability",
                message="requested data_axes/truth_requirements are not supported",
                simulator_id=simulator_id,
            )
        )
        native = set()
        derivable = set()
        truth_outputs: dict[str, str] = {}
        supported_effective_extras: list[str] = []
    else:
        resolved_params = _resolve_simulator_params(
            simulator_id=simulator_id,
            user_params={},
            spec_params=spec.get("params", {}),
            capability=capability,
        )
        requested_truth = parse_truth_requirements(scenario.truth_requirements)
        native, derivable = _supported_requested_artifacts(capability)
        truth_outputs = truth_output_statuses(capability)
        missing_truth = [
            context
            for context in requested_truth.contexts
            if truth_outputs.get(context) not in {"native", "derivable"}
        ]
        if missing_truth:
            missing_contexts = [
                context_prefix_for_family(context) for context in missing_truth
            ]
            issues.append(
                make_issue(
                    severity="block",
                    code="unsupported_truth_outputs",
                    message="requested truth context(s) are not supported by this simulator: "
                    + ", ".join(missing_contexts),
                    simulator_id=simulator_id,
                )
            )
        primary_truth_output = primary_truth_context_family(requested_truth)
        primary_truth_status = truth_outputs.get(primary_truth_output)
        if primary_truth_status == "derivable":
            primary_context = context_prefix_for_family(primary_truth_output)
            issues.append(
                make_issue(
                    severity="warn",
                    code="primary_truth_context_derived",
                    message=(
                        f"requested canonical truth context '{primary_context}' "
                        "is derived by this simulator rather than "
                        "produces natively"
                    ),
                    simulator_id=simulator_id,
                    context=primary_context,
                )
            )
        truth_parameter_errors = validate_truth_parameter_requirements(
            capability=capability,
            data_axes=scenario.data_axes,
            truth_requirements=scenario.truth_requirements,
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
                data_axes=scenario.data_axes,
                truth_requirements=scenario.truth_requirements,
                requested_extras=scenario.requested_extras,
                simulator_params=resolved_params,
                native_outputs=[],
                resolved_input_paths=scenario.resolved_input_paths,
                scope="scenario",
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
                    message="unsupported extras for this semantic capability: "
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
                data_axes=scenario.data_axes,
                truth_requirements=scenario.truth_requirements,
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
    truth_output_rows = [
        {"context": context, "status": truth_outputs[context]}
        for context in SIMULATOR_TRUTH_CONTEXT_FAMILIES
        if truth_outputs.get(context) in {"native", "derivable"}
    ]
    return {
        "simulator_id": simulator_id,
        "name": spec["name"],
        "requested_data_axes": dict(scenario.data_axes),
        "requested_truth_requirements": dict(scenario.truth_requirements),
        "requested_extras": list(scenario.requested_extras),
        "effective_extras": list(scenario.effective_extras),
        "inputs_used": sorted(scenario.inputs),
        "native_extras_used": native_used,
        "derived_extras_used": derived_used,
        "truth_outputs": truth_output_rows,
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
            "data_axes": scenario.data_axes,
            "truth_requirements": scenario.truth_requirements,
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
