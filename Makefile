PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python)
INFERENCE_WRAPPER_SCRIPTS := wrappers/inference_tools/scripts
SIMULATION_WRAPPER_SCRIPTS := wrappers/simulation_data_tools/scripts
PYTEST_FLAGS ?= -q
ARGS ?=
TOOL ?=
SIMULATOR ?=
WRAPPER ?= python

install:
	@$(PYTHON) -m pip install --upgrade pip
	@$(PYTHON) -m pip install -e .

install-dev-deps:
	@$(PYTHON) -m pip install --upgrade pip
	@$(PYTHON) -m pip install -e ".[dev]"

build:
	@$(PYTHON) -m pip install --upgrade build
	@$(PYTHON) -m build

clean:
	@find . -type d -name '.mypy_cache' -exec rm -rf {} +
	@find . -type d -name '.pytest_cache' -exec rm -rf {} +
	@find . -type d -name '__pycache__' -exec rm -rf {} +

black:
	@$(PYTHON) -m isort --profile black --skip-glob 'wrappers/**/repo/**' --skip-glob 'wrappers/**/papers/**' andrea wrappers tests
	@$(PYTHON) -m black --extend-exclude 'wrappers/.*/(repo|papers)/' andrea wrappers tests

build-tool-images:
	@$(PYTHON) $(INFERENCE_WRAPPER_SCRIPTS)/build_tool_images.py $(ARGS)

push-tool-images:
	@$(PYTHON) $(INFERENCE_WRAPPER_SCRIPTS)/sync_tool_images.py push $(ARGS)

pull-tool-images:
	@$(PYTHON) $(INFERENCE_WRAPPER_SCRIPTS)/sync_tool_images.py pull $(ARGS)

run-tool-smoketests:
	@$(PYTHON) $(INFERENCE_WRAPPER_SCRIPTS)/run_smoketests.py $(ARGS)

benchmark-tool-costs:
	@$(PYTHON) $(INFERENCE_WRAPPER_SCRIPTS)/benchmark_costs.py $(ARGS)

benchmark-simulator-costs:
	@$(PYTHON) $(SIMULATION_WRAPPER_SCRIPTS)/benchmark_costs.py $(ARGS)

run-simulator-smoketests:
	@$(PYTHON) $(SIMULATION_WRAPPER_SCRIPTS)/run_smoketests.py $(ARGS)

build-simulator-images:
	@$(PYTHON) $(SIMULATION_WRAPPER_SCRIPTS)/build_simulator_images.py $(ARGS)

push-simulator-images:
	@$(PYTHON) $(SIMULATION_WRAPPER_SCRIPTS)/sync_simulator_images.py push $(ARGS)

pull-simulator-images:
	@$(PYTHON) $(SIMULATION_WRAPPER_SCRIPTS)/sync_simulator_images.py pull $(ARGS)

list-simulator-images:
	@$(PYTHON) $(SIMULATION_WRAPPER_SCRIPTS)/sync_simulator_images.py list $(ARGS)

validate-toolspecs:
	@$(PYTHON) $(INFERENCE_WRAPPER_SCRIPTS)/validate_toolspecs.py $(ARGS)

validate-input-specs:
	@$(PYTHON) $(INFERENCE_WRAPPER_SCRIPTS)/validate_input_specs.py $(ARGS)

validate-smoketest-configs:
	@$(PYTHON) $(INFERENCE_WRAPPER_SCRIPTS)/validate_smoketest_configs.py $(ARGS)

validate-tool-costs:
	@$(PYTHON) $(INFERENCE_WRAPPER_SCRIPTS)/validate_tool_costs.py $(ARGS)

validate-simulatorspecs:
	@$(PYTHON) $(SIMULATION_WRAPPER_SCRIPTS)/validate_simulatorspecs.py $(ARGS)

validate-simulation-input-specs:
	@$(PYTHON) $(SIMULATION_WRAPPER_SCRIPTS)/validate_input_specs.py $(ARGS)

validate-simulator-smoketest-configs:
	@$(PYTHON) $(SIMULATION_WRAPPER_SCRIPTS)/validate_smoketest_configs.py $(ARGS)

validate-simulator-costs:
	@$(PYTHON) $(SIMULATION_WRAPPER_SCRIPTS)/validate_simulator_costs.py $(ARGS)

validate-inference-catalog:
	@$(MAKE) validate-toolspecs
	@$(MAKE) validate-input-specs
	@$(MAKE) validate-smoketest-configs
	@$(MAKE) validate-tool-costs

validate-generation-catalog:
	@$(MAKE) validate-simulation-input-specs
	@$(MAKE) validate-simulatorspecs
	@$(MAKE) validate-simulator-smoketest-configs
	@$(MAKE) validate-simulator-costs

scaffold-tool:
	@test -n "$(TOOL)" || (echo "Usage: make scaffold-tool TOOL=<tool_id> [WRAPPER=<language>] [ARGS='...']" && exit 2)
	@$(PYTHON) $(INFERENCE_WRAPPER_SCRIPTS)/scaffold_tool.py --tool $(TOOL) --wrapper $(WRAPPER) $(ARGS)

scaffold-simulator:
	@test -n "$(SIMULATOR)" || (echo "Usage: make scaffold-simulator SIMULATOR=<simulator_id> [WRAPPER=<language>] [ARGS='...']" && exit 2)
	@$(PYTHON) $(SIMULATION_WRAPPER_SCRIPTS)/scaffold_simulator.py --simulator $(SIMULATOR) --wrapper $(WRAPPER) $(ARGS)

prepare-tool-papers:
	@test -n "$(TOOL)" || (echo "Usage: make prepare-tool-papers TOOL=<tool_id> [ARGS='...']" && exit 2)
	@$(PYTHON) $(INFERENCE_WRAPPER_SCRIPTS)/prepare_tool_papers.py --tool $(TOOL) $(ARGS)

prepare-simulator-papers:
	@test -n "$(SIMULATOR)" || (echo "Usage: make prepare-simulator-papers SIMULATOR=<simulator_id> [ARGS='...']" && exit 2)
	@$(PYTHON) $(SIMULATION_WRAPPER_SCRIPTS)/prepare_simulator_papers.py --simulator $(SIMULATOR) $(ARGS)

verify-tool:
	@test -n "$(TOOL)" || (echo "Usage: make verify-tool TOOL=<tool_id> [ARGS='...']" && exit 2)
	@$(PYTHON) $(INFERENCE_WRAPPER_SCRIPTS)/validate_toolspecs.py --tool $(TOOL)
	@$(PYTHON) $(INFERENCE_WRAPPER_SCRIPTS)/validate_input_specs.py
	@$(PYTHON) $(INFERENCE_WRAPPER_SCRIPTS)/validate_smoketest_configs.py --tool $(TOOL)
	@$(PYTHON) $(INFERENCE_WRAPPER_SCRIPTS)/run_smoketests.py --tool $(TOOL) $(ARGS)

verify-simulator:
	@test -n "$(SIMULATOR)" || (echo "Usage: make verify-simulator SIMULATOR=<simulator_id> [ARGS='...']" && exit 2)
	@$(PYTHON) $(SIMULATION_WRAPPER_SCRIPTS)/validate_input_specs.py
	@$(PYTHON) $(SIMULATION_WRAPPER_SCRIPTS)/validate_simulatorspecs.py --simulator $(SIMULATOR)
	@$(PYTHON) $(SIMULATION_WRAPPER_SCRIPTS)/validate_smoketest_configs.py --simulator $(SIMULATOR)
	@$(PYTHON) $(SIMULATION_WRAPPER_SCRIPTS)/build_simulator_images.py --simulator $(SIMULATOR)
	@$(PYTHON) $(SIMULATION_WRAPPER_SCRIPTS)/run_smoketests.py --simulator $(SIMULATOR) --skip-build $(ARGS)

clone-tool-repos:
	@$(PYTHON) $(INFERENCE_WRAPPER_SCRIPTS)/sync_tool_repos.py clone $(ARGS)

clean-tool-repos:
	@$(PYTHON) $(INFERENCE_WRAPPER_SCRIPTS)/sync_tool_repos.py clean $(ARGS)

fetch-tool-publications:
	@$(PYTHON) $(INFERENCE_WRAPPER_SCRIPTS)/sync_tool_publications.py fetch $(ARGS)

clean-tool-publications:
	@$(PYTHON) $(INFERENCE_WRAPPER_SCRIPTS)/sync_tool_publications.py clean $(ARGS)

test-all:
	@$(PYTHON) -m pytest $(PYTEST_FLAGS) tests
