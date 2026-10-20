"""Recovery must target only the campaign's original worker process group."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('cost_campaign_under_test',
    ROOT / 'wrappers/inference_tools/scripts/run_cost_coverage.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_recovery_terminates_verified_worker_only():
    command = [sys.executable, '-c', 'import time; time.sleep(60)']
    worker = subprocess.Popen(command, start_new_session=True)
    try:
        record = module.process_record(worker.pid, command)
        changed_identity = {**record, 'start_ticks': str(int(record['start_ticks']) + 1)}
        assert not module.terminate_recorded_child(changed_identity)
        assert worker.poll() is None
        assert module.terminate_recorded_child(record)
        assert worker.wait(timeout=3) != 0
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait()


def test_recovery_rejects_different_command():
    command = [sys.executable, '-c', 'import time; time.sleep(60)']
    worker = subprocess.Popen(command, start_new_session=True)
    try:
        record = module.process_record(worker.pid, command)
        assert not module.terminate_recorded_child({**record, 'command': ['unrelated']})
        assert worker.poll() is None
    finally:
        worker.kill()
        worker.wait()
