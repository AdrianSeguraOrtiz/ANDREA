"""Real-container regressions for SynTReN's auxiliary expression-scale RNG."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
CONFIGS = ROOT / "wrappers/simulation_data_tools/tests/smoketest_configs"
pytestmark = pytest.mark.skipif(
    os.environ.get("ANDREA_RUN_DOCKER_SMOKETESTS") != "1",
    reason="Set ANDREA_RUN_DOCKER_SMOKETESTS=1 to run actual Docker integrations",
)


def _run(config, directory, seed, image):
    request = json.loads(json.dumps(config["request"]))
    request.update(simulator_id="syntren", seed=seed)
    directory.mkdir(parents=True)
    out = directory / "out"
    out.mkdir()
    args = ["docker", "run", "--rm", "--network", "none", "--cpus", "1",
            "--memory", "8g", "--user", f"{os.getuid()}:{os.getgid()}"]
    request["mounted_inputs"] = {}
    for name, relative in request.pop("inputs", {}).items():
        source = (CONFIGS / relative).resolve()
        target = f"/work/inputs/{name}/{source.name}"
        args += ["-v", f"{source}:{target}:ro"]
        request["mounted_inputs"][name] = target
    request_path = directory / "request.json"
    request_path.write_text(json.dumps(request))
    args += ["-v", f"{request_path}:/work/request/simulator-run-request.json:ro",
             "-v", f"{out}:/work/out", image]
    result = subprocess.run(args, capture_output=True, text=True, timeout=180)
    (directory / "stdout.log").write_text(result.stdout)
    (directory / "stderr.log").write_text(result.stderr)
    assert result.returncode == 0, result.stderr
    for relative in config["required_files"]:
        assert (out / relative).is_file(), relative
    from andrea.core.commands.generate_data.output_validation import validate_simulator_output_package
    manifest = json.loads((out / "simulator-output-manifest.json").read_text())
    validate_simulator_output_package(stage_dir=out, dataset_id="syntren-seed-test",
        data_axes=request["data_axes"], truth_requirements=request["truth_requirements"],
        simulator_manifest=manifest)
    return out


@pytest.mark.parametrize("name", ["syntren_bulk_observational_global",
    "syntren_bulk_perturbational_global", "syntren_custom_inputs_perturbational_global"])
def test_seed_controls_expression_without_changing_upstream_model(tmp_path, name):
    config = json.loads((CONFIGS / f"{name}.json").read_text())
    if name == "syntren_bulk_observational_global":
        config["request"]["params"]["sampling"]["num_experiments"] = 20
    evidence = os.environ.get("ANDREA_SYNTREN_TEST_EVIDENCE")
    root = Path(evidence).resolve() / name if evidence else tmp_path
    image = "adriansegura99/simulator_syntren:1.1.1"
    seed = config["request"]["seed"]
    outputs = [_run(config, root / f"repeat-{i}", seed, image) for i in range(3)]
    scientific = [p.relative_to(outputs[0]) for p in outputs[0].rglob("*")
                  if p.is_file() and (p.parts[-2] in {"truth", "extras", "native"}
                                     or p.name == "expression.tsv")]
    assert scientific
    for relative in scientific:
        assert len({hashlib.sha256((out / relative).read_bytes()).hexdigest()
                    for out in outputs}) == 1, str(relative)
    changed = _run(config, root / "different-seed", seed + 17, image)
    assert (changed / "expression.tsv").read_bytes() != (outputs[0] / "expression.tsv").read_bytes()
    legacy = _run(config, root / "legacy-model", seed, "adriansegura99/simulator_syntren:1.1.0")
    def without_unseeded_scales(out):
        text = (out / "provenance/raw/syntren_output/generated.xml").read_text()
        return re.sub(r"<maxExpression>[^<]+</maxExpression>", "<maxExpression/>", text)
    # Topology, kinetics, and the complete native RNG states must be unchanged.
    assert without_unseeded_scales(legacy) == without_unseeded_scales(outputs[0])
