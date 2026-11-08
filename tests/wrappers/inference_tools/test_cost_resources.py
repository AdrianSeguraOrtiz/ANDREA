"""Cost admission control without containers or native model executions."""

from __future__ import annotations

import copy
import json
import multiprocessing
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "wrappers/inference_tools/scripts"))
from shared.cost_resources import (
    GIB,
    ResourcePool,
    ResourceStateError,
    UnsatisfiableResources,
    default_state_dir,
    discover_resources,
)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def capabilities():
    return {
        "affinity_cpus": list(range(8)),
        "effective_cpus": list(range(8)),
        "cpu_quota_cores": None,
        "cpu_capacity": 8,
        "smt_groups": [[0, 4], [1, 5], [2, 6], [3, 7]],
        "host_memory_total_bytes": 64 * GIB,
        "host_memory_available_bytes": 64 * GIB,
        "memory_total_bytes": 64 * GIB,
        "memory_available_bytes": 64 * GIB,
        "cgroup_limits": [],
        "isolation_limit": "Visible topology, not physical isolation",
    }


def pool(path, **kwargs):
    return ResourcePool(
        path, ram_headroom_gb=0, ram_headroom_fraction=0, probe=capabilities, **kwargs
    )


def test_v2_intersects_affinity_cpuset_ancestor_quotas_and_memory(tmp_path):
    proc, system = tmp_path / "proc", tmp_path / "sys"
    write(
        proc / "meminfo",
        f"MemTotal: {16 * GIB // 1024} kB\nMemAvailable: {12 * GIB // 1024} kB\n",
    )
    write(proc / "self/cgroup", "0::/parent/child\n")
    write(proc / "self/mountinfo", "1 0 0:1 / /sys/fs/cgroup rw - cgroup2 cgroup rw\n")
    group = system / "fs/cgroup"
    write(group / "parent/child/cpuset.cpus.effective", "2-5")
    write(group / "parent/cpuset.cpus.effective", "1-6")
    write(group / "parent/child/cpu.max", "200000 100000")
    write(group / "parent/cpu.max", "150000 100000")
    write(group / "cpu.max", "max 100000")
    for folder, maximum, current in (
        (group / "parent", 10, 3),
        (group / "parent/child", 8, 2),
    ):
        write(folder / "memory.max", str(maximum * GIB))
        write(folder / "memory.current", str(current * GIB))
    for cpu in range(2, 6):
        write(
            system / f"devices/system/cpu/cpu{cpu}/topology/thread_siblings_list",
            f"{cpu},{cpu + 4}",
        )
    actual = discover_resources(proc_root=proc, sys_root=system, affinity=set(range(8)))
    assert actual["effective_cpus"] == [2, 3, 4, 5]
    assert actual["cpu_quota_cores"] == 1.5
    assert actual["cpu_capacity"] == 1
    assert actual["memory_total_bytes"] == 8 * GIB
    assert actual["memory_available_bytes"] == 6 * GIB
    assert actual["smt_groups"] == [[2, 6], [3, 7], [4, 8], [5, 9]]
    assert "VM" in actual["isolation_limit"]


def test_v1_inherited_cpuset_and_finite_memory_quota(tmp_path):
    proc, system = tmp_path / "proc", tmp_path / "sys"
    write(
        proc / "meminfo",
        f"MemTotal: {16 * GIB // 1024} kB\nMemAvailable: {12 * GIB // 1024} kB\n",
    )
    write(
        proc / "self/cgroup",
        "2:cpu,cpuacct:/parent/child\n3:memory:/parent/child\n4:cpuset:/parent/child\n",
    )
    write(
        proc / "self/mountinfo",
        "\n".join(
            f"{index} 0 0:{index} / /sys/fs/cgroup/{controller} rw - cgroup cgroup rw,{controller}"
            for index, controller in enumerate(("cpu", "memory", "cpuset"), 1)
        ),
    )
    group = system / "fs/cgroup"
    write(group / "cpuset/parent/child/cpuset.cpus", "")
    write(group / "cpuset/parent/cpuset.cpus", "0-3")
    write(group / "cpu/parent/child/cpu.cfs_quota_us", "200000")
    write(group / "cpu/parent/child/cpu.cfs_period_us", "100000")
    write(group / "cpu/parent/cpu.cfs_quota_us", "100000")
    write(group / "cpu/parent/cpu.cfs_period_us", "100000")
    write(group / "memory/parent/child/memory.limit_in_bytes", str(4 * GIB))
    write(group / "memory/parent/child/memory.usage_in_bytes", str(GIB))
    actual = discover_resources(proc_root=proc, sys_root=system, affinity={1, 2, 3, 4})
    assert actual["effective_cpus"] == [1, 2, 3]
    assert actual["cpu_capacity"] == 1
    assert actual["memory_total_bytes"] == 4 * GIB
    assert actual["memory_available_bytes"] == 3 * GIB


def test_missing_memavailable_or_malformed_quota_cannot_admit(tmp_path):
    proc, system = tmp_path / "proc", tmp_path / "sys"
    write(proc / "meminfo", "MemTotal: 1000 kB\n")
    with pytest.raises(ResourceStateError, match="discover"):
        discover_resources(proc_root=proc, sys_root=system, affinity={0})
    write(proc / "meminfo", "MemTotal: 1000 kB\nMemAvailable: 500 kB\n")
    write(proc / "self/cgroup", "0::/\n")
    write(proc / "self/mountinfo", "1 0 0:1 / /sys/fs/cgroup rw - cgroup2 cgroup rw\n")
    write(system / "fs/cgroup/cpu.max", "broken")
    with pytest.raises(ResourceStateError, match="discover"):
        discover_resources(proc_root=proc, sys_root=system, affinity={0})


def test_exact_requests_spread_cores_and_block_visible_siblings(tmp_path):
    first_pool, second_pool = pool(tmp_path), pool(tmp_path)
    first = first_pool.reserve(2, 8)
    second = second_pool.reserve(2, 8)
    assert first.cpus == (0, 1)
    assert second.cpus == (2, 3)
    assert set(first.context["blocked_cpus"]).isdisjoint(second.context["blocked_cpus"])
    assert first_pool.reserve(1, 1) is None
    assert first.context["ram_gb"] == 8
    assert first.context["execution_policy"]["cpu_quota_cores"] is None
    assert "live_snapshot" in first.context
    first.release()
    again = first_pool.reserve(1, 1)
    assert again.cpus == (0,)
    again.release()
    second.release()
    assert first_pool.snapshot()["leases"] == {}


def test_headroom_and_user_caps_do_not_shrink_or_double_deduct_caps(tmp_path):
    broker = ResourcePool(
        tmp_path,
        ram_headroom_gb=4,
        ram_headroom_fraction=0.1,
        cpu_headroom=1,
        max_threads=4,
        max_ram_gb=32,
        probe=capabilities,
    )
    snapshot = broker.snapshot()
    assert snapshot["cpu_budget"] == 4
    assert snapshot["ram_budget_bytes"] == 32 * GIB
    with pytest.raises(UnsatisfiableResources, match="pending"):
        broker.reserve(5, 1)
    with pytest.raises(UnsatisfiableResources):
        broker.reserve(1, 33)
    with broker.reserve(4, 32) as lease:
        assert len(lease.cpus) == 4 and lease.context["ram_gb"] == 32
        assert broker.reserve(1, 1) is None


def test_ram_reservations_and_dynamic_external_pressure_wait_without_fake_results(
    tmp_path,
):
    live = capabilities()
    broker = ResourcePool(
        tmp_path,
        ram_headroom_gb=0,
        ram_headroom_fraction=0,
        probe=lambda: copy.deepcopy(live),
    )
    first = broker.reserve(1, 40)
    assert broker.reserve(1, 25) is None
    live["memory_available_bytes"] = 42 * GIB
    assert broker.reserve(1, 3) is None
    first.release()
    live["memory_available_bytes"] = GIB
    assert broker.reserve(1, 2) is None
    assert broker.snapshot()["leases"] == {}


def test_active_stricter_global_budget_is_respected_by_other_campaign(tmp_path):
    limited, broad = pool(tmp_path, max_threads=2, max_ram_gb=16), pool(tmp_path)
    first = limited.reserve(1, 8)
    assert broad.reserve(2, 8) is None
    assert broad.reserve(1, 9) is None
    second = broad.reserve(1, 8)
    first.release()
    second.release()


def test_active_larger_headroom_is_kept_for_other_campaigns(tmp_path):
    live = capabilities()
    strict = ResourcePool(
        tmp_path,
        ram_headroom_gb=32,
        ram_headroom_fraction=0,
        probe=lambda: copy.deepcopy(live),
    )
    broad = pool(tmp_path)
    first = strict.reserve(1, 1)
    # External processes consume memory after the first reservation. Another
    # campaign's lower headroom must not invalidate that active policy.
    live["memory_available_bytes"] = 40 * GIB
    broad._probe = lambda: copy.deepcopy(live)
    assert broad.reserve(1, 20) is None
    first.release()


def test_context_manager_releases_after_exception_and_release_is_idempotent(tmp_path):
    broker = pool(tmp_path)
    lease = broker.reserve(1, 1)
    with pytest.raises(RuntimeError, match="worker exited"):
        with lease:
            raise RuntimeError("worker exited and was cleaned")
    lease.release()
    assert broker.snapshot()["leases"] == {}


def test_orphan_or_reused_pid_blocks_without_reclaiming(tmp_path):
    broker = pool(tmp_path)
    lease = broker.reserve(1, 1)
    state = json.loads(broker.state_path.read_text())
    state["leases"][lease.lease_id]["owner"]["start_ticks"] = "different-start"
    broker.state_path.write_text(json.dumps(state))
    before = broker.state_path.read_bytes()
    assert broker.snapshot()["orphaned_leases"] == [lease.lease_id]
    with pytest.raises(ResourceStateError, match="verified worker/container cleanup"):
        broker.reserve(1, 1)
    assert broker.state_path.read_bytes() == before


@pytest.mark.parametrize("state", ["{broken", "[]", '{"leases": {"x": {}}}'])
def test_bad_state_is_never_replaced_with_empty_capacity(tmp_path, state):
    broker = pool(tmp_path)
    broker.state_path.write_text(state)
    with pytest.raises(ResourceStateError, match="registry"):
        broker.reserve(1, 1)
    assert broker.state_path.read_text() == state


def compete(directory, start, finish, output):
    broker = pool(Path(directory))
    start.wait(5)
    lease = broker.reserve(4, 8)
    output.put(None if lease is None else {"cpus": lease.cpus, "id": lease.lease_id})
    if lease is not None:
        finish.wait(5)
        lease.release()


def abandon(directory, output):
    lease = pool(Path(directory)).reserve(1, 1)
    output.put(lease.lease_id)
    # Simulate a coordinator exiting without proving container/worker cleanup.


def test_dead_owner_does_not_authorize_reclaim_of_possible_surviving_worker(tmp_path):
    context = multiprocessing.get_context("fork")
    output = context.Queue()
    process = context.Process(target=abandon, args=(str(tmp_path), output))
    process.start()
    lease_id = output.get(timeout=5)
    process.join(timeout=5)
    assert process.exitcode == 0
    broker = pool(tmp_path)
    assert broker.snapshot()["orphaned_leases"] == [lease_id]
    with pytest.raises(ResourceStateError, match="cleanup"):
        broker.reserve(1, 1)
    assert lease_id in broker.snapshot()["leases"]


def test_two_processes_atomically_compete_for_same_cpu_groups(tmp_path):
    context = multiprocessing.get_context("fork")
    start, finish, output = context.Event(), context.Event(), context.Queue()
    processes = [
        context.Process(target=compete, args=(str(tmp_path), start, finish, output))
        for _ in range(2)
    ]
    try:
        for process in processes:
            process.start()
        start.set()
        results = [output.get(timeout=5), output.get(timeout=5)]
        assert sum(result is not None for result in results) == 1
        assert sum(result is None for result in results) == 1
        assert len(pool(tmp_path).snapshot()["leases"]) == 1
    finally:
        finish.set()
        for process in processes:
            process.join(timeout=5)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
    assert all(process.exitcode == 0 for process in processes)
    assert pool(tmp_path).snapshot()["leases"] == {}


def test_default_state_directory_is_shared_across_campaigns(tmp_path, monkeypatch):
    first, second = tmp_path / "first-campaign", tmp_path / "second-campaign"
    first.mkdir()
    second.mkdir()
    monkeypatch.chdir(first)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(first))
    first_broker = default_state_dir()
    monkeypatch.chdir(second)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(second))
    assert default_state_dir() == first_broker
    assert first_broker.is_absolute()
    assert "cost-resources" in first_broker.parts
