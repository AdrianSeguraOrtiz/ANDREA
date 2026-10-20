"""Numerical and preservation checks for CeSpGRN's narrow PSD fallback."""
from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "wrappers/inference_tools/scripts/templates/python"))
SPEC = importlib.util.spec_from_file_location("cespgrn_numerical_wrapper", ROOT / "wrappers/inference_tools/tools/cespgrn/run_tool.py")
assert SPEC and SPEC.loader
WRAPPER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = WRAPPER
SPEC.loader.exec_module(WRAPPER)


class Tensor:
    def __init__(self, values, dtype=np.float32, device="cpu"):
        self.values = np.asarray(values, dtype=dtype)
        self.dtype, self.device = dtype, device

    def detach(self): return self
    def cpu(self): return self
    def numpy(self): return self.values


def check_psd(value):
    matrix = value.values.astype(float)
    eigenvalue = np.linalg.eigvalsh(matrix).min()
    return bool(eigenvalue > -1e-5 and np.allclose(matrix, matrix.T)), eigenvalue


class CeSpGRNNumericalFallbackTests(unittest.TestCase):
    def test_symmetric_projection_keeps_upstream_nonpositive_eigenvalue_rule(self):
        matrix = np.diag([-1., 0., 1e-8, 2.])
        result = WRAPPER.orthogonal_psd_projection(matrix)
        np.testing.assert_allclose(result, np.diag([1e-3, 1e-3, 1e-8, 2.]), atol=1e-14)

    def test_degenerate_high_dimensional_covariance_remains_psd(self):
        matrix = np.ones((200, 200), dtype=np.float32)
        result = WRAPPER.orthogonal_psd_projection(matrix, floor_small_positive=True)
        self.assertEqual(result.dtype, np.float64)
        np.testing.assert_allclose(result, result.T, atol=0)
        self.assertGreater(np.linalg.eigvalsh(result.astype(np.float32)).min(), 0)

    def test_projection_rejects_nonfinite_or_nonsquare_data(self):
        for matrix in (np.ones((2, 3)), np.diag([1, np.nan])):
            with self.subTest(matrix=matrix), self.assertRaisesRegex(ValueError, "finite square"):
                WRAPPER.orthogonal_psd_projection(matrix)

    def test_successful_upstream_projection_is_preserved_exactly(self):
        expected = Tensor(np.eye(3))
        original = lambda _matrix: expected
        upstream = types.SimpleNamespace(find_clostest_PSD=original, isPSD=check_psd)
        upstream.est_cov = lambda **_kwargs: upstream.find_clostest_PSD(Tensor(np.eye(3)))
        observed, repairs = WRAPPER.estimate_covariance_with_psd_fallback(
            upstream, types.SimpleNamespace(as_tensor=Tensor), counts=np.empty(0), kernel_truncated=np.empty(0))
        self.assertIs(observed, expected)
        self.assertEqual(repairs, [])
        self.assertIs(upstream.find_clostest_PSD, original)

    def test_failed_projection_is_recovered_and_reported(self):
        original = lambda _matrix: Tensor(np.diag([-0.1, 1., 2.]))
        upstream = types.SimpleNamespace(find_clostest_PSD=original, isPSD=check_psd)
        upstream.est_cov = lambda **_kwargs: upstream.find_clostest_PSD(Tensor(np.diag([-1., 0., 2.])))
        observed, repairs = WRAPPER.estimate_covariance_with_psd_fallback(
            upstream, types.SimpleNamespace(as_tensor=Tensor), counts=np.empty(0), kernel_truncated=np.empty(0))
        self.assertTrue(check_psd(observed)[0])
        self.assertEqual(len(repairs), 1)
        self.assertEqual(repairs[0]["method"], "float64_symmetric_eigh")
        self.assertLess(repairs[0]["upstream_projection_minimum_eigenvalue"], 0)
        self.assertIs(upstream.find_clostest_PSD, original)

    def test_original_projection_is_restored_after_unrelated_failure(self):
        original = lambda _matrix: None
        upstream = types.SimpleNamespace(find_clostest_PSD=original, isPSD=check_psd)
        def fail(**_kwargs): raise ValueError("invalid kernel")
        upstream.est_cov = fail
        with self.assertRaisesRegex(ValueError, "invalid kernel"):
            WRAPPER.estimate_covariance_with_psd_fallback(
                upstream, None, counts=np.empty(0), kernel_truncated=np.empty(0))
        self.assertIs(upstream.find_clostest_PSD, original)


if __name__ == "__main__":
    unittest.main()
