from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from ._helpers import InferNetworkCoreTestCase


class InferNetworkPipelineTests(InferNetworkCoreTestCase):
    def test_execute_wrapper_calls_preflight_plan_and_run(self) -> None:
        with (
            patch(
                "andrea.core.commands.infer_network.pipeline.preflight_infer_network",
                return_value={"runs": {"selected": []}},
            ) as preflight_mock,
            patch(
                "andrea.core.commands.infer_network.pipeline.plan_infer_network",
                return_value=Path("/tmp/fake_run_dir"),
            ) as plan_mock,
            patch(
                "andrea.core.commands.infer_network.pipeline.run_infer_network_plan",
                return_value=Path("/tmp/fake_run_dir"),
            ) as run_mock,
        ):
            out = self.mod.infer_network(
                dataset_manifest_path=Path("/tmp/dataset-manifest.json"),
                tools_params_path=Path("/tmp/tools_params.json"),
            )

        self.assertEqual(out, Path("/tmp/fake_run_dir"))
        preflight_mock.assert_called_once()
        plan_mock.assert_called_once()
        run_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
