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

    def test_group_aggregated_requires_cell_native_capability(self) -> None:
        module = _load_validate_toolspecs_module()
        instance = self._minimal_toolspec(
            execution_capabilities=["group_aggregated"],
            conditional_required=[
                {
                    "input": "groups",
                    "execution": "mode",
                    "op": "eq",
                    "value": "group_aggregated",
                    "usage": "Used to aggregate native per-cell networks by group.",
                    "message": "groups is required when execution.mode=group_aggregated.",
                }
            ],
        )

        errors = module.semantic_errors_for_toolspec(
            tool_id="cell_tool",
            instance=instance,
        )

        self.assertTrue(
            any("must also declare 'cell_native'" in error for error in errors),
            errors,
        )

    def test_group_aggregated_requires_groups_conditional_rule(self) -> None:
        module = _load_validate_toolspecs_module()
        instance = self._minimal_toolspec(
            execution_capabilities=["cell_native", "group_aggregated"],
        )

        errors = module.semantic_errors_for_toolspec(
            tool_id="cell_tool",
            instance=instance,
        )

        self.assertTrue(
            any("execution.mode == 'group_aggregated'" in error for error in errors),
            errors,
        )

    def test_cell_native_group_aggregated_contract_can_validate(self) -> None:
        module = _load_validate_toolspecs_module()
        instance = self._minimal_toolspec(
            execution_capabilities=["cell_native", "group_aggregated"],
            conditional_required=[
                {
                    "input": "groups",
                    "execution": "mode",
                    "op": "eq",
                    "value": "group_aggregated",
                    "usage": "Used to aggregate native per-cell networks by group.",
                    "message": "groups is required when execution.mode=group_aggregated.",
                }
            ],
        )

        errors = module.semantic_errors_for_toolspec(
            tool_id="cell_tool",
            instance=instance,
        )

        self.assertEqual(errors, [])
