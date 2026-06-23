#!/usr/bin/env python3
"""Build small embedded GRouNdGAN demo bundles for the Docker image."""

from __future__ import annotations

import json
import os
import pickle
import sys
import types
from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd
import torch

GROUNDGAN_SRC = Path(os.environ.get("GROUNDGAN_SRC", "/opt/groundgan/src"))
BUNDLE_ROOT = Path("/opt/andrea/bundles/groundgan")


def install_scanpy_stub() -> None:
    scanpy_stub = types.ModuleType("scanpy")
    scanpy_stub.read_h5ad = ad.read_h5ad
    sys.modules.setdefault("scanpy", scanpy_stub)


def make_graph(gene_count: int, tf_count: int) -> dict[int, set[int]]:
    graph: dict[int, set[int]] = {}
    for target in range(tf_count, gene_count):
        regulators = {(target - tf_count) % tf_count}
        if tf_count > 1 and target % 3 == 0:
            regulators.add((target - tf_count + 1) % tf_count)
        graph[target] = regulators
    return graph


def write_reference_h5ad(path: Path, *, genes: list[str], seed: int) -> None:
    rng = np.random.default_rng(seed)
    obs_names = [f"reference_cell_{index:03d}" for index in range(1, 9)]
    matrix = rng.poisson(lam=2.0, size=(len(obs_names), len(genes))).astype("float32")
    payload = ad.AnnData(
        X=matrix,
        obs=pd.DataFrame(index=obs_names),
        var=pd.DataFrame(index=genes),
    )
    payload.write_h5ad(path)


def build_bundle(
    *,
    bundle_id: str,
    gene_count: int,
    tf_count: int,
    hidden_width: int,
    seed: int,
) -> None:
    install_scanpy_stub()
    if str(GROUNDGAN_SRC) not in sys.path:
        sys.path.insert(0, str(GROUNDGAN_SRC))

    from gans.causal_gan import CausalGAN  # noqa: PLC0415
    from networks.generator import Generator  # noqa: PLC0415

    torch.manual_seed(seed)
    np.random.seed(seed)

    bundle_dir = BUNDLE_ROOT / bundle_id
    bundle_dir.mkdir(parents=True, exist_ok=True)
    genes = [f"gene_{index:03d}" for index in range(gene_count)]
    graph = make_graph(gene_count=gene_count, tf_count=tf_count)

    reference_path = bundle_dir / "reference.h5ad"
    graph_path = bundle_dir / "causal_graph.pkl"
    model_path = bundle_dir / "model_checkpoint.pth"
    cc_path = bundle_dir / "causal_controller_checkpoint.pth"

    write_reference_h5ad(reference_path, genes=genes, seed=seed)
    with graph_path.open("wb") as handle:
        pickle.dump(graph, handle)

    causal_controller = Generator(
        z_input=2,
        output_cells_dim=gene_count,
        gen_layers=[hidden_width],
        library_size=None,
    )
    torch.save({"generator_state_dict": causal_controller.state_dict()}, cc_path)

    gan = CausalGAN(
        genes_no=gene_count,
        batch_size=8,
        latent_dim=2,
        noise_per_gene=1,
        depth_per_gene=1,
        width_per_gene=2,
        cc_latent_dim=2,
        cc_layers=[hidden_width],
        cc_pretrained_checkpoint=str(cc_path),
        crit_layers=[hidden_width],
        causal_graph=graph,
        labeler_layers=[hidden_width],
        device="cpu",
        library_size=1000,
    )
    torch.save(
        {
            "generator_state_dict": gan.gen.state_dict(),
            "critic_state_dict": gan.crit.state_dict(),
        },
        model_path,
    )
    (bundle_dir / "bundle_metadata.json").write_text(
        json.dumps(
            {
                "bundle_id": bundle_id,
                "gene_count": gene_count,
                "tf_count": tf_count,
                "target_count": len(graph),
                "edge_count": sum(len(regs) for regs in graph.values()),
                "hidden_width": hidden_width,
                "seed": seed,
                "purpose": "Small untrained demo bundle for ANDREA GUI and smoke tests.",
            },
            indent=2,
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    for spec in [
        {"bundle_id": "toy_4gene", "gene_count": 4, "tf_count": 1, "hidden_width": 8, "seed": 4},
        {"bundle_id": "toy_20gene", "gene_count": 20, "tf_count": 4, "hidden_width": 32, "seed": 20},
        {"bundle_id": "toy_50gene", "gene_count": 50, "tf_count": 10, "hidden_width": 64, "seed": 50},
    ]:
        build_bundle(**spec)


if __name__ == "__main__":
    main()
