from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
VALIDATE_TOOLSPECS_SCRIPT = (
    REPO_ROOT / "wrappers" / "inference_tools" / "scripts" / "validate_toolspecs.py"
)


def _load_validate_toolspecs_module():
    spec = importlib.util.spec_from_file_location(
        "validate_toolspecs", VALIDATE_TOOLSPECS_SCRIPT
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _python_executable() -> str:
    return str(
        Path(".venv/bin/python") if Path(".venv/bin/python").exists() else "python"
    )


class ToolSpecCatalogTest(unittest.TestCase):
    def _minimal_toolspec(
        self,
        *,
        execution_capabilities: list[str],
        conditional_required: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        return {
            "id": "cell_tool",
            "execution_capabilities": execution_capabilities,
            "runtime_resources": {
                "threading": {
                    "supported": False,
                    "default_threads": 1,
                    "max_threads": 1,
                    "upstream_mapping": "No upstream parallel runtime control.",
                }
            },
            "taxonomic_scope": {
                "allowed_groups": ["animal"],
                "supported_species": [],
            },
            "params": {},
            "extra_inputs": {
                "required": [],
                "optional": [],
                "conditional_required": conditional_required or [],
            },
            "compatibility_rules": [],
        }

    def test_all_tool_specs_validate_with_wrapper_script(self) -> None:
        completed = subprocess.run(
            [
                _python_executable(),
                str(VALIDATE_TOOLSPECS_SCRIPT),
            ],
            cwd=REPO_ROOT,
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_runtime_resources_threading_is_required(self) -> None:
        module = _load_validate_toolspecs_module()
        instance = self._minimal_toolspec(execution_capabilities=["global"])
        instance.pop("runtime_resources")

        errors = module.semantic_errors_for_toolspec(
            tool_id="cell_tool",
            instance=instance,
        )

        self.assertTrue(
            any("runtime_resources.threading is required" in error for error in errors),
            errors,
        )

    def test_runtime_resources_supported_false_requires_one_thread(self) -> None:
        module = _load_validate_toolspecs_module()
        instance = self._minimal_toolspec(execution_capabilities=["global"])
        instance["runtime_resources"]["threading"]["max_threads"] = 2

        errors = module.semantic_errors_for_toolspec(
            tool_id="cell_tool",
            instance=instance,
        )

        self.assertTrue(
            any("supported=false" in error for error in errors),
            errors,
        )

    def test_runtime_resources_default_must_not_exceed_max(self) -> None:
        module = _load_validate_toolspecs_module()
        instance = self._minimal_toolspec(execution_capabilities=["global"])
        instance["runtime_resources"]["threading"] = {
            "supported": True,
            "default_threads": 4,
            "max_threads": 2,
            "upstream_mapping": "Wrapper maps --threads to upstream n_jobs.",
        }

        errors = module.semantic_errors_for_toolspec(
            tool_id="cell_tool",
            instance=instance,
        )

        self.assertTrue(
            any("default_threads must be <= max_threads" in error for error in errors),
            errors,
        )

    def test_runtime_resources_supported_tool_may_have_no_intrinsic_maximum(
        self,
    ) -> None:
        module = _load_validate_toolspecs_module()
        instance = self._minimal_toolspec(execution_capabilities=["global"])
        instance["runtime_resources"]["threading"] = {
            "supported": True,
            "default_threads": 1,
            "max_threads": None,
            "upstream_mapping": "Wrapper maps --threads to upstream workers.",
        }

        errors = module.semantic_errors_for_toolspec(
            tool_id="cell_tool",
            instance=instance,
        )

        self.assertFalse(
            any("runtime_resources.threading" in error for error in errors),
            errors,
        )

    def test_group_aggregated_requires_column_native_capability(self) -> None:
        module = _load_validate_toolspecs_module()
        instance = self._minimal_toolspec(
            execution_capabilities=["group_aggregated"],
            conditional_required=[
                {
                    "input": "groups",
                    "execution": "mode",
                    "op": "eq",
                    "value": "group_aggregated",
                    "usage": "Used to aggregate native per-column networks by group.",
                    "message": "groups is required when execution.mode=group_aggregated.",
                    "delivery": "orchestration_only",
                }
            ],
        )

        errors = module.semantic_errors_for_toolspec(
            tool_id="cell_tool",
            instance=instance,
        )

        self.assertTrue(
            any("must also declare 'column_native'" in error for error in errors),
            errors,
        )

    def test_group_aggregated_requires_groups_conditional_rule(self) -> None:
        module = _load_validate_toolspecs_module()
        instance = self._minimal_toolspec(
            execution_capabilities=["column_native", "group_aggregated"],
        )

        errors = module.semantic_errors_for_toolspec(
            tool_id="cell_tool",
            instance=instance,
        )

        self.assertTrue(
            any("execution.mode == 'group_aggregated'" in error for error in errors),
            errors,
        )

    def test_column_native_group_aggregated_contract_can_validate(self) -> None:
        module = _load_validate_toolspecs_module()
        instance = self._minimal_toolspec(
            execution_capabilities=["column_native", "group_aggregated"],
            conditional_required=[
                {
                    "input": "groups",
                    "execution": "mode",
                    "op": "eq",
                    "value": "group_aggregated",
                    "usage": "Used to aggregate native per-column networks by group.",
                    "message": "groups is required when execution.mode=group_aggregated.",
                    "delivery": "orchestration_only",
                }
            ],
        )

        errors = module.semantic_errors_for_toolspec(
            tool_id="cell_tool",
            instance=instance,
        )

        self.assertEqual(errors, [])

    def test_group_emulated_groups_must_be_orchestration_only(self) -> None:
        module = _load_validate_toolspecs_module()
        instance = self._minimal_toolspec(
            execution_capabilities=["global", "group_emulated"],
            conditional_required=[
                {
                    "input": "groups",
                    "execution": "mode",
                    "op": "eq",
                    "value": "group_emulated",
                    "usage": "Used by ANDREA to partition expression columns.",
                    "message": "groups is required for group emulation.",
                    "delivery": "runtime",
                }
            ],
        )

        errors = module.semantic_errors_for_toolspec(
            tool_id="cell_tool",
            instance=instance,
        )

        self.assertTrue(
            any(
                "execution.mode=group_emulated must use delivery=orchestration_only"
                in error
                for error in errors
            ),
            errors,
        )

    def test_orchestration_delivery_is_reserved_for_managed_groups(self) -> None:
        module = _load_validate_toolspecs_module()
        instance = self._minimal_toolspec(execution_capabilities=["global"])
        instance["extra_inputs"]["optional"] = [
            {
                "input": "tf_list",
                "usage": "Restricts candidate regulators.",
                "delivery": "orchestration_only",
            }
        ]

        errors = module.semantic_errors_for_toolspec(
            tool_id="cell_tool",
            instance=instance,
        )

        self.assertTrue(
            any("only for a conditional groups rule" in error for error in errors),
            errors,
        )

    def test_runtime_delivery_cannot_depend_on_logical_execution_mode(self) -> None:
        module = _load_validate_toolspecs_module()
        instance = self._minimal_toolspec(
            execution_capabilities=["global", "group_emulated"],
            conditional_required=[
                {
                    "input": "tf_list",
                    "execution": "mode",
                    "op": "eq",
                    "value": "group_emulated",
                    "usage": "Invalid logical-mode runtime rule.",
                    "message": "tf_list is required.",
                    "delivery": "runtime",
                }
            ],
        )

        errors = module.semantic_errors_for_toolspec(
            tool_id="cell_tool",
            instance=instance,
        )

        self.assertTrue(
            any(
                "delivery=runtime cannot depend on logical "
                "execution.mode=group_emulated" in error
                for error in errors
            ),
            errors,
        )

    def test_group_emulated_requires_global_capability(self) -> None:
        module = _load_validate_toolspecs_module()
        instance = self._minimal_toolspec(
            execution_capabilities=["group_emulated"],
            conditional_required=[
                {
                    "input": "groups",
                    "execution": "mode",
                    "op": "eq",
                    "value": "group_emulated",
                    "usage": "Used by ANDREA to partition expression columns.",
                    "message": "groups is required for group emulation.",
                    "delivery": "orchestration_only",
                }
            ],
        )

        errors = module.semantic_errors_for_toolspec(
            tool_id="cell_tool",
            instance=instance,
        )

        self.assertTrue(
            any("must also declare 'global'" in error for error in errors),
            errors,
        )

    def test_group_native_groups_must_be_runtime_delivered(self) -> None:
        module = _load_validate_toolspecs_module()
        instance = self._minimal_toolspec(execution_capabilities=["group_native"])
        instance["extra_inputs"]["required"] = [
            {
                "input": "groups",
                "usage": "Consumed by the native grouped method.",
                "delivery": "orchestration_only",
            }
        ]

        errors = module.semantic_errors_for_toolspec(
            tool_id="cell_tool",
            instance=instance,
        )

        self.assertTrue(
            any("group_native must use delivery=runtime" in error for error in errors),
            errors,
        )

    def test_group_native_requires_runtime_context_input(self) -> None:
        module = _load_validate_toolspecs_module()
        instance = self._minimal_toolspec(execution_capabilities=["group_native"])

        errors = module.semantic_errors_for_toolspec(
            tool_id="cell_tool",
            instance=instance,
        )

        self.assertTrue(
            any("exactly one runtime-delivered context source" in error for error in errors),
            errors,
        )

    def test_group_native_rejects_ambiguous_runtime_context_sources(self) -> None:
        module = _load_validate_toolspecs_module()
        instance = self._minimal_toolspec(execution_capabilities=["group_native"])
        instance["extra_inputs"]["required"] = [
            {
                "input": context_input,
                "usage": "Defines native output contexts.",
                "delivery": "runtime",
            }
            for context_input in ("groups", "column_phenotypes")
        ]

        errors = module.semantic_errors_for_toolspec(
            tool_id="cell_tool",
            instance=instance,
        )

        self.assertTrue(
            any("exactly one runtime-delivered context source" in error for error in errors),
            errors,
        )
