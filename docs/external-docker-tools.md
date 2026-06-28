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

ANDREA runs the image with a thread count and mounted inputs. The image is
responsible for:

1. reading `expression.tsv`, `params.json`, `execution.json` and selected extras;
2. running the upstream method;
3. writing `/io/out/network.csv`;
4. optionally writing `/io/out/progress.json`, logs and native artifacts.

`network.csv` must use the standardized edge table expected by ANDREA. ANDREA
then validates, annotates, normalizes and merges the output with other selected
tools.

## GUI Form

The `infer-network` GUI includes `Add External Docker Tool`. The form asks for
the minimum information required to execute one temporary run:

- run ID;
- display name;
- Docker image name and tag;
- execution mode;
- selected Step 1 extras needed by the image;
- flat key-value image parameters.

The run is added to the same selected-run list as catalog tools. Runtime
parameters are written through the same `tools_params.json` mechanism used by
catalog integrations.

## Safety

External images run arbitrary code. Use only images you trust. ANDREA runs
custom inference images with Docker networking disabled.

## Formal Integration

If the tool is ready for broader use, use the GUI's `Request New Tool` flow or
open a GitHub issue/request with the tool description, image location and
expected input/output contract.
