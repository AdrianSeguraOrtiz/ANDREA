# Installation

ANDREA is distributed as a Python package and executes simulator/inference
wrappers through Docker when a workflow needs to run external tools.

## Requirements

- Python `>=3.11,<3.14`
- Docker for `generate-data` and `infer-network` executions

The CLI can inspect reports and validate many schemas without Docker, but
running wrappers requires a working Docker installation.

## PyPI Installation

```sh
pip install ANDREA
andrea --help
```

## Development Installation

```sh
git clone https://github.com/AdrianSeguraOrtiz/ANDREA.git
cd ANDREA
python -m pip install -e ".[dev]"
andrea --help
```

When working inside the repository, the equivalent Makefile shortcut is:

```sh
make install-dev-deps
```

## Docker Images

Wrapper Docker images are not bundled in the wheel. Catalog specs record the
image expected for each simulator or inference tool. Depending on the workflow,
ANDREA may use a local image, pull an image, or rely on wrapper-development
commands to build images from `wrappers/`.

## Verifying An Installation

```sh
andrea --help
andrea generate-data --help
andrea infer-network --help
andrea evaluate-inference --help
andrea compare-networks --help
```

Developer validation and release checks are documented in
[development.md](development.md) and [release.md](release.md).
