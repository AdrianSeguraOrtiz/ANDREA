from __future__ import annotations

import gzip
import importlib.util
import pickle
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOLS_ROOT = REPO_ROOT / "wrappers" / "inference_tools" / "tools"
PYTHON_TEMPLATE_ROOT = (
    REPO_ROOT / "wrappers" / "inference_tools" / "scripts" / "templates" / "python"
)
NETWORK_COLUMNS = ["source", "target", "score", "sign", "evidence", "context"]
NETWORK_HEADER = ",".join(NETWORK_COLUMNS) + "\n"

if str(PYTHON_TEMPLATE_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_TEMPLATE_ROOT))


def _load_tool_module(
    tool_id: str,
    *,
    temporary_modules: dict[str, types.ModuleType] | None = None,
):
    module_name = f"{tool_id}_empty_network_run_tool"
    previous: dict[str, types.ModuleType | None] = {}
    for name, stub in (temporary_modules or {}).items():
        previous[name] = sys.modules.get(name)
        sys.modules[name] = stub

    spec = importlib.util.spec_from_file_location(
        module_name,
        TOOLS_ROOT / tool_id / "run_tool.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        for name, old_module in previous.items():
            if old_module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old_module
    return module


PYSCENIC = _load_tool_module("pyscenic")
SCSGL = _load_tool_module("scsgl")
SCGENERAI = _load_tool_module("scgenerai")
CESPGRN = _load_tool_module("cespgrn")
METASEM = _load_tool_module("metasem")
DIGNET = _load_tool_module("dignet")
PLANET = _load_tool_module("planet")
MINIEX3 = _load_tool_module("miniex3")
SIMIC = _load_tool_module("simic")
SIMIC.np = np
INFERELATOR3 = _load_tool_module(
    "inferelator3",
    temporary_modules={"joblib": types.ModuleType("joblib")},
)
SCING = _load_tool_module(
    "scing",
    temporary_modules={"anndata": types.ModuleType("anndata")},
)

_anndata_stub = types.ModuleType("anndata")
_scanpy_stub = types.ModuleType("scanpy")
_torch_stub = types.ModuleType("torch")
_scregulate_stub = types.ModuleType("scregulate")
_scregulate_stub.train_model = object()
_fine_tuning_stub = types.ModuleType("scregulate.fine_tuning")
_fine_tuning_stub.fine_tune_clusters = object()
SCREGULATE = _load_tool_module(
    "scregulate",
    temporary_modules={
        "anndata": _anndata_stub,
        "scanpy": _scanpy_stub,
        "torch": _torch_stub,
        "scregulate": _scregulate_stub,
        "scregulate.fine_tuning": _fine_tuning_stub,
    },
)


class EmptyNetworkContractTests(unittest.TestCase):
    def test_cespgrn_writes_header_only_network_for_zero_correlations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "network.csv"

            edge_count = CESPGRN.write_network_csv(
                path=output_path,
                partial_correlations=np.zeros((1, 2, 2), dtype=float),
                gene_ids=["G1", "G2"],
                cell_ids=["C1"],
            )

            self.assertEqual(edge_count, 0)
            self.assertEqual(output_path.read_text(encoding="utf-8"), NETWORK_HEADER)

    def test_cespgrn_still_rejects_non_finite_correlations(self) -> None:
        correlations = np.zeros((1, 2, 2), dtype=float)
        correlations[0, 0, 1] = np.nan
        with (
            tempfile.TemporaryDirectory() as tmp,
            self.assertRaisesRegex(
                ValueError,
                "non-finite value",
            ),
        ):
            CESPGRN.write_network_csv(
                path=Path(tmp) / "network.csv",
                partial_correlations=correlations,
                gene_ids=["G1", "G2"],
                cell_ids=["C1"],
            )

    def test_metasem_writes_header_only_network_for_valid_empty_edges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_path = Path(tmp) / "raw.tsv"
            output_path = Path(tmp) / "network.csv"
            raw_path.write_text("TF\tTarget\tEdgeWeight\n", encoding="utf-8")

            edge_count = METASEM._convert_network(raw_path, output_path)

            self.assertEqual(edge_count, 0)
            self.assertEqual(output_path.read_text(encoding="utf-8"), NETWORK_HEADER)

    def test_metasem_still_rejects_malformed_edge_weights(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_path = Path(tmp) / "raw.tsv"
            raw_path.write_text(
                "TF\tTarget\tEdgeWeight\nG1\tG2\tnot-a-number\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "non-finite or non-numeric"):
                METASEM._convert_network(raw_path, Path(tmp) / "network.csv")

    def test_diffusion_wrappers_preserve_valid_zero_adjacencies(self) -> None:
        adjacency = pd.DataFrame(
            [[0.0, 0.0], [0.0, 0.0]],
            index=["G1", "G2"],
            columns=["G1", "G2"],
        )
        for tool in (DIGNET, PLANET):
            with self.subTest(tool=tool.__name__), tempfile.TemporaryDirectory() as tmp:
                network = tool._network_from_adjacency(adjacency)
                output_path = Path(tmp) / "network.csv"
                network.to_csv(output_path, index=False, columns=NETWORK_COLUMNS)

                self.assertTrue(network.empty)
                self.assertEqual(
                    output_path.read_text(encoding="utf-8"),
                    NETWORK_HEADER,
                )

    def test_diffusion_wrappers_still_reject_malformed_adjacencies(self) -> None:
        adjacency = pd.DataFrame(
            [[0.0, "invalid"], [0.0, 0.0]],
            index=["G1", "G2"],
            columns=["G1", "G2"],
        )
        for tool in (DIGNET, PLANET):
            with (
                self.subTest(tool=tool.__name__),
                self.assertRaisesRegex(
                    ValueError,
                    "non-numeric value",
                ),
            ):
                tool._network_from_adjacency(adjacency)

    def test_simic_writes_header_only_network_for_zero_coefficients(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            weights_path = Path(tmp) / "weights.pkl"
            output_path = Path(tmp) / "network.csv"
            with weights_path.open("wb") as fh:
                pickle.dump(
                    {
                        "weight_dic": {0: np.zeros((1, 1), dtype=float)},
                        "TF_ids": ["G1"],
                        "query_targets": ["G2"],
                    },
                    fh,
                )

            edge_count = SIMIC._convert_weights_to_network(
                weights_path=weights_path,
                phenotype_labels=["state"],
                network_csv_path=output_path,
            )

            self.assertEqual(edge_count, 0)
            self.assertEqual(output_path.read_text(encoding="utf-8"), NETWORK_HEADER)

    def test_simic_still_rejects_non_finite_coefficients(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            weights_path = Path(tmp) / "weights.pkl"
            with weights_path.open("wb") as fh:
                pickle.dump(
                    {
                        "weight_dic": {0: np.array([[np.nan]])},
                        "TF_ids": ["G1"],
                        "query_targets": ["G2"],
                    },
                    fh,
                )

            with self.assertRaisesRegex(ValueError, "non-finite coefficient"):
                SIMIC._convert_weights_to_network(
                    weights_path=weights_path,
                    phenotype_labels=["state"],
                    network_csv_path=Path(tmp) / "network.csv",
                )

    def test_inferelator_writes_header_only_network_for_valid_empty_output(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            raw_dir.mkdir()
            raw_path = raw_dir / "network.tsv.gz"
            with gzip.open(raw_path, "wt", encoding="utf-8") as fh:
                fh.write("target\tregulator\tcombined_confidences\n")
            output_path = Path(tmp) / "network.csv"

            edge_count = INFERELATOR3._convert_network(
                raw_dir=raw_dir,
                network_csv_path=output_path,
                execution=INFERELATOR3.ResolvedExecution(mode="global"),
                gene_alias_reverse_map={},
            )

            self.assertEqual(edge_count, 0)
            self.assertEqual(output_path.read_text(encoding="utf-8"), NETWORK_HEADER)

    def test_inferelator_still_rejects_missing_raw_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(FileNotFoundError):
            INFERELATOR3._convert_network(
                raw_dir=Path(tmp),
                network_csv_path=Path(tmp) / "network.csv",
                execution=INFERELATOR3.ResolvedExecution(mode="global"),
                gene_alias_reverse_map={},
            )

    def test_miniex_writes_header_only_network_for_valid_empty_edge_table(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            regulons_dir = raw_dir / "regulons"
            regulons_dir.mkdir(parents=True)
            (regulons_dir / "state_edgeTable.tsv").write_text(
                "TF\tTG\tcluster\tweight\n",
                encoding="utf-8",
            )
            output_path = Path(tmp) / "network.csv"

            edge_count = MINIEX3._convert_network(raw_dir, output_path, {})

            self.assertEqual(edge_count, 0)
            self.assertEqual(output_path.read_text(encoding="utf-8"), NETWORK_HEADER)

    def test_miniex_still_rejects_an_edge_table_without_a_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            regulons_dir = raw_dir / "regulons"
            regulons_dir.mkdir(parents=True)
            (regulons_dir / "state_edgeTable.tsv").touch()

            with self.assertRaisesRegex(ValueError, "empty or has no header"):
                MINIEX3._convert_network(
                    raw_dir,
                    Path(tmp) / "network.csv",
                    {},
                )

    def test_pyscenic_writes_header_only_network_for_valid_empty_adjacencies(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_path = Path(tmp) / "adjacencies.tsv"
            output_path = Path(tmp) / "network.csv"
            raw_path.write_text("TF\ttarget\timportance\n", encoding="utf-8")

            edge_count = PYSCENIC._convert_adjacencies(raw_path, output_path)

            self.assertEqual(edge_count, 0)
            self.assertEqual(output_path.read_text(encoding="utf-8"), NETWORK_HEADER)

    def test_pyscenic_still_rejects_malformed_adjacencies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_path = Path(tmp) / "adjacencies.tsv"
            raw_path.write_text("TF\ttarget\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "missing columns"):
                PYSCENIC._convert_adjacencies(raw_path, Path(tmp) / "network.csv")

    def test_scing_writes_header_only_network_for_valid_empty_merged_output(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_path = Path(tmp) / "final.network.merged.csv"
            output_path = Path(tmp) / "network.csv"
            raw_path.write_text("source,target,importance\n", encoding="utf-8")
            expression = SCING.ExpressionInput(
                values=pd.DataFrame(),
                gene_ids=["G1", "G2"],
                column_ids=["C1", "C2"],
            )

            edge_count = SCING._convert_network(raw_path, output_path, expression)

            self.assertEqual(edge_count, 0)
            self.assertEqual(output_path.read_text(encoding="utf-8"), NETWORK_HEADER)

    def test_scing_preserves_empty_upstream_subsamples_and_skips_the_merger(
        self,
    ) -> None:
        build_calls = 0

        class EmptyBuilder:
            def __init__(self, *, prefix, outdir, **_kwargs):
                self.prefix = prefix
                self.outdir = Path(outdir)
                self.adata = types.SimpleNamespace(n_vars=3, n_obs=3)

            def subsample_cells(self):
                return None

            def filter_genes(self):
                return None

            def filter_gene_connectivities(self):
                return None

            def build_grn(self):
                nonlocal build_calls
                build_calls += 1
                self.edges = pd.DataFrame(columns=SCING.UPSTREAM_EDGE_COLUMNS)

            def save_edges(self):
                self.outdir.mkdir(parents=True, exist_ok=True)
                self.edges.to_csv(self.outdir / f"{self.prefix}.csv.gz")

        class UnexpectedMerger:
            def __init__(self, **_kwargs):
                raise AssertionError(
                    "SCING must not merge when every upstream network is empty"
                )

        fake_scing = types.ModuleType("scing")
        fake_scing.build = types.SimpleNamespace(grnBuilder=EmptyBuilder)
        fake_scing.merge = types.SimpleNamespace(NetworkMerger=UnexpectedMerger)
        merged_adata = types.SimpleNamespace(n_obs=3)
        fake_scing.supercells = types.SimpleNamespace(
            supercell_pipeline=lambda *_args, **_kwargs: merged_adata
        )
        params = SCING.ResolvedParams(
            n_supercells=3,
            supercell_hvgs=3,
            supercell_pcs=1,
            n_subsample_networks=2,
            network_hvgs=-1,
            gene_neighbors=1,
            gene_pcs=1,
            subsample_fraction=0.7,
            edge_consensus_threshold=0.2,
            remove_cycles=True,
            random_seed=0,
        )
        expression = SCING.ExpressionInput(
            values=pd.DataFrame(),
            gene_ids=["G1", "G2", "G3"],
            column_ids=["C1", "C2", "C3"],
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_dir = root / "raw"
            work_dir = root / "work"
            raw_dir.mkdir()
            work_dir.mkdir()
            with (
                mock.patch.dict(sys.modules, {"scing": fake_scing}),
                mock.patch.object(SCING, "_to_anndata", return_value=object()),
            ):
                raw_path = SCING._run_scing(
                    expression=expression,
                    params=params,
                    raw_dir=raw_dir,
                    work_dir=work_dir,
                    progress_path=root / "progress.json",
                    threads=1,
                )

            self.assertEqual(build_calls, 2)
            self.assertEqual(
                pd.read_csv(raw_path).columns.tolist(),
                SCING.UPSTREAM_EDGE_COLUMNS,
            )
            self.assertTrue(pd.read_csv(raw_path).empty)
            for index in range(2):
                saved = raw_dir / "intermediate_networks" / f"net.{index:03d}.csv.gz"
                self.assertTrue(saved.is_file())
                self.assertTrue(pd.read_csv(saved, index_col=0).empty)

            output_path = root / "network.csv"
            self.assertEqual(
                SCING._convert_network(raw_path, output_path, expression),
                0,
            )
            self.assertEqual(output_path.read_text(encoding="utf-8"), NETWORK_HEADER)

            def omit_edges_artifact(_builder):
                return None

            with (
                mock.patch.dict(sys.modules, {"scing": fake_scing}),
                mock.patch.object(EmptyBuilder, "build_grn", omit_edges_artifact),
                self.assertRaisesRegex(RuntimeError, "did not expose an edges table"),
            ):
                SCING._build_intermediate_networks(
                    adata_merged=merged_adata,
                    params=params,
                    intermediate_dir=root / "missing-artifact",
                    threads=1,
                    progress_path=root / "missing-progress.json",
                )

    def test_scing_still_rejects_malformed_merged_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_path = Path(tmp) / "final.network.merged.csv"
            raw_path.write_text("source,target\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "missing columns"):
                SCING._convert_network(
                    raw_path,
                    Path(tmp) / "network.csv",
                    SCING.ExpressionInput(
                        values=pd.DataFrame(),
                        gene_ids=["G1", "G2"],
                        column_ids=["C1", "C2"],
                    ),
                )

    def test_scsgl_writes_header_only_network_when_no_raw_edge_is_exportable(
        self,
    ) -> None:
        raw_edges = pd.DataFrame(
            [
                {"context": "global", "Gene1": "G1", "Gene2": "G1", "EdgeWeight": 1.0},
                {"context": "global", "Gene1": "G1", "Gene2": "G2", "EdgeWeight": 0.0},
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "network.csv"

            edge_count = SCSGL._convert_edges(
                raw_edges=raw_edges,
                output_path=output_path,
                gene_order={"G1": 0, "G2": 1},
            )

            self.assertEqual(edge_count, 0)
            self.assertEqual(output_path.read_text(encoding="utf-8"), NETWORK_HEADER)

    def test_scregulate_writes_header_only_network_for_zero_weight_matrices(
        self,
    ) -> None:
        raw_weights = SCREGULATE._matrix_to_frame(
            by_context={"global": np.zeros((2, 1), dtype=float)},
            gene_names=["G1", "G2"],
            tf_names=["G1"],
        )
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "network.csv"

            edge_count = SCREGULATE._write_network(raw_weights, output_path)

            self.assertEqual(edge_count, 0)
            self.assertEqual(output_path.read_text(encoding="utf-8"), NETWORK_HEADER)

    def test_scgenerai_accepts_valid_result_tables_without_positive_edges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            results_dir = raw_dir / "results"
            results_dir.mkdir(parents=True)
            for sample_index in range(2):
                (results_dir / f"LRP_{sample_index}_{sample_index}.csv").write_text(
                    "LRP,source_gene,target_gene\n",
                    encoding="utf-8",
                )

            network = SCGENERAI.convert_raw_results(raw_dir, ["C1", "C2"])
            output_path = Path(tmp) / "network.csv"
            network.to_csv(output_path, index=False, columns=NETWORK_COLUMNS)

            self.assertTrue(network.empty)
            self.assertEqual(output_path.read_text(encoding="utf-8"), NETWORK_HEADER)

    def test_scgenerai_still_rejects_missing_raw_result_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            (raw_dir / "results").mkdir(parents=True)

            with self.assertRaises(FileNotFoundError):
                SCGENERAI.convert_raw_results(raw_dir, ["C1"])


@unittest.skipUnless(shutil.which("Rscript"), "Rscript is required for R wrapper tests")
class REmptyNetworkContractTests(unittest.TestCase):
    def test_ppcor_build_network_returns_a_typed_empty_table(self) -> None:
        r_test = r"""
        parsed <- as.list(parse(file = commandArgs(TRUE)[[1L]]))
        assignment_name <- function(expression) {
          if (!is.call(expression) || !identical(expression[[1L]], as.name("<-"))) {
            return(NA_character_)
          }
          as.character(expression[[2L]])
        }
        environment <- new.env(parent = globalenv())
        for (expression in parsed) {
          if (assignment_name(expression) %in% c("NETWORK_COLUMNS", "build_network")) {
            eval(expression, envir = environment)
          }
        }

        genes <- c("G1", "G2")
        estimate <- matrix(
          c(1, 0, 0, 1),
          nrow = 2,
          dimnames = list(genes, genes)
        )
        network <- environment$build_network(estimate)
        stopifnot(
          nrow(network) == 0L,
          identical(names(network), environment$NETWORK_COLUMNS)
        )
        output <- tempfile(fileext = ".csv")
        write.csv(network, output, row.names = FALSE)
        roundtrip <- read.csv(output, check.names = FALSE)
        stopifnot(
          nrow(roundtrip) == 0L,
          identical(names(roundtrip), environment$NETWORK_COLUMNS)
        )
        """
        self._run_r_contract("ppcor", r_test)

    def test_scminer_parses_a_header_only_consensus_table(self) -> None:
        r_test = r"""
        parsed <- as.list(parse(file = commandArgs(TRUE)[[1L]]))
        assignment_name <- function(expression) {
          if (!is.call(expression) || !identical(expression[[1L]], as.name("<-"))) {
            return(NA_character_)
          }
          as.character(expression[[2L]])
        }
        environment <- new.env(parent = globalenv())
        for (expression in parsed) {
          if (assignment_name(expression) %in% c(
            "NETWORK_COLUMNS", "map_alias", "parse_sjaracne_network"
          )) {
            eval(expression, envir = environment)
          }
        }

        upstream <- tempfile(fileext = ".tsv")
        writeLines("source\ttarget\tMI\tspearman", upstream)
        aliases <- data.frame(
          upstream_id = c("g000001", "g000002"),
          gene_id = c("G1", "G2"),
          stringsAsFactors = FALSE
        )
        network <- environment$parse_sjaracne_network(upstream, aliases)
        stopifnot(
          nrow(network) == 0L,
          identical(names(network), environment$NETWORK_COLUMNS)
        )
        output <- tempfile(fileext = ".csv")
        write.csv(network, output, row.names = FALSE)
        roundtrip <- read.csv(output, check.names = FALSE)
        stopifnot(
          nrow(roundtrip) == 0L,
          identical(names(roundtrip), environment$NETWORK_COLUMNS)
        )
        """
        self._run_r_contract("scminer", r_test)

    def _run_r_contract(self, tool_id: str, script: str) -> None:
        completed = subprocess.run(
            [
                "Rscript",
                "-e",
                script,
                str(TOOLS_ROOT / tool_id / "run_tool.R"),
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"{tool_id} empty-network contract failed:\n"
            f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
