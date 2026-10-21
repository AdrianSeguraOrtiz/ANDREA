# dynGENIE3 integration review — 2026-10-21

This is the review before the recovered calibration campaign. Live execution
and acceptance status belong to the campaign state and hashed final dossier,
not to this dated review. The first campaign remains intact under
`release-logs/dyngenie3/campaign/`; recovery uses
`release-logs/dyngenie3/campaign-recovery-20261021/`.

## Observed failure and corrections

The first campaign built immutable image
`sha256:4494966f2bc3ae22e95db9d62966ff43b9f21a955931f97a3b1f433f12d05313`
and passed its 12 planned physical executions, including exact native VIM
repeatability across one/two workers and sensitivity to a changed seed. It
stopped before calibration: the catalog figure generator called `int(None)`
for the valid `max_threads=null` contract. The tool spec and smoke config had
already been installed, so the old activation was partial, rather than an
unsuccessful forest fit.

The renderer now preserves an absent tool-level thread ceiling. A regression
test covers bounded and unbounded tools, and regeneration includes all 25
tools. Activation state distinguishes installed artifacts from completed maps.
Recovery preserves the failed campaign, verifies its build receipt and exact
installed-source subjects, and reuses the immutable image only when they match.
It imports no functional or calibration acceptance: the stronger physical
verification runs again.

The review also corrected two independent issues:

- Core's raw merge rounded weights to 12 significant figures. It now preserves
  a binary64 round trip. The min/max normalization formula remains unchanged;
  its normalized weights have a different intended meaning from native weights.
  Existing result archives are untouched. A new real CLI execution verified
  VIM → wrapper CSV → merged raw equality for all 12 TF/SS edges.
- Prediction evidence could reject the same genuine run copied during benchmark
  resume. It now counts that execution once only after checking identical
  timing, dimensions, inputs, outputs and source/image/hardware provenance.
  Conflicting copies or repeated IDs inside one receipt still fail.

The numerical audit additionally found a float32 edge case in default decay
estimation: adjacent intervals can be finite while the separation of a gene's
minimum/maximum expression times overflows. It could silently yield a zero
decay rate. Recovery rejects such an unrepresentable separation when
`alpha="from_data"`; explicit valid rates retain their supported derivative
path. This changes installed wrapper code, so recovery builds version `1.0.1`
and preserves `1.0.0` instead of reusing or overwriting it.
The optional RF steady-state diagnostic also needed to handle nonfinite
predictions when alpha is zero: SciPy 1.12 raises rather than returning NaN.
The compatibility patch now records that correlation as undefined without
discarding the primary network. A real pinned-library check reproduced this
case without fitting a model; expanded image tests cover it physically.

## Comparison with the catalog's acceptance requirements

| Requirement | dynGENIE3 evidence and scope |
| --- | --- |
| Method and implementation identity | Official Python at the requested commit, public byte checksum, pinned image base and hashed dependency wheels. Python is the paper implementation and has the broadest exposed surface; no cross-language speed ranking is asserted. |
| Scientific contract | Explicit observed longitudinal bulk time, independent series boundaries, irregular intervals, exact ID joins and optional separate steady-state samples. Unsupported cohort pairing, implicit technical averaging and pseudotime are rejected. |
| Public parameters and defaults | All eight compatible JSON controls have traceability; defaults retain RF, 1000 trees, sqrt features and data-derived decay. Seed is a documented ANDREA extension; workers are a resource control. The double-KO prediction workflow is explicitly excluded from network inference. |
| Outputs | Directed unsigned target-normalized ranking weights; native VIM, alphas, diagnostics, parameters, seeds, hashes and optional models retained. Raw core delivery now preserves float values. No causal, signed or edge-lag claim is made. |
| Repeatability and effective resources | Twelve original image tests passed, with exact repeated VIM across one/two workers, changed-seed sensitivity, native pools of one thread and observed cgroup CPU/RAM limits. |
| Diagnostic semantics | Expanded physical acceptance has 17 containers, including RF quality TS-only, ET with SS, alpha zero, one tree and one training sample. Eight-gene fixtures avoid the trivial six-gene top-five stability case. Five in-container probes reload saved models without refitting and independently reconstruct VIM, OOB diagnostics and the upstream stability definition. Undefined metrics remain null. |
| Core, CLI, GUI and maps | Actual default TF+SS CLI execution passed; raw float values are exact. Browser preflight, eligibility, selection and public parameter controls passed with no JS errors. The map now includes dynGENIE3 under tree methods. |
| Cost structure and domain | Planned, not yet accepted at this review: 408 runs, 27 crossed default points plus seven point-only branch centers, four resource strata and three repetitions. Exact temporal geometry guards prevent equal-column profiles from substituting for different series designs. |
| Independent runtime prediction accuracy | Pending actual held-out results: 24 executions at genes 75/250 and exact default t8/s2 shape, with frozen calls to the real planner. Other temporal shapes and branch growth are not covered by that validation. Failed thresholds must remain failed evidence. |
| Resource-domain comparison | This finite campaign measures 1/2 CPUs and 4/8 GiB. Existing GENIE3 and Inferelator catalogs include 1/2/4/8-thread measurements. Four/eight-thread dynGENIE3 requests remain outside this campaign's calibrated resource domain. |
| Publication | Local image only. No registry/PyPI publication or merge to main. Upstream software redistribution licensing remains unresolved. |

The functional implementation follows the same catalog contracts and has
additional time-series checks. It is not fully calibrated merely because the
image or a small fixture works. The recovered campaign must pass structure,
declared-domain, independent prediction and final interface/dossier gates before
claiming calibration within its finite domain. This is integration validation,
not a claim that inferred biological edges are correct.
