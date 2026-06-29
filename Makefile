PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python)
INFERENCE_WRAPPER_SCRIPTS := wrappers/inference_tools/scripts
SIMULATION_WRAPPER_SCRIPTS := wrappers/simulation_data_tools/scripts
PYTEST_FLAGS ?= -q
ARGS ?=
TOOL ?=
SIMULATOR ?=
WRAPPER ?= python
PACKAGE_NAME ?= ANDREA
PACKAGE_VERSION ?= $(shell $(PYTHON) -c "from andrea.config import __version__; print(__version__)")
TESTPYPI_REPOSITORY_URL ?= https://test.pypi.org/legacy/
PYPI_REPOSITORY_URL ?= https://upload.pypi.org/legacy/
TESTPYPI_INDEX_URL ?= https://test.pypi.org/simple/
PYPI_INDEX_URL ?= https://pypi.org/simple/

install:
	@$(PYTHON) -m pip install --upgrade pip
	@$(PYTHON) -m pip install -e .

install-dev-deps:
	@$(PYTHON) -m pip install --upgrade pip
	@$(PYTHON) -m pip install -e ".[dev]"

build:
	@$(PYTHON) -m pip install --upgrade build
	@$(PYTHON) -m build

build-package:
	@$(PYTHON) -m pip install --upgrade build
	@rm -rf dist
	@$(PYTHON) -m build

check-package:
	@$(PYTHON) -m pip install --upgrade twine
	@$(PYTHON) -m twine check dist/*

check-package-version:
	@actual=$$($(PYTHON) -c "from andrea.config import __version__; print(__version__)"); \
	if [ "$$actual" != "$(PACKAGE_VERSION)" ]; then \
		echo "PACKAGE_VERSION=$(PACKAGE_VERSION) does not match andrea.config.__version__=$$actual"; \
		exit 2; \
	fi

check-dist:
	@test -d dist || (echo "dist/ does not exist. Run 'make build-package' first." && exit 2)

check-twine-credentials:
	@test -n "$(TWINE_USERNAME)" || (echo "Set TWINE_USERNAME=__token__" && exit 2)
	@test -n "$(TWINE_PASSWORD)" || (echo "Set TWINE_PASSWORD=<pypi-token>" && exit 2)

smoke-wheel:
	@$(MAKE) check-dist
	@tmp_dir=$$(mktemp -d); \
	trap 'rm -rf "$$tmp_dir"' EXIT; \
	$(PYTHON) -m venv "$$tmp_dir/venv"; \
	"$$tmp_dir/venv/bin/python" -m pip install --upgrade pip; \
	"$$tmp_dir/venv/bin/python" -m pip install dist/*.whl; \
	"$$tmp_dir/venv/bin/andrea" --help >/dev/null; \
	echo "Wheel smoke test passed."

smoke-testpypi:
	@tmp_dir=$$(mktemp -d); \
	trap 'rm -rf "$$tmp_dir"' EXIT; \
	$(PYTHON) -m venv "$$tmp_dir/venv"; \
	"$$tmp_dir/venv/bin/python" -m pip install --upgrade pip; \
	"$$tmp_dir/venv/bin/python" -m pip install \
		--index-url "$(TESTPYPI_INDEX_URL)" \
		--extra-index-url "$(PYPI_INDEX_URL)" \
		"$(PACKAGE_NAME)==$(PACKAGE_VERSION)"; \
	"$$tmp_dir/venv/bin/andrea" --help >/dev/null; \
	echo "TestPyPI smoke test passed for $(PACKAGE_NAME)==$(PACKAGE_VERSION)."

smoke-pypi:
	@tmp_dir=$$(mktemp -d); \
	trap 'rm -rf "$$tmp_dir"' EXIT; \
	$(PYTHON) -m venv "$$tmp_dir/venv"; \
	"$$tmp_dir/venv/bin/python" -m pip install --upgrade pip; \
	"$$tmp_dir/venv/bin/python" -m pip install \
		--index-url "$(PYPI_INDEX_URL)" \
		"$(PACKAGE_NAME)==$(PACKAGE_VERSION)"; \
	"$$tmp_dir/venv/bin/andrea" --help >/dev/null; \
	echo "PyPI smoke test passed for $(PACKAGE_NAME)==$(PACKAGE_VERSION)."

publish-testpypi: check-dist check-twine-credentials
	@$(PYTHON) -m pip install --upgrade twine
	@TWINE_USERNAME="$(TWINE_USERNAME)" TWINE_PASSWORD="$(TWINE_PASSWORD)" \
		$(PYTHON) -m twine upload --non-interactive \
		--repository-url "$(TESTPYPI_REPOSITORY_URL)" dist/*

publish-pypi: check-dist check-twine-credentials
	@$(PYTHON) -m pip install --upgrade twine
	@TWINE_USERNAME="$(TWINE_USERNAME)" TWINE_PASSWORD="$(TWINE_PASSWORD)" \
		$(PYTHON) -m twine upload --non-interactive \
		--repository-url "$(PYPI_REPOSITORY_URL)" dist/*

publish-testpypi-full: check-package-version
	@$(MAKE) build-package
	@$(MAKE) check-package
	@$(MAKE) smoke-wheel
	@$(MAKE) publish-testpypi PACKAGE_VERSION="$(PACKAGE_VERSION)" TWINE_USERNAME="$(TWINE_USERNAME)" TWINE_PASSWORD="$(TWINE_PASSWORD)"
	@$(MAKE) smoke-testpypi PACKAGE_NAME="$(PACKAGE_NAME)" PACKAGE_VERSION="$(PACKAGE_VERSION)"

publish-pypi-full: check-package-version
	@$(MAKE) build-package
	@$(MAKE) check-package
	@$(MAKE) smoke-wheel
	@$(MAKE) publish-pypi PACKAGE_VERSION="$(PACKAGE_VERSION)" TWINE_USERNAME="$(TWINE_USERNAME)" TWINE_PASSWORD="$(TWINE_PASSWORD)"

clean:
	@find . -type d -name '.mypy_cache' -exec rm -rf {} +
	@find . -type d -name '.pytest_cache' -exec rm -rf {} +
	@find . -type d -name '__pycache__' -exec rm -rf {} +

render-doc-assets:
	@$(PYTHON) scripts/render_doc_assets.py

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
	@$(PYTHON) $(SIMULATION_WRAPPER_SCRIPTS)/validate_simulator_costs.py --require $(ARGS)

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
