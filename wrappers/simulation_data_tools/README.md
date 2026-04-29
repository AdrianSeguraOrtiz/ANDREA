# simulation_data_tools

Wrapper and maintenance tooling for the packaged simulator catalog in `andrea/catalog_simulation_data_tools/`.

Use this area for:
- validating SimulatorSpecs
- validating simulator smoketest configs
- building simulator Docker images
- pushing/pulling simulator Docker images
- running simulator smoketests
- scaffolding new simulator integrations
- extracting local simulator paper PDFs
- keeping smoketest parameter overrides for small, fast runs

Runtime catalog assets consumed by `andrea generate-data` live in `andrea/catalog_simulation_data_tools/`.
Simulator build sources live in `wrappers/simulation_data_tools/simulators/`.
Smoketest parameter overrides live in `wrappers/simulation_data_tools/param_overrides/`.

## Layout

```text
wrappers/simulation_data_tools/
  param_overrides/
    <simulator_id>.json  # optional, merged before per-config smoketest params
  simulators/
    <simulator_id>/
      Dockerfile
      run_simulator.py | run_simulator.R | run_simulator.sh
      repo/             # optional local clone of simulatorspec.implementation_url
      papers/           # optional local publication cache
  scripts/
    shared/
    scaffold_simulator.py
    prepare_simulator_papers.py
    validate_simulatorspecs.py
    validate_smoketest_configs.py
    build_simulator_images.py
    sync_simulator_images.py
    run_smoketests.py
  SIMULATOR_INTEGRATION_PLAYBOOK.md
  tests/
    schemas/
      smoketest.config.schema.json
    smoketest_configs/
      <simulator_id>_*.json
```

```text
andrea/catalog_simulation_data_tools/
  schemas/
  simulators/
    <simulator_id>/
      simulatorspec.json
```

## Scripts

```bash
python wrappers/simulation_data_tools/scripts/validate_simulatorspecs.py
python wrappers/simulation_data_tools/scripts/validate_smoketest_configs.py
python wrappers/simulation_data_tools/scripts/build_simulator_images.py --list
python wrappers/simulation_data_tools/scripts/run_smoketests.py --list
```

`run_smoketests.py` builds the simulator image unless `--skip-build` is provided.
Smoketest params are resolved from `param_overrides/<simulator_id>.json` plus each config's `request.params`, with the config taking precedence.

## Make Targets

```bash
make validate-simulatorspecs
make validate-simulator-smoketest-configs
make build-simulator-images ARGS="--list"
make run-simulator-smoketests ARGS="--list"
make verify-simulator SIMULATOR=dyngen ARGS="--skip-build"
```

## Adding A Simulator

1. Run `make scaffold-simulator SIMULATOR=<simulator_id>`.
2. Place upstream evidence under `wrappers/simulation_data_tools/simulators/<simulator_id>/repo/` and local papers under `papers/`.
3. Follow `wrappers/simulation_data_tools/SIMULATOR_INTEGRATION_PLAYBOOK.md`.
4. Keep expensive smoketest defaults in `param_overrides/<simulator_id>.json`; keep scenario-specific deltas in `tests/smoketest_configs/`.
