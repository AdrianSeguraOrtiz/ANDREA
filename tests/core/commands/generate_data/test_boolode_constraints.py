from __future__ import annotations

import json
import unittest
from pathlib import Path

from andrea.core.commands.generate_data.request import _resolve_simulator_params


class BoolodeParameterTests(unittest.TestCase):
    def resolve(self, **params):
        root = Path(__file__).resolve().parents[4]
        spec = json.loads(
            (
                root
                / "andrea/catalog_simulation_data_tools/simulators/boolode/simulatorspec.json"
            ).read_text()
        )
        return _resolve_simulator_params(
            simulator_id="boolode", user_params=params, spec_params=spec["params"]
        )

    def test_invalid_sampling_branches_are_rejected_before_execution(self):
        cases = [
            (
                {"num_cells": 300, "simulation_time": 8, "integration_step_size": 0.1},
                "integration_step_size",
            ),
            (
                {"num_cells": 500, "simulation_time": 3, "integration_step_size": 1},
                "integration_step_size",
            ),
            (
                {"num_cells": 500, "simulation_time": 3.5, "integration_step_size": 1},
                "simulation_time must be an integer",
            ),
            (
                {"sample_cells": True, "num_cells": 1000, "n_clusters": 1},
                "num_cells < 1000",
            ),
            (
                {"simulation_time": 0.02, "integration_step_size": 0.01},
                "three time points",
            ),
        ]
        for params, message in cases:
            with self.subTest(params=params), self.assertRaisesRegex(
                ValueError, message
            ):
                self.resolve(**params)

    def test_valid_small_output_default_and_fine_grids_remain_supported(self):
        for params in [
            {},
            {"num_cells": 300, "simulation_time": 8, "integration_step_size": 0.01},
            {"num_cells": 300, "simulation_time": 8, "integration_step_size": 0.001},
            # The initial time point is omitted: 499 * (3 - 1) = 998 columns.
            {"num_cells": 499, "simulation_time": 3.5, "integration_step_size": 1},
            {
                "sample_cells": True,
                "num_cells": 999,
                "n_clusters": 1,
                "integration_step_size": 0.1,
            },
        ]:
            with self.subTest(params=params):
                self.resolve(**params)


if __name__ == "__main__":
    unittest.main()
