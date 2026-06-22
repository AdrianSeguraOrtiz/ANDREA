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

    @unittest.skip(
        "ToolSpecs are intentionally invalid until runtime_resources.threading is backfilled tool by tool."
    )
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

    def test_legacy_execution_scope_is_rejected(self) -> None:
        module = _load_validate_toolspecs_module()
        instance = self._minimal_toolspec(execution_capabilities=["global"])
        instance["execution_scope"] = "global"

        errors = module.semantic_errors_for_toolspec(
            tool_id="cell_tool",
            instance=instance,
        )

        self.assertTrue(
            any("execution_scope is legacy" in error for error in errors),
            errors,
        )

    def test_legacy_cell_native_capability_is_rejected(self) -> None:
        module = _load_validate_toolspecs_module()
        instance = self._minimal_toolspec(execution_capabilities=["cell_native"])

        errors = module.semantic_errors_for_toolspec(
            tool_id="cell_tool",
            instance=instance,
        )

        self.assertTrue(
            any("'cell_native' is legacy" in error for error in errors),
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
                }
            ],
        )

        errors = module.semantic_errors_for_toolspec(
            tool_id="cell_tool",
            instance=instance,
        )

        self.assertEqual(errors, [])
