from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from andrea.core.commands.infer_network.commons.custom_tools import (
    normalize_custom_tool_outputs,
    normalize_custom_tools_payload,
    serialize_custom_tools,
)
from andrea.core.commands.infer_network.commons.shared import SchemaConstraints
from andrea.core.commands.infer_network.commons.tools import _load_tools_params


class CustomToolsContractTests(unittest.TestCase):
    @staticmethod
    def _constraints() -> SchemaConstraints:
        return SchemaConstraints(
            column_kinds={"cells", "samples"},
            expression_profiles={"counts", "mixed"},
            taxonomic_groups={"animal"},
            assumptions={"generic"},
            extra_input_keys={"groups", "tf_list"},
            extra_input_filenames={
                "groups": "groups.tsv",
                "tf_list": "tf_list.txt",
            },
        )

    @staticmethod
    def _valid_tool() -> dict[str, object]:
        return {
            "run_id": "demo_01",
            "name": "Demo Tool",
            "docker_image": "example/demo:1.0",
            "execution_mode": "global",
            "extra_inputs": ["tf_list"],
            "outputs": {"directed": True, "sign": "none"},
        }

    def _normalize(
        self,
        tool: dict[str, object],
    ) -> tuple[dict[str, dict[str, object]], list[dict[str, object]]]:
        with tempfile.TemporaryDirectory() as tmp:
            specs, blocked = normalize_custom_tools_payload(
                payload={"tools": [tool]},
                tools_root=Path(tmp),
                constraints=self._constraints(),
            )
        return specs, blocked

    def test_canonical_definition_round_trips_without_defaults(self) -> None:
        specs, blocked = self._normalize(self._valid_tool())

        self.assertEqual(blocked, [])
        self.assertEqual(
            serialize_custom_tools(specs),
            {"tools": [self._valid_tool()]},
        )

    def test_all_public_fields_are_required(self) -> None:
        for key in (
            "run_id",
            "name",
            "docker_image",
            "execution_mode",
            "extra_inputs",
            "outputs",
        ):
            tool = self._valid_tool()
            del tool[key]

            with self.subTest(key=key):
                if key == "run_id":
                    with self.assertRaisesRegex(
                        ValueError,
                        r"custom_tools\.tools\[1\]\.run_id is required",
                    ):
                        self._normalize(tool)
                    continue
                specs, blocked = self._normalize(tool)
                self.assertEqual(specs, {})
                self.assertEqual(len(blocked), 1)
                self.assertIn(f"{key} is required", blocked[0]["issues"][0]["message"])

    def test_enum_values_must_already_be_canonical(self) -> None:
        cases = [
            ("execution_mode", " global ", "unsupported execution_mode"),
            ("execution_mode", "GLOBAL", "unsupported execution_mode"),
            ("outputs.sign", "SIGNED", "outputs.sign must be one of"),
            ("outputs.sign", " signed ", "outputs.sign must be one of"),
        ]
        for field, value, expected in cases:
            tool = self._valid_tool()
            if field == "outputs.sign":
                tool["outputs"] = {"directed": True, "sign": value}
            else:
                tool[field] = value

            with self.subTest(field=field, value=value):
                specs, blocked = self._normalize(tool)
                self.assertEqual(specs, {})
                self.assertIn(expected, blocked[0]["issues"][0]["message"])

    def test_run_id_is_portable_and_never_slugified(self) -> None:
        case_preserving = self._valid_tool()
        case_preserving["run_id"] = "Demo.Tool-01"
        specs, blocked = self._normalize(case_preserving)

        self.assertEqual(blocked, [])
        self.assertEqual(list(specs), ["custom_Demo.Tool-01"])
        self.assertEqual(
            serialize_custom_tools(specs)["tools"][0]["run_id"],
            "Demo.Tool-01",
        )

        for run_id in ("demo tool", "../demo", "demo/tool", ".demo", "demo\\tool"):
            tool = self._valid_tool()
            tool["run_id"] = run_id
            with (
                self.subTest(run_id=run_id),
                self.assertRaisesRegex(ValueError, "run_id is invalid:.*must match"),
            ):
                self._normalize(tool)

    def test_duplicate_run_ids_are_rejected_before_catalog_construction(self) -> None:
        tool = self._valid_tool()
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(
            ValueError,
            "duplicates derived tool_id: custom_demo_01",
        ):
            normalize_custom_tools_payload(
                payload={"tools": [tool, copy.deepcopy(tool)]},
                tools_root=Path(tmp),
                constraints=self._constraints(),
            )

    def test_custom_prefix_is_always_derived_from_the_complete_run_id(self) -> None:
        tool = self._valid_tool()
        tool["run_id"] = "custom_demo_01"

        specs, blocked = self._normalize(tool)

        self.assertEqual(blocked, [])
        self.assertEqual(list(specs), ["custom_custom_demo_01"])

    def test_tools_params_external_identity_is_never_repaired(self) -> None:
        custom_identity = {"custom_demo_01": "demo_01"}
        valid_run = {
            "run_id": "demo_01",
            "tool_id": "custom_demo_01",
            "params": {},
            "execution": {"mode": "global"},
        }

        with tempfile.TemporaryDirectory() as tmp:
            tools_params_path = Path(tmp) / "tools_params.json"

            def load(run: dict[str, object]) -> dict[str, dict[str, object]]:
                tools_params_path.write_text(
                    json.dumps({"runs": [run]}),
                    encoding="utf-8",
                )
                return _load_tools_params(
                    tools_params_path,
                    custom_run_ids_by_tool_id=custom_identity,
                )

            self.assertEqual(
                load(valid_run),
                {
                    "demo_01": {
                        "tool_id": "custom_demo_01",
                        "params": {},
                        "execution": {"mode": "global"},
                    }
                },
            )

            rejected_cases = [
                {key: value for key, value in valid_run.items() if key != "run_id"},
                {**valid_run, "run_id": ""},
                {**valid_run, "run_id": " demo_01 "},
                {**valid_run, "tool_id": " custom_demo_01 "},
            ]
            for run in rejected_cases:
                with (
                    self.subTest(run=run),
                    self.assertRaisesRegex(
                        ValueError,
                        "external identity must be exactly",
                    ),
                ):
                    load(run)

            changed_run = load({**valid_run, "run_id": "different_01"})
            self.assertIn("different_01", changed_run)
            self.assertEqual(
                changed_run["different_01"]["tool_id"],
                "custom_demo_01",
            )

            aliased_tool = load({**valid_run, "tool_id": "demo_01"})
            self.assertEqual(aliased_tool["demo_01"]["tool_id"], "demo_01")

    def test_tools_params_catalog_identity_keeps_existing_normalization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tools_params_path = Path(tmp) / "tools_params.json"
            tools_params_path.write_text(
                json.dumps(
                    {
                        "runs": [
                            {
                                "run_id": " catalog_run ",
                                "tool_id": " aracne3 ",
                            },
                            {"tool_id": "genie3"},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            loaded = _load_tools_params(
                tools_params_path,
                custom_run_ids_by_tool_id={"custom_demo_01": "demo_01"},
            )

        self.assertEqual(loaded["catalog_run"]["tool_id"], "aracne3")
        self.assertEqual(loaded["genie3__02"]["tool_id"], "genie3")

    def test_extra_inputs_are_explicit_unique_canonical_standard_keys(self) -> None:
        cases = [
            (["tf_list", "tf_list"], "must not contain duplicates"),
            ([" tf_list"], "without surrounding whitespace"),
            (["unknown"], "unsupported standardized inputs"),
        ]
        for extra_inputs, expected in cases:
            tool = self._valid_tool()
            tool["extra_inputs"] = extra_inputs

            with self.subTest(extra_inputs=extra_inputs):
                specs, blocked = self._normalize(tool)
                self.assertEqual(specs, {})
                self.assertIn(expected, blocked[0]["issues"][0]["message"])

    def test_output_normalization_rejects_case_and_whitespace_variants(self) -> None:
        for sign in ("SIGNED", " signed "):
            with self.subTest(sign=sign):
                outputs, errors = normalize_custom_tool_outputs(
                    {"directed": True, "sign": sign}
                )
                self.assertNotIn("sign", outputs)
                self.assertEqual(
                    errors,
                    ["outputs.sign must be one of: none, signed, mixed"],
                )

    def test_serialization_fails_on_missing_or_corrupt_internal_fields(self) -> None:
        specs, blocked = self._normalize(self._valid_tool())
        self.assertEqual(blocked, [])
        tool_id = "custom_demo_01"
        cases = [
            ("missing run metadata", "_andrea_run_id", None),
            ("missing execution metadata", "_andrea_execution_mode", None),
            ("missing name", "name", None),
            ("missing extras", "extra_inputs", None),
            ("wrong capabilities", "execution_capabilities", ["group_native"]),
            (
                "noncanonical outputs",
                "outputs",
                {
                    "directed": True,
                    "sign": "NONE",
                    "evidence": "external_tool_output",
                },
            ),
        ]
        for label, key, value in cases:
            corrupt = copy.deepcopy(specs)
            if value is None:
                del corrupt[tool_id][key]
            else:
                corrupt[tool_id][key] = value

            with (
                self.subTest(label=label),
                self.assertRaisesRegex(
                    ValueError,
                    "Cannot serialize custom tool",
                ),
            ):
                serialize_custom_tools(corrupt)


if __name__ == "__main__":
    unittest.main()
