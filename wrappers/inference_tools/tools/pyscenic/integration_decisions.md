# pySCENIC Integration Decisions

## Sources Reviewed

- Upstream repo: `wrappers/inference_tools/tools/pyscenic/repo/pySCENIC/`
- Local paper:
  - `wrappers/inference_tools/tools/pyscenic/papers/pySCENIC.pdf`
  - extracted helper text: `wrappers/inference_tools/tools/pyscenic/papers/pySCENIC.txt`
- Installation/version checks:
  - `wrappers/inference_tools/tools/pyscenic/repo/pySCENIC/docs/installation.rst`
  - `wrappers/inference_tools/tools/pyscenic/repo/pySCENIC/README.rst`
  - PyPI project page: `https://pypi.org/project/pyscenic/`
  - ReadTheDocs installation page: `https://pyscenic.readthedocs.io/en/latest/installation.html`

## Paper Preparation

- PDF inputs found: `pySCENIC.pdf`.
- Extracted text used for analysis: `pySCENIC.txt`.
- Extraction quality: usable for method, DOI, authorship, workflow and output
  semantics. Some ligatures and line wrapping are present but did not block the
  audit.

## Selected Contract

The wrapper will mirror the public command:

```bash
pyscenic grn <expression_mtx_fname> <tfs_fname> --method <method> \
  --num_workers <threads> --client_or_address local -o raw/adjacencies.tsv
```

This is the only pySCENIC entrypoint selected for Phase 1 because it directly
produces a TF-target adjacency table with an `importance` score. That output
maps cleanly to ANDREA `network.csv`.

The full SCENIC pipeline is intentionally not exposed in this integration
contract. `ctx` and `aucell` produce regulon/motif/AUC artifacts, not a raw GRN
edge table, and require cisTarget ranking databases plus motif annotations with
species/genome semantics that are not represented by the current normalized
input catalog.

## Upstream Interface Audit

### Public execution modes / entrypoints

- `pyscenic grn`
  - Evidence:
    - `README.rst` describes SCENIC step 1 as deriving TFs and target genes
      with arboreto, GRNBoost2 and GENIE3.
    - `docs/installation.rst` documents `pyscenic {grn,add_cor,ctx,aucell}`
      and Docker examples for `pyscenic grn --num_workers 6 -o ...`.
    - `src/pyscenic/cli/pyscenic.py` defines parser `grn`, required
      `expression_mtx_fname` and `tfs_fname`, optional `--method`,
      `--seed`, `--num_workers`, `--client_or_address` and writes `network`
      to CSV/TSV.
    - `papers/pySCENIC.txt` states the output of the network inference step is
      a list of adjacencies connecting a TF with a target gene and a weight or
      importance.
  - ANDREA mapping:
    - `global`: one pySCENIC GRN run over the full expression matrix.
    - `group_emulated`: ANDREA partitions expression columns by `groups` and
      runs the same pySCENIC GRN command once per group.
  - Not `group_native`: no documented pySCENIC CLI argument consumes group
    labels and produces multiple group networks in one run.
  - Not `column_native`: no public pySCENIC GRN interface produces one network
    per input column/cell.
  - Not `group_aggregated`: no native per-column pySCENIC network exists to
    aggregate.

- `pyscenic add_cor`
  - Evidence:
    - `docs/installation.rst` calls it optional and says it adds Pearson
      correlations to GRN adjacencies.
    - `src/pyscenic/cli/pyscenic.py` defines `add_cor` over an existing
      adjacency file and expression matrix.
  - Decision: excluded. It is a post-processing annotation step, not the
    primary GRN inference entrypoint. The selected score remains upstream
    `importance`; the wrapper will not mix a post-hoc Pearson correlation sign
    into the `pyscenic grn` contract.

- `pyscenic ctx`
  - Evidence:
    - `docs/installation.rst` says successful full pipeline use needs ranking
      databases and motif-to-TF annotations.
    - `src/pyscenic/cli/pyscenic.py` requires `module_fname`,
      one or more `database_fname` files and `--annotations_fname`.
    - The paper describes cisTarget pruning and motif enrichment after the
      initial GRN step.
  - Decision: excluded. It produces enriched motifs/regulons and depends on
    species/genome cisTarget resources not currently modeled by normalized
    inputs. Adding it later would require new input specs for ranking databases
    and motif annotation tables.

- `pyscenic aucell`
  - Evidence:
    - `src/pyscenic/cli/pyscenic.py` requires an expression matrix and gene
      signatures and writes an AUC matrix.
    - The paper describes AUCell as quantifying regulon activity at cellular
      resolution.
  - Decision: excluded. The output is a cell-by-regulon activity matrix, not a
    GRN edge list.

- `arboreto_with_multiprocessing.py`
  - Evidence:
    - `setup.py` installs `src/pyscenic/cli/arboreto_with_multiprocessing.py`
      as a script.
    - The script accepts expression matrix, TF list, `--method`,
      `--num_workers`, `--seed` and writes an adjacency table.
  - Decision: excluded for now. It duplicates the GRN step through a lower
    level helper script, while the documented user-facing pySCENIC command is
    `pyscenic grn`.

- Notebook/API workflow
  - Evidence:
    - `docs/tutorial.rst` shows Python calls to `arboreto.algo.grnboost2`,
      `prune2df`, `df2regulons` and `aucell`.
  - Decision: excluded as separate public modes. The selected CLI command is
    the reproducible runtime contract.

## Required Inputs

- Expression matrix: implicit ANDREA normalized expression matrix. The wrapper
  will serialize it as CSV/TSV with rows as expression columns/cells and
  columns as genes, matching `pyscenic grn` without `--transpose`.
- `tf_list`: required.
  - Evidence:
    - `docs/installation.rst` says a list of transcription factors is required
      for the GENIE3/GRNBoost2 network inference step.
    - `src/pyscenic/cli/pyscenic.py` makes `tfs_fname` a required positional
      argument and describes it as TXT with one TF per line.
    - Existing normalized spec `andrea/catalog_inference_tools/input_specs/tf_list.json`
      has exactly that semantic content and checks that lines are expression
      genes.
  - Decision: reuse existing `tf_list`; no new input spec.

## Optional or Conditional Inputs

- `groups`: conditional for `execution.mode=group_emulated`.
  - Evidence:
    - Existing `groups.json` maps expression column ids to a group label.
    - pySCENIC has no group input; ANDREA can emulate grouped inference by
      partitioning the expression columns before invoking a global command.
  - Decision: declare `groups` as `conditional_required` for `group_emulated`.

- No optional inputs are declared for the selected `grn` contract.
- No new input specs are required in Phase 1.

## Parameters and Defaults

- `method`
  - Chosen value: enum `["grnboost2", "genie3"]`, default `grnboost2`.
  - Evidence:
    - `src/pyscenic/cli/pyscenic.py` defines `--method` choices
      `genie3` and `grnboost2`, default `grnboost2`.
    - The paper states the workflow relies by default on GRNBoost2 and allows
      GENIE3.
  - Rationale: this changes the public upstream GRN inference algorithm.
  - Uncertainty: none.

- `seed`
  - Chosen value: nullable integer, default `null`.
  - Evidence:
    - `src/pyscenic/cli/pyscenic.py` defines `--seed`, default `None`, with
      help text saying the default is a random seed.
  - Rationale: `null` preserves the upstream runtime-dependent default by
    omitting `--seed`. An integer gives deterministic regressor initialization.
  - Uncertainty: none.

- Not exposed as params:
  - `--num_workers`: runtime resource mapped from ANDREA `--threads`.
  - `--client_or_address`: fixed to `local` for one logical ANDREA run.
  - `--output`: fixed to wrapper raw artifact path.
  - `--transpose`: fixed false because the wrapper writes rows as cells and
    columns as genes.
  - `--sparse`: not exposed in Phase 1; wrapper serialization controls the
    matrix representation and the public input is the normalized expression
    matrix, not an upstream sparse-file choice.
  - Loom attribute names: not exposed; the wrapper will not pass loom input to
    the selected command.

## Output Mapping to `network.csv`

- Upstream raw output:
  - `pyscenic grn` writes a table of TF-target genes.
  - The pySCENIC paper describes a weight or importance associated with each
    TF-target connection.
  - Arboreto output columns are expected as `TF`, `target`, `importance`.
- ANDREA mapping:
  - `source`: upstream `TF`, mapped back to the exact ANDREA gene id.
  - `target`: upstream `target`, mapped back to the exact ANDREA gene id.
  - `score`: positive raw `importance` magnitude.
  - `sign`: no sign is exported; the selected upstream score is not a signed
    coefficient.
  - `evidence`: `association`.
  - `context`: `global` or `group:<group_id>` depending on execution mode.
- Filtering:
  - The wrapper must not write `score <= 0` rows.
  - No ANDREA-specific score normalization is applied in the wrapper.
- Identifier preservation:
  - pySCENIC/arboreto can read gene names from matrix headers, but the wrapper
    should write `raw/gene_alias_map.tsv` if it has to protect problematic
    identifiers for upstream parsing. All public outputs must be converted
    back to exact ANDREA expression gene ids.

## Runtime Resource Mapping

- ToolSpec: `runtime_resources.threading.supported=true`,
  `default_threads=1`, `max_threads=8`.
- Evidence:
  - `README.rst` says pySCENIC scales to multi-core clusters through Dask.
  - `docs/installation.rst` Docker examples pass `--num_workers 6`.
  - `src/pyscenic/cli/pyscenic.py` adds `--num_workers` defaulting to
    `cpu_count()` and calls `_prepare_client(args.client_or_address,
    num_workers=args.num_workers)` in the GRN command.
  - `src/pyscenic/prune.py` implements `_prepare_client()` with Dask
    `LocalCluster(n_workers=num_workers, threads_per_worker=1)` for `local`.
  - `src/pyscenic/cli/pyscenic.py` sets `OPENBLAS_NUM_THREADS=1` and
    `MKL_NUM_THREADS=1`.
- Decision:
  - ANDREA `--threads=N` maps to `pyscenic grn --num_workers N
    --client_or_address local`.
  - The wrapper should also prevent nested oversubscription for BLAS/OpenMP
    style libraries where practical.
  - Worker controls must not be user-facing tool params.
- Uncertainty:
  - Upstream has no declared hard worker cap; `max_threads=8` follows current
    ANDREA planning practice for CPU tools and can be revised after cost
    profiling.

## Installation Strategy

- Preferred public installation source:
  - `pip install pyscenic==0.12.1`.
- Evidence:
  - `docs/installation.rst` documents stable installation with `pip install
    pyscenic` and notes Python 3.7 or greater.
  - `README.rst` release notes identify version `0.12.1`.
  - `setup.py` package name is `pyscenic`.
  - PyPI and ReadTheDocs confirm `0.12.1` as the available stable release used
    by upstream container examples.
- Fallback:
  - If package installation becomes unavailable, install from upstream GitHub
    tag `0.12.1` (`ce41b61b6570490949bd12b514e9f6de46d19c1f`), not from the
    local repo folder.
- Implemented:
  - Dockerfile uses Python 3.10 and installs `pyscenic==0.12.1` with that same
    interpreter/runtime.

## ToolSpec Evidence Ledger

### `schema_version`

- Chosen value: `1.0`.
- Evidence: current catalog schema requires `1.0`.
- Rationale: standard ANDREA ToolSpec version.
- Uncertainty: none.

### `id`

- Chosen value: `pyscenic`.
- Evidence: scaffold path and catalog location use `pyscenic`.
- Rationale: stable lowercase tool id.
- Uncertainty: none.

### `name`

- Chosen value: `pySCENIC`.
- Evidence: upstream README title and package docs.
- Rationale: public upstream spelling.
- Uncertainty: none.

### `publication`, `first_author`, `year`

- Chosen values:
  - `publication`: `https://doi.org/10.1038/s41596-020-0336-2`,
    `https://doi.org/10.1038/nmeth.4463`
  - `first_author`: `Bram Van de Sande`
  - `year`: `2020`
- Evidence:
  - `papers/pySCENIC.txt` first page gives DOI
    `10.1038/s41596-020-0336-2`, title "A scalable SCENIC workflow for
    single-cell gene regulatory network analysis", first author Bram Van de
    Sande and Nature Protocols 2020 volume/page line.
  - `README.rst` references the original SCENIC Nature Methods paper DOI
    `10.1038/nmeth.4463`.
- Rationale: the first DOI is the pySCENIC implementation/protocol paper; the
  second records the original SCENIC method origin.
- Uncertainty: none.

### `method_summary` and `method_keywords`

- Chosen value: summary and keywords in ToolSpec.
- Evidence:
  - `README.rst` says pySCENIC is a Python implementation of SCENIC for
    single-cell RNA-seq and that step 1 uses arboreto, GRNBoost2 and GENIE3.
  - `papers/pySCENIC.txt` describes three stages: GRNBoost2 network inference,
    cisTarget pruning and AUCell activity; it also states the network
    inference output is TF-target adjacencies with importance weights.
- Rationale: summary reflects the published method while making the selected
  wrapper scope (`grn`) explicit.
- Uncertainty: none.

### `implementation_url`

- Chosen value: `https://pypi.org/project/pyscenic/`.
- Evidence: upstream docs recommend `pip install pyscenic`; PyPI hosts the
  stable package.
- Rationale: package manager source is preferred over cloning a repo.
- Uncertainty: none.

### `docker_image`

- Chosen value: `adriansegura99/inference-tools_pyscenic:1.0.0`.
- Evidence: existing inference tool image naming convention.
- Rationale: matches project convention for wrapper images.
- Uncertainty: final image exists only after Phase 2 build/publish.

### `execution_capabilities`

- Chosen value: `["global", "group_emulated"]`.
- Evidence and rationale: see "Upstream Interface Audit".
- Uncertainty: none for selected `grn` entrypoint.

### `runtime_resources`

- Chosen value: supported threading, default `1`, max `8`, map to
  `--num_workers`.
- Evidence and rationale: see "Runtime Resource Mapping".
- Uncertainty: upstream hard max is not defined.

### `taxonomic_scope` and `compatibility_rules`

- Chosen value: all broad groups, no supported species list, no compatibility
  rules.
- Evidence:
  - The selected `pyscenic grn` contract consumes a user-provided TF list and
    expression gene ids. No motif ranking database or species-specific
    cisTarget annotation is used in this contract.
- Rationale: species constraints belong to the excluded `ctx`/full-pipeline
  resources, not to GRNBoost2/GENIE3 with a matching TF list.
- Uncertainty: biological interpretability depends on a correct TF list for
  the organism.

### `accepts` and `assumes`

- Chosen values: `accepts=["cells"]`, `assumes="scrna_specific"`.
- Evidence:
  - README and paper repeatedly describe single-cell RNA-seq expression
    matrices and SCENIC cell-level workflows.
  - `pyscenic grn` help describes the input as an expression matrix for a
    single-cell experiment.
- Rationale: pySCENIC is designed for scRNA-seq cells. The selected GRN step
  uses variation across cells.
- Uncertainty: metacells may be technically runnable but are not declared by
  the primary docs reviewed.

### `extra_inputs`

- Chosen values: required `tf_list`, conditional `groups` for
  `group_emulated`.
- Evidence and rationale: see "Required Inputs" and "Optional or Conditional
  Inputs".
- Uncertainty: none.

### `outputs`

- Chosen values: directed, no sign, evidence `association`.
- Evidence:
  - The paper states the GRN step returns adjacencies connecting a TF with a
    target gene and an importance weight.
  - The CLI writes the arboreto network table as-is.
- Rationale: TF-to-target regression importance is directed by candidate TF
  role but not signed.
- Uncertainty: none.

### `progress`

- Chosen value: `kind="none"`.
- Evidence:
  - `pyscenic grn` logs coarse stages but the documented CLI does not expose a
    stable structured progress unit.
  - The alternative helper script has a `tqdm` progress bar, but it is not the
    selected public command.
- Rationale: wrapper should report coarse lifecycle only.
- Uncertainty: a future wrapper could parse lower-level arboreto progress, but
  that would be less stable than the documented CLI.

### `params`

- Chosen values: `method`, `seed`.
- Evidence and rationale: see "Parameters and Defaults".
- Uncertainty: none.

### `artifacts_aux`

- Chosen values:
  - `pyscenic.log`
  - `raw/adjacencies.tsv`
  - `raw/pyscenic_config.json`
  - `raw/gene_alias_map.tsv`
- Evidence:
  - `pyscenic grn` writes raw adjacencies.
  - Playbook requires preserving public identifiers; an alias map may be needed
    if upstream-safe gene names are used.
- Rationale: keep raw upstream output, resolved configuration and any id
  round-trip map available for inspection.
- Uncertainty: if Phase 2 proves aliasing unnecessary for all supported ids,
  `gene_alias_map.tsv` may still be written as an identity map.

## Normalized Input Mapping

### Reused input specs

- `tf_list`
  - Semantics match pySCENIC `tfs_fname`: one TF identifier per line.
- `groups`
  - Semantics match ANDREA grouping for group-emulated execution.

### New input specs required

- None for the selected `pyscenic grn` contract.
- Future full-pipeline support would likely require new specs for cisTarget
  ranking databases, motif-to-TF annotations and regulon/signature artifacts.

## Implemented Wrapper Notes

- Runtime source: Dockerfile installs `pyscenic==0.12.1` from PyPI; the local
  `repo/` folder is not copied into the image.
- Interpreter/runtime: Python 3.10 image, matching upstream installation docs
  and upstream container examples.
- Dependency compatibility: Dockerfile pins `setuptools==80.9.0` because
  `ctxcore==0.2.0` imports `pkg_resources`, and pins `umap-learn==0.5.3` so
  `pyscenic==0.12.1` does not pull a newer `scikit-learn` stack during
  dependency resolution.
- Expression serialization: wrapper writes an upstream TSV with rows as
  expression columns/cells and columns as genes, then runs without
  `--transpose`.
- Params: wrapper validates and passes `method`; when `seed=null` it omits
  `--seed`, preserving the pySCENIC random-seed default.
- Runtime resources: wrapper maps ANDREA `--threads` to
  `pyscenic grn --num_workers <threads> --client_or_address local`; worker
  controls are not exposed as user params.
- Output conversion: wrapper filters non-finite, self-loop and
  `importance <= 0` edges, writes raw positive `score=importance`, `sign=?`,
  `evidence=association` and no ANDREA-specific normalization.
- Identifier preservation: wrapper uses public gene ids directly and writes
  `raw/gene_alias_map.tsv` as an identity map. If later upstream-safe aliases
  become necessary, this artifact is the round-trip location.
- Execution modes: wrapper validates `execution.mode=global` and
  `execution.mode=group_emulated`. For `group_emulated`, it requires and
  validates that every expression column in the current run is present in
  `groups.tsv`. The wrapper allows `groups.tsv` to contain additional global
  rows because ANDREA core can pass a full grouping file to each group-emulated
  subrun after it has already subset the expression matrix. The wrapper does
  not partition internally; ANDREA core owns group-emulated orchestration and
  group context rewriting.

## Smoketest Outcome

- Validation:
  - `python -m py_compile wrappers/inference_tools/tools/pyscenic/run_tool.py`
  - `make validate-toolspecs ARGS="--tool pyscenic"`
  - `python wrappers/inference_tools/scripts/validate_input_specs.py --spec tf_list --spec groups`
  - `python wrappers/inference_tools/scripts/validate_smoketest_configs.py --tool pyscenic`
- Build:
  - `python wrappers/inference_tools/scripts/build_tool_images.py --tool pyscenic --image-tag pyscenic=pyscenic-smoketest:local`
- Smoketest:
  - `python wrappers/inference_tools/scripts/run_smoketests.py --tool pyscenic --skip-image-build --image-tag pyscenic=pyscenic-smoketest:local --timeout 300 --show-output --show-output-lines 100`
  - Result: passed.
  - Variant `global_grnboost2`: produced 21 positive directed edges and all
    four declared auxiliary artifacts.
  - Variant `group_emulated_contract`: validated `groups.tsv`, recorded two
    groups in `raw/pyscenic_config.json`, produced 21 positive directed edges
    and all four declared auxiliary artifacts. Direct wrapper output retains
    `context=global`; ANDREA core is responsible for actual group-emulated
    partitioning and `group:<id>` public contexts.
- GENIE3 is not run in the smoke test to keep runtime small; schema validation
  covers the enum value and the wrapper passes it through to
  `pyscenic grn --method`.
- Phase 3 verification:
  - `python wrappers/inference_tools/scripts/run_smoketests.py --tool pyscenic --timeout 300`
  - Result: passed on both `global_grnboost2` and
    `group_emulated_contract`; each variant produced 21 positive directed
    edges and validated 4 auxiliary artifacts.
- GUI follow-up fix:
  - A GUI `group_emulated` run at
    `inferred_networks/gui_dataset_20260623T014257Z` failed because each
    subrun received a subset expression matrix while `groups.tsv` still
    contained global rows for other expression columns.
  - The wrapper now accepts a superset `groups.tsv` and filters the in-memory
    group map to the current expression columns.
  - The pyscenic-specific smoketest fixture uses a 20-column expression matrix
    with a 30-row `groups.tsv` to cover this GUI subrun shape.
  - Follow-up verification passed with the rebuilt `pyscenic-smoketest:local`
    image: both `global_grnboost2` and `group_emulated_contract` produced 21
    positive directed edges and validated 4 auxiliary artifacts.

## Known Limitations / Open Questions

- The integration is not a full SCENIC pipeline. It does not run cisTarget
  pruning, motif enrichment, regulon generation, AUCell or loom export.
- A valid organism-specific TF list is mandatory. The ToolSpec cannot verify
  biological TF correctness beyond the existing expression-gene subset check.
- No species compatibility rule is declared because the selected GRN entrypoint
  does not consume species-specific cisTarget resources.
- Worker scaling beyond 8 threads is not declared until cost profiling supports
  a larger planning cap.
