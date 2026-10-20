"""Exercise thread policy before the interpreter starts, including inherited env."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "wrappers/inference_tools/scripts"
spec = importlib.util.spec_from_file_location("thread_entrypoint_builder", SCRIPTS / "build_tool_images.py")
builder = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = builder
spec.loader.exec_module(builder)

KEYS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS", "BLIS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")


@pytest.mark.parametrize("runtime", ["python", "r"])
@pytest.mark.parametrize("policy,expected", [("assigned", "4"), ("single", "1")])
def test_limits_exist_at_interpreter_entry(tmp_path, runtime, policy, expected):
    # This executable stands in for the interpreter, so even its first action
    # must see the limits. No NumPy/R import inside a wrapper can be early enough.
    interpreter = tmp_path / builder.RUNTIME_ENTRYPOINTS[runtime][0]
    interpreter.write_text(
        f"#!{sys.executable}\nimport json,os,sys\n"
        f"print(json.dumps({{'env':{{k:os.environ.get(k) for k in {KEYS!r}}},'args':sys.argv[1:]}}))\n"
    )
    interpreter.chmod(0o755)
    app = tmp_path / "dummy"
    app.touch()
    text = builder.render_entrypoint(runtime, policy).replace(
        builder.RUNTIME_ENTRYPOINTS[runtime][1], str(app)
    )
    script = tmp_path / "entrypoint.sh"
    script.write_text(text)
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}")
    env.update({key: "77" for key in KEYS})
    result = subprocess.run(
        ["bash", str(script), "--input", "input", "--params", "params", "--extra", "extra",
         "--output-dir", str(tmp_path / "out"), "--threads", "4"],
        env=env, capture_output=True, text=True, check=True,
    )
    report = json.loads(result.stdout)
    assert report["env"] == {key: expected for key in KEYS}
    assert report["args"][-2:] == ["--threads", "4"]  # outer worker allocation is retained


def test_catalog_policies_distinguish_native_and_worker_parallelism():
    mapping = builder.load_json(SCRIPTS / "template_map.json")
    configs = builder.validate_template_map(mapping, {name: Path(name) for name in mapping["tools"]})
    for name in ["cespgrn", "scgenerai", "metasem", "scregulate"]:
        assert configs[name].native_threads == "assigned"
    for name in ["clr", "infercsn", "lioness", "ppcor", "tigress", "genie3", "grnboost2"]:
        assert configs[name].native_threads == "single"
    # An unmapped policy must fail instead of silently issuing an ineffective image.
    mapping["tools"]["cespgrn"]["native_threads"] = "automatic"
    with pytest.raises(RuntimeError, match="native_threads"):
        builder.validate_template_map(mapping, {name: Path(name) for name in mapping["tools"]})


def test_fingerprint_includes_rendered_policy(tmp_path):
    sys.path.insert(0, str(ROOT / "wrappers"))
    from benchmark_support import build_context_fingerprint
    import shutil

    scripts = tmp_path / "wrappers/inference_tools/scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(SCRIPTS / "build_tool_images.py", scripts)
    tool = tmp_path / "wrappers/inference_tools/tools/probe"
    tool.mkdir(parents=True)
    (tool / "Dockerfile").write_text("FROM scratch\nCOPY run_tool.sh /app/run_tool.sh\n")
    mapping = {"bundle_templates": {}, "tools": {"probe": {
        "runtime": "python", "bundles": [], "native_threads": "assigned"}}}
    path = scripts / "template_map.json"
    path.write_text(json.dumps(mapping))
    assigned = build_context_fingerprint(root=tmp_path, kind="inference", identifier="probe")
    mapping["tools"]["probe"]["native_threads"] = "single"
    path.write_text(json.dumps(mapping))
    assert build_context_fingerprint(root=tmp_path, kind="inference", identifier="probe") != assigned
