"""Runtime shim for CeSpGRN's expected torch_sqrtm module."""

from __future__ import annotations

import numpy as np
import scipy.linalg
import torch
from torch.autograd import Function


class MatrixSquareRoot(Function):
    """Matrix square root with a SciPy forward pass and Sylvester backward pass."""

    @staticmethod
    def forward(ctx, input_tensor):  # type: ignore[override]
        matrix = input_tensor.detach().cpu().numpy().astype(np.float64)
        sqrt_matrix = scipy.linalg.sqrtm(matrix)
        if np.iscomplexobj(sqrt_matrix):
            sqrt_matrix = sqrt_matrix.real
        sqrt_tensor = torch.from_numpy(np.asarray(sqrt_matrix)).to(input_tensor)
        ctx.save_for_backward(sqrt_tensor)
        return sqrt_tensor

    @staticmethod
    def backward(ctx, grad_output):  # type: ignore[override]
        grad_input = None
        if ctx.needs_input_grad[0]:
            (sqrt_tensor,) = ctx.saved_tensors
            sqrt_matrix = sqrt_tensor.detach().cpu().numpy().astype(np.float64)
            grad_matrix = grad_output.detach().cpu().numpy().astype(np.float64)
            grad_sqrt = scipy.linalg.solve_sylvester(
                sqrt_matrix,
                sqrt_matrix,
                grad_matrix,
            )
            grad_input = torch.from_numpy(np.asarray(grad_sqrt)).to(grad_output)
        return grad_input
