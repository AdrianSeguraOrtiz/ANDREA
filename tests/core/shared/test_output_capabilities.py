from __future__ import annotations

import unittest

from andrea.core.shared.output_capabilities import (
    validate_frozen_output_capabilities,
)


class OutputCapabilitiesIdentityTests(unittest.TestCase):
    @staticmethod
    def _tools(*, run_id: str, catalog_tool_id: str, origin: str) -> dict:
        return {
            "selected": [run_id],
            "catalog_tool_ids": {run_id: catalog_tool_id},
            "tool_origins": {run_id: origin},
            "output_capabilities": {
                run_id: {
                    "tool_origin": origin,
                    "catalog_tool_id": catalog_tool_id,
                    "directed": True,
                    "sign": "none",
                }
            },
        }

    def test_catalog_run_ids_keep_the_catalog_run_label_contract(self) -> None:
        payload = self._tools(
            run_id="catalog run label",
            catalog_tool_id="genie3",
            origin="catalog",
        )
        self.assertIn(
            "catalog run label",
            validate_frozen_output_capabilities(payload, label="tools"),
        )

    def test_custom_run_ids_require_exact_portable_identity(self) -> None:
        valid = self._tools(
            run_id="spathi.01",
            catalog_tool_id="custom_spathi.01",
            origin="custom",
        )
        self.assertIn(
            "spathi.01",
            validate_frozen_output_capabilities(valid, label="tools"),
        )

        invalid = self._tools(
            run_id="spathi/01",
            catalog_tool_id="custom_spathi.01",
            origin="custom",
        )
        with self.assertRaises(ValueError):
            validate_frozen_output_capabilities(invalid, label="tools")


if __name__ == "__main__":
    unittest.main()
