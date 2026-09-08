# External Docker Tools

`infer-network` can run temporary external Docker images without adding them to
ANDREA's official catalog. This is intended for developers who want to compare a
work-in-progress method against catalog tools before requesting formal
integration.

## Standard Container Contract

The image must accept ANDREA's standard `/io` layout:

```text
/io/expression.tsv
/io/params.json
/io/execution.json
/io/extra/
/io/out/
```

ANDREA mounts `/io` read-only and mounts `/io/out` separately as read-write.
External images must not modify their inputs or create scratch files elsewhere
under `/io`; all temporary and final files must live below `/io/out`.

ANDREA runs the image with a thread count and the inputs declared for runtime
delivery. The image is responsible for:

1. reading `expression.tsv`, `params.json`, `execution.json` and selected extras;
2. running the upstream method;
3. writing `/io/out/network.csv` with the required columns
   `source,target,score,sign,evidence,context`;
4. optionally writing `/io/out/progress.json`, logs and native artifacts.

`network.csv` must use the standardized edge table expected by ANDREA. ANDREA
then validates, annotates, normalizes and merges the output with other selected
tools. A header-only file is a valid zero-edge result.

Contexts must agree with the physical child execution. `global` emits only
`global`; `group_native` emits `group:<id>`; and `column_native` emits
`column:<id>`. For `group_emulated`, ANDREA launches a child on each
group-filtered matrix, expects that child to emit `global`, then assigns the
logical `group:<id>` context itself. For `group_aggregated`, the child executes
the `column_native` contract and ANDREA derives the logical group networks from
its `column:<id>` rows. Contexts outside the planned set mark the run as failed
during merge.

## GUI Form

The `infer-network` GUI includes `Add External Docker Tool`. The form asks for
the minimum information required to execute one temporary run:

- run ID;
- display name;
- Docker image name and tag;
- execution mode;
- whether the returned network is directed and whether it carries signs;
- selected Step 1 extras read by the image at runtime;
- flat key-value image parameters.

The run is added to the same selected-run list as catalog tools. Runtime
parameters are written through the same `tools_params.json` mechanism used by
catalog integrations.

The equivalent `custom_tools.json` fields are explicit and portable:

```json
{
  "tools": [
    {
      "run_id": "spathi_01",
      "name": "SPATHI",
      "docker_image": "example/spathi:0.1.0",
      "execution_mode": "group_native",
      "extra_inputs": ["tf_list", "groups"],
      "outputs": {"directed": true, "sign": "none"},
      "runtime_resources": {
        "threading": {
          "supported": true,
          "default_threads": 1,
          "max_threads": null,
          "upstream_mapping": "cli:--threads"
        }
      }
    }
  ]
}
```

The matching `tools_params.json` entry uses the derived tool ID explicitly:

```json
{
  "runs": [
    {
      "run_id": "spathi_01",
      "tool_id": "custom_spathi_01",
      "params": {},
      "execution": {"mode": "group_native"},
      "resources": {"threads": 8, "ram_gb": 16, "cpuset_cpus": [0, 1, 2, 3, 4, 5, 6, 7]}
    }
  ]
}
```

The `custom_tools.json` root contains exactly `tools`, and every tool entry must
contain exactly the seven fields shown above. `name` must be non-empty and
`extra_inputs` must be present even when its value is `[]`. String values are
canonical: surrounding whitespace is rejected, and enum values such as
`execution_mode` and `outputs.sign` are case-sensitive. ANDREA does not insert
missing fields while loading or freezing this file.

`runtime_resources.threading` is mandatory. It declares whether `--threads`
may exceed one, its default and intrinsic maximum accepted value, and how the
wrapper maps it to the upstream runtime. `max_threads: null` means that a
multithreaded tool has no intrinsic limit; the run remains bounded by the
explicit ANDREA resource budget. A single-threaded image must declare both
counts as `1`; ANDREA never assumes or silently forces one thread.

`run_id` must match `[A-Za-z0-9][A-Za-z0-9._-]*` exactly; it is never slugified
or otherwise repaired.

The matching external `tools_params.json` run must provide both `run_id` and
`tool_id`. An external definition and its request are one-to-one: the
matching `tools_params.json` entry must use the same `run_id` and
cannot reuse the definition under another logical run ID. ANDREA always
prepends the literal `custom_` to the complete definition `run_id`; an existing
`custom_` prefix is not special. Therefore `run_id: "spathi_01"` requires the
pair `run_id: "spathi_01"`, `tool_id: "custom_spathi_01"`, while `run_id:
"custom_spathi_01"` would derive `tool_id: "custom_custom_spathi_01"`.
Unprefixed or differently named aliases are rejected.
The request uses the normal `runs` array with `run_id`, `tool_id`, `params`,
`execution` and optional operational `resources`. `resources.threads` and
`resources.ram_gb` are exact allocations, not scientific parameters. If a
resource is omitted, the planner selects it from the ToolSpec/cost profile;
external tools without a cost profile otherwise use the documented conservative
fallback. An explicit `ram_gb` is applied as the Docker memory limit to every
physical child of the logical run and must fit the global planning budget.
Optional `cpuset_cpus` requires an
explicit thread count, must be a strictly increasing list with at least that
many available logical CPUs, and is applied to Docker without repair or
clamping.

`outputs` is required and must contain exactly `directed` and `sign`.
`directed` is a boolean; `sign` accepts `none`, `signed` or `mixed`. There are
no inferred defaults: an incomplete or unknown output contract blocks the
external tool during preflight.

The declared sign contract is checked against the generated `network.csv`:

- `none` requires `sign=?` on every edge;
- `signed` requires `sign=+` or `sign=-` on every edge;
- `mixed` accepts signed and unsigned edges.

A contradiction marks that tool execution as failed instead of silently
evaluating it under different semantics.

### Runtime inputs versus orchestration inputs

`extra_inputs` names only standardized files that the external image reads from
`/io/extra`. A `group_native` definition must declare exactly one source for
its output group identities: `"groups"` or `"column_phenotypes"`. The SPATHI
example above uses `"groups"`.

For `group_emulated` and `group_aggregated`, do not include `"groups"` in
`extra_inputs`. Selecting either mode already makes `groups.tsv` a required
dataset input, but ANDREA alone reads it to split expression columns or
aggregate column-level networks. It is deliberately not mounted in the child
container. The GUI shows `groups` as managed by ANDREA and disables its runtime
checkbox in these two modes; hand-written JSON that declares it is rejected.

For example, an emulated tool that reads only a TF list uses:

```json
{
  "run_id": "emulated_01",
  "name": "Emulated method",
  "docker_image": "example/emulated:1.0",
  "execution_mode": "group_emulated",
  "extra_inputs": ["tf_list"],
  "outputs": {"directed": true, "sign": "none"},
  "runtime_resources": {
    "threading": {
      "supported": false,
      "default_threads": 1,
      "max_threads": 1,
      "upstream_mapping": "cli:--threads"
    }
  }
}
```

## Downstream Evaluation

`infer-network` freezes the effective output capabilities for every catalog or
external run in `run_report.json`. Consequently, `evaluate-inference` can score
an external tool directly from an inference analysis bundle; the tool does not
need to be installed in ANDREA's catalog. Freezing also makes evaluation
independent of later catalog changes. The frozen `output_capabilities` map must
match the selected run IDs exactly and is the sole capability source used by
evaluation; neither the current catalog nor `custom_tools.json` is consulted as
a fallback.

The report also freezes `tools.completed_contexts` for each completed logical
run. It includes successful zero-edge contexts and excludes failed
`group_emulated` children. Evaluation materializes missing zero-edge pairings
from this inventory; it never guesses them from the truth manifest.

## Safety

External images run arbitrary code. Use only images you trust. ANDREA runs
custom inference images with Docker networking disabled.

## Formal Integration

If the tool is ready for broader use, use the GUI's `Request New Tool` flow or
open a GitHub issue/request with the tool description, image location and
expected input/output contract.
