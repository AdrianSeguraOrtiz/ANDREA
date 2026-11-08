"""Conservative, cooperating CPU/RAM leases for cost measurements on Linux.

The caller must enforce ``lease.cpus`` and its requested RAM on the worker, then
wait for verified worker/container cleanup before releasing. This is admission
control between campaigns sharing one state directory, not host isolation.
Visible SMT topology, especially in a VM, cannot prove physical exclusivity.
Orphaned leases deliberately require external, verified cleanup; never reclaim
one merely because its owner died while a container might still be running.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Callable, Iterator
import uuid

GIB = 1024**3


class ResourceStateError(RuntimeError):
    """Discovery or shared state cannot safely authorize a new measurement."""


class UnsatisfiableResources(ValueError):
    """The unchanged request exceeds this executor's effective capacity."""


def default_state_dir() -> Path:
    """Common per-user, per-boot broker, independent of campaign directories."""
    boot = _text(Path("/proc/sys/kernel/random/boot_id"))
    if not boot or not re.fullmatch(r"[a-zA-Z0-9-]+", boot):
        raise ResourceStateError("Cannot identify this host boot for the shared broker")
    return Path.home() / ".cache" / "andrea" / "cost-resources" / boot


def _text(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ResourceStateError(
            f"Cannot read resource capability {path}: {exc}"
        ) from exc


def _cpu_list(value: str) -> set[int]:
    cpus: set[int] = set()
    for part in filter(None, value.split(",")):
        bounds = part.split("-")
        if len(bounds) not in (1, 2):
            raise ValueError(f"Invalid CPU list: {value}")
        first, last = int(bounds[0]), int(bounds[-1])
        if first < 0 or last < first:
            raise ValueError(f"Invalid CPU list: {value}")
        cpus.update(range(first, last + 1))
    return cpus


def _unescape(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), value)


def _cgroup_directories(proc_root: Path, sys_root: Path):
    """Resolve this process's v1/v2 hierarchies, including inherited limits."""
    memberships = {}
    for line in (_text(proc_root / "self/cgroup") or "").splitlines():
        _, controllers, group = line.split(":", 2)
        for controller in controllers.split(","):
            memberships[controller] = Path(group)
    for line in (_text(proc_root / "self/mountinfo") or "").splitlines():
        before, separator, after = line.partition(" - ")
        if not separator:
            continue
        details = after.split()
        if details[0] not in {"cgroup", "cgroup2"}:
            continue
        fields = before.split()
        mount_root = Path(_unescape(fields[3]))
        mount = Path(_unescape(fields[4]))
        if mount.is_relative_to("/sys"):
            mount = sys_root / mount.relative_to("/sys")
        controllers = {""} if details[0] == "cgroup2" else set(details[2].split(","))
        for controller in controllers & memberships.keys():
            group = memberships[controller]
            if ".." in group.parts:
                raise ValueError("Cgroup membership contains parent traversal")
            if group.is_relative_to(mount_root):
                relative = group.relative_to(mount_root)
            elif group == Path("/"):
                # A cgroup namespace can expose its delegated mount root as '/'.
                relative = Path(".")
            else:
                raise ValueError(f"Cannot map cgroup {group} under {mount_root}")
            current = mount / relative
            while True:
                yield details[0], controller, current
                if current == mount:
                    break
                current = current.parent


def discover_resources(
    *,
    proc_root: Path = Path("/proc"),
    sys_root: Path = Path("/sys"),
    affinity: set[int] | None = None,
) -> dict[str, Any]:
    """Read visible affinity, SMT, inherited cgroup quotas and available RAM.

    CPU capacity floors fractional quotas: a measurement requiring one full
    CPU is not admitted into half a CPU of quota. Memory availability is the
    minimum of MemAvailable and every finite ancestor's limit minus usage.
    No Docker daemon calls, resource changes or worker executions are made.
    """
    try:
        allowed = set(os.sched_getaffinity(0) if affinity is None else affinity)
        original_affinity = sorted(allowed)
        if not allowed or any(
            isinstance(cpu, bool) or not isinstance(cpu, int) or cpu < 0
            for cpu in allowed
        ):
            raise ValueError("No valid effective CPU affinity")
        meminfo = {}
        for line in (_text(proc_root / "meminfo") or "").splitlines():
            key, _, value = line.partition(":")
            if key in {"MemTotal", "MemAvailable"}:
                amount, unit = value.split()
                if unit != "kB":
                    raise ValueError("Unsupported meminfo unit")
                meminfo[key] = int(amount) * 1024
        total, available = meminfo["MemTotal"], meminfo["MemAvailable"]
        if total <= 0 or not 0 <= available <= total:
            raise ValueError("Invalid MemTotal/MemAvailable")
        quota_cores = []
        memory_limits, cgroup_limits = [], []
        seen = set()
        for version, controller, directory in _cgroup_directories(proc_root, sys_root):
            for name in (
                "cpuset.cpus.effective",
                "cpuset.effective_cpus",
                "cpuset.cpus",
            ):
                if version == "cgroup2" or controller == "cpuset":
                    cpuset = _text(directory / name)
                    if cpuset:
                        allowed &= _cpu_list(cpuset)
                        break
            if version == "cgroup2" or controller == "cpu":
                if version == "cgroup2":
                    raw = _text(directory / "cpu.max")
                    quota, period = raw.split() if raw is not None else ("max", "1")
                else:
                    quota = _text(directory / "cpu.cfs_quota_us") or "-1"
                    period = _text(directory / "cpu.cfs_period_us")
                if quota not in {"max", "-1"}:
                    cores = int(quota) / int(period)
                    if cores <= 0:
                        raise ValueError("Nonpositive cgroup CPU quota")
                    quota_cores.append(cores)
                    cgroup_limits.append(
                        {"path": str(directory), "cpu_quota_cores": cores}
                    )
            if (
                version == "cgroup2" or controller == "memory"
            ) and directory not in seen:
                seen.add(directory)
                limit_file, current_file = (
                    ("memory.max", "memory.current")
                    if version == "cgroup2"
                    else ("memory.limit_in_bytes", "memory.usage_in_bytes")
                )
                raw_limit = _text(directory / limit_file)
                if raw_limit is not None and raw_limit != "max":
                    limit = int(raw_limit)
                    current = int(_text(directory / current_file))
                    if limit <= 0 or current < 0:
                        raise ValueError("Invalid cgroup memory limit/usage")
                    memory_limits.append((limit, max(0, limit - current)))
                    cgroup_limits.append(
                        {
                            "path": str(directory),
                            "memory_max_bytes": limit,
                            "memory_current_bytes": current,
                        }
                    )
        if not allowed:
            raise ValueError("Affinity and cgroup cpuset have empty intersection")
        groups: list[set[int]] = []
        for cpu in sorted(allowed):
            siblings = _text(
                sys_root / f"devices/system/cpu/cpu{cpu}/topology/thread_siblings_list"
            )
            group = _cpu_list(siblings) | {cpu} if siblings else {cpu}
            overlaps = [other for other in groups if group & other]
            for other in overlaps:
                group |= other
                groups.remove(other)
            groups.append(group)
        quota = min(quota_cores) if quota_cores else None
        return {
            "affinity_cpus": original_affinity,
            "effective_cpus": sorted(allowed),
            "cpu_quota_cores": quota,
            "cpu_capacity": (
                min(len(allowed), math.floor(quota))
                if quota is not None
                else len(allowed)
            ),
            "smt_groups": sorted([sorted(group) for group in groups]),
            "host_memory_total_bytes": total,
            "host_memory_available_bytes": available,
            "memory_total_bytes": min([total] + [limit for limit, _ in memory_limits]),
            "memory_available_bytes": min(
                [available] + [remaining for _, remaining in memory_limits]
            ),
            "cgroup_limits": cgroup_limits,
            "isolation_limit": "Visible CPU topology only; physical exclusivity, VM scheduling, cache, memory bandwidth, I/O, NUMA, power and external workloads are not isolated.",
        }
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        ZeroDivisionError,
    ) as exc:
        raise ResourceStateError(
            f"Cannot safely discover cost resources: {exc}"
        ) from exc


def _owner() -> dict[str, Any]:
    try:
        pid = os.getpid()
        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        boot = _text(Path("/proc/sys/kernel/random/boot_id"))
        if not boot:
            raise ValueError("Missing boot identity")
        return {"pid": pid, "start_ticks": stat[19], "boot_id": boot}
    except (OSError, ValueError, IndexError) as exc:
        raise ResourceStateError(
            f"Cannot establish lease owner identity: {exc}"
        ) from exc


def _alive(owner: dict[str, Any]) -> bool:
    try:
        stat = Path(f"/proc/{owner['pid']}/stat").read_text().rsplit(")", 1)[1].split()
        return (
            stat[19] == owner["start_ticks"]
            and stat[0] != "Z"
            and owner["boot_id"]
            == Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        )
    except FileNotFoundError:
        return False
    except (OSError, ValueError, IndexError) as exc:
        raise ResourceStateError(f"Cannot verify lease owner identity: {exc}") from exc


@dataclass
class ResourceLease:
    lease_id: str
    cpus: tuple[int, ...]
    context: dict[str, Any]
    _pool: ResourcePool
    _released: bool = False

    def release(self) -> None:
        """Release only after the caller has confirmed worker/container exit."""
        if not self._released:
            self._pool._release(self.lease_id)
            self._released = True

    def __enter__(self) -> ResourceLease:
        return self

    def __exit__(self, *_: Any) -> None:
        self.release()


class ResourcePool:
    """Atomic admission broker shared by cooperating cost campaigns.

    Requests never shrink. ``None`` means temporarily busy/low available RAM;
    ``UnsatisfiableResources`` means the unchanged point cannot fit capacity.
    A stale lease blocks admissions with ``ResourceStateError`` until verified
    cleanup, because its native process/container could outlive the owner.
    Reservations conservatively subtract their full RAM from live availability,
    including already resident memory; this can underuse RAM but covers leases
    whose workers have not yet started. This does not reserve RAM in the kernel.
    """

    def __init__(
        self,
        state_dir: Path,
        *,
        ram_headroom_gb: float = 1.0,
        ram_headroom_fraction: float = 0.1,
        cpu_headroom: int = 0,
        max_threads: int | None = None,
        max_ram_gb: float | None = None,
        probe: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        for name, value in (
            ("ram_headroom_gb", ram_headroom_gb),
            ("ram_headroom_fraction", ram_headroom_fraction),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"{name} must be finite and nonnegative")
        if ram_headroom_fraction >= 1:
            raise ValueError("ram_headroom_fraction must be below 1")
        if (
            isinstance(cpu_headroom, bool)
            or not isinstance(cpu_headroom, int)
            or cpu_headroom < 0
        ):
            raise ValueError("cpu_headroom must be a nonnegative integer")
        if max_threads is not None and (
            isinstance(max_threads, bool)
            or not isinstance(max_threads, int)
            or max_threads < 1
        ):
            raise ValueError("max_threads must be a positive integer")
        if max_ram_gb is not None:
            self._request(1, max_ram_gb)
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_path = self.state_dir / "leases.json"
        self._probe = probe or discover_resources
        self._identity = _owner()
        self._options = {
            "ram_headroom_gb": ram_headroom_gb,
            "ram_headroom_fraction": ram_headroom_fraction,
            "cpu_headroom": cpu_headroom,
            "max_threads": max_threads,
            "max_ram_gb": max_ram_gb,
        }

    @staticmethod
    def _request(threads: int, ram_gb: float) -> int:
        if isinstance(threads, bool) or not isinstance(threads, int) or threads < 1:
            raise ValueError("threads must be a positive integer")
        if (
            isinstance(ram_gb, bool)
            or not isinstance(ram_gb, (int, float))
            or not math.isfinite(ram_gb)
            or ram_gb <= 0
        ):
            raise ValueError("ram_gb must be finite and positive")
        return math.ceil(ram_gb * GIB)

    @contextmanager
    def _locked(self) -> Iterator[dict[str, Any]]:
        with (self.state_dir / "leases.lock").open("a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                try:
                    if self.state_path.is_symlink():
                        raise ResourceStateError("Lease state must not be a symlink")
                    state = (
                        json.loads(self.state_path.read_text())
                        if self.state_path.exists()
                        else {"leases": {}}
                    )
                    if not isinstance(state, dict) or not isinstance(
                        state.get("leases"), dict
                    ):
                        raise ValueError("Expected lease registry object")
                    allocated: set[int] = set()
                    for lease_id, row in state["leases"].items():
                        if (
                            not isinstance(lease_id, str)
                            or not isinstance(row, dict)
                            or not isinstance(row["ram_bytes"], int)
                            or isinstance(row["ram_bytes"], bool)
                            or self._request(row["threads"], row["ram_bytes"] / GIB)
                            != row["ram_bytes"]
                            or not isinstance(row["cpus"], list)
                            or not isinstance(row["blocked_cpus"], list)
                            or len(row["cpus"]) != row["threads"]
                            or not set(row["cpus"]) <= set(row["blocked_cpus"])
                            or any(
                                isinstance(cpu, bool)
                                or not isinstance(cpu, int)
                                or cpu < 0
                                for cpu in row["blocked_cpus"] + row["cpus"]
                            )
                            or len(set(row["cpus"])) != len(row["cpus"])
                            or isinstance(row["owner"]["pid"], bool)
                            or not isinstance(row["owner"]["pid"], int)
                            or row["owner"]["pid"] <= 0
                            or not isinstance(row["owner"]["start_ticks"], str)
                            or not isinstance(row["owner"]["boot_id"], str)
                            or isinstance(row["cpu_budget"], bool)
                            or not isinstance(row["cpu_budget"], int)
                            or row["cpu_budget"] < row["threads"]
                            or not isinstance(row["ram_budget_bytes"], int)
                            or isinstance(row["ram_budget_bytes"], bool)
                            or not isinstance(row["ram_headroom_bytes"], int)
                            or isinstance(row["ram_headroom_bytes"], bool)
                            or row["ram_headroom_bytes"] < 0
                            or row["ram_budget_bytes"] < row["ram_bytes"]
                        ):
                            raise ValueError("Malformed lease record")
                        if allocated & set(row["blocked_cpus"]):
                            raise ValueError("Overlapping visible CPU/SMT leases")
                        allocated.update(row["blocked_cpus"])
                except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
                    raise ResourceStateError(
                        f"Cannot safely use cost lease registry: {exc}"
                    ) from exc
                yield state
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _save(self, state: dict[str, Any]) -> None:
        descriptor, name = tempfile.mkstemp(prefix=".leases-", dir=self.state_dir)
        try:
            with os.fdopen(descriptor, "w") as handle:
                json.dump(state, handle, sort_keys=True, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.state_path)
            directory = os.open(self.state_dir, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        capabilities = self._probe()
        options = self._options
        headroom = math.ceil(
            max(
                options["ram_headroom_gb"] * GIB,
                options["ram_headroom_fraction"] * capabilities["memory_total_bytes"],
            )
        )
        cpu_budget = max(0, capabilities["cpu_capacity"] - options["cpu_headroom"])
        ram_budget = max(0, capabilities["memory_total_bytes"] - headroom)
        if options["max_threads"] is not None:
            cpu_budget = min(cpu_budget, options["max_threads"])
        if options["max_ram_gb"] is not None:
            ram_budget = min(ram_budget, math.floor(options["max_ram_gb"] * GIB))
        return {
            "capabilities": capabilities,
            "cpu_budget": cpu_budget,
            "ram_budget_bytes": ram_budget,
            "ram_headroom_bytes": headroom,
            "available_ram_bytes": max(
                0, capabilities["memory_available_bytes"] - headroom
            ),
            "leases": state["leases"],
            "orphaned_leases": [
                key for key, row in state["leases"].items() if not _alive(row["owner"])
            ],
            "policy": {
                **options,
                "smt_policy": "Reserve every visible SMT sibling of selected CPUs",
                "memory_policy": "Subtract complete active reservations from live available RAM; no kernel reservation",
            },
        }

    def snapshot(self) -> dict[str, Any]:
        with self._locked() as state:
            return self._snapshot(state)

    def reserve(self, threads: int, ram_gb: float) -> ResourceLease | None:
        requested_ram = self._request(threads, ram_gb)
        if _owner() != self._identity:
            raise ResourceStateError(
                "A forked process must construct its own ResourcePool"
            )
        with self._locked() as state:
            snapshot = self._snapshot(state)
            if snapshot["orphaned_leases"]:
                raise ResourceStateError(
                    f"Orphaned cost leases require verified worker/container cleanup: {snapshot['orphaned_leases']}"
                )
            if (
                threads > snapshot["cpu_budget"]
                or requested_ram > snapshot["ram_budget_bytes"]
            ):
                raise UnsatisfiableResources(
                    f"Unchanged request {threads} threads/{ram_gb:g} GiB exceeds effective budget {snapshot['cpu_budget']} threads/{snapshot['ram_budget_bytes'] / GIB:g} GiB; leave this point pending"
                )
            active = list(state["leases"].values())
            used_threads = sum(row["threads"] for row in active)
            reserved_ram = sum(row["ram_bytes"] for row in active)
            global_cpu = min(
                [snapshot["cpu_budget"]] + [row["cpu_budget"] for row in active]
            )
            global_ram = min(
                [snapshot["ram_budget_bytes"]]
                + [row["ram_budget_bytes"] for row in active]
            )
            global_headroom = max(
                [snapshot["ram_headroom_bytes"]]
                + [row["ram_headroom_bytes"] for row in active]
            )
            blocked = {cpu for row in active for cpu in row["blocked_cpus"]}
            capabilities = snapshot["capabilities"]
            free = set(capabilities["effective_cpus"]) - blocked
            groups = [sorted(set(group) & free) for group in capabilities["smt_groups"]]
            # Spread threads over distinct visible cores before using siblings.
            ordered = [group[0] for group in groups if group]
            ordered.extend(cpu for group in groups for cpu in group[1:])
            if (
                used_threads + threads > global_cpu
                or len(ordered) < threads
                or reserved_ram + requested_ram > global_ram
                or reserved_ram + requested_ram
                > max(0, capabilities["memory_available_bytes"] - global_headroom)
            ):
                return None
            cpus = tuple(ordered[:threads])
            reserved_cpus = sorted(
                {
                    cpu
                    for group in capabilities["smt_groups"]
                    if set(group) & set(cpus)
                    for cpu in group
                }
            )
            policy = {
                **snapshot["policy"],
                "cpu_budget": snapshot["cpu_budget"],
                "ram_budget_bytes": snapshot["ram_budget_bytes"],
                **{
                    key: capabilities[key]
                    for key in (
                        "affinity_cpus",
                        "effective_cpus",
                        "cpu_quota_cores",
                        "smt_groups",
                        "memory_total_bytes",
                        "isolation_limit",
                    )
                },
            }
            context = {
                "cpus": list(cpus),
                "blocked_cpus": reserved_cpus,
                "threads": threads,
                "ram_gb": ram_gb,
                "execution_policy": policy,
                "live_snapshot": {
                    key: value
                    for key, value in snapshot.items()
                    if key not in {"leases", "policy"}
                },
            }
            lease_id = uuid.uuid4().hex
            state["leases"][lease_id] = {
                "owner": self._identity,
                "cpus": list(cpus),
                "blocked_cpus": reserved_cpus,
                "threads": threads,
                "ram_bytes": requested_ram,
                "cpu_budget": snapshot["cpu_budget"],
                "ram_budget_bytes": snapshot["ram_budget_bytes"],
                "ram_headroom_bytes": snapshot["ram_headroom_bytes"],
                "created_at_unix": time.time(),
                "context": context,
            }
            self._save(state)
            return ResourceLease(lease_id, cpus, context, self)

    def _release(self, lease_id: str) -> None:
        if _owner() != self._identity:
            raise ResourceStateError("Only the lease owner can release its resources")
        with self._locked() as state:
            row = state["leases"].get(lease_id)
            if row is None:
                return
            if row["owner"] != self._identity:
                raise ResourceStateError("Lease owner identity changed")
            del state["leases"][lease_id]
            self._save(state)
