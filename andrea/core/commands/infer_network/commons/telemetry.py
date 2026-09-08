"""Physical-task timing and container resource telemetry for inference."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from andrea.core.shared.container_runtime import run_cmd

from .resources import parse_linux_cpuset


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _timestamp_delta_seconds(
    started_at: str | None,
    finished_at: str | None,
) -> float | None:
    if not started_at or not finished_at:
        return None
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        finished = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if started.tzinfo is None or finished.tzinfo is None or finished < started:
        return None
    return round((finished - started).total_seconds(), 6)


def directory_size_bytes(root: Path) -> int:
    """Return the exact size of regular files below ``root``."""

    if not root.exists():
        return 0
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except OSError:
            # A wrapper may atomically replace a progress file while it exits.
            continue
    return total


def _read_int(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
        return int(raw)
    except (OSError, ValueError):
        return None


def _read_key_values(path: Path) -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        fields = line.split()
        if len(fields) != 2:
            continue
        try:
            values[fields[0]] = int(fields[1])
        except ValueError:
            continue
    return values


def _docker_inspect(container_id: str) -> dict[str, Any] | None:
    result = run_cmd(["docker", "inspect", container_id])
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout or "")
    except json.JSONDecodeError:
        return None
    if (
        not isinstance(payload, list)
        or len(payload) != 1
        or not isinstance(payload[0], dict)
    ):
        return None
    return payload[0]


def _cgroup_memberships(pid: int) -> tuple[str | None, dict[str, str]]:
    unified: str | None = None
    v1_memberships: dict[str, str] = {}
    try:
        lines = Path(f"/proc/{pid}/cgroup").read_text(encoding="utf-8").splitlines()
    except OSError:
        return unified, v1_memberships
    for line in lines:
        fields = line.split(":", 2)
        if len(fields) != 3:
            continue
        _hierarchy, controllers, relative = fields
        if not controllers:
            unified = relative
            continue
        for controller in controllers.split(","):
            if controller:
                v1_memberships[controller] = relative
    return unified, v1_memberships


def _process_effective_cpuset(pid: int) -> tuple[int, ...] | None:
    """Read the kernel-reported affinity of the container's init process."""

    try:
        lines = Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        key, separator, value = line.partition(":")
        if separator and key == "Cpus_allowed_list":
            try:
                return parse_linux_cpuset(value.strip())
            except ValueError:
                return None
    return None


@dataclass
class ContainerTelemetrySampler:
    """Collect cgroup counters while measuring the enclosing physical task."""

    container_id: str
    assigned_threads: int
    assigned_ram_gb: float
    requested_cpuset_cpus: tuple[int, ...] | None
    task_started_monotonic_ns: int
    task_started_at_utc: str
    cgroup_root: Path = Path("/sys/fs/cgroup")
    container_started_at_utc: str | None = None
    configured_cpuset_cpus: tuple[int, ...] | None = None
    effective_cpuset_cpus: tuple[int, ...] | None = None
    effective_cpu_quota_cores: float | None = None
    effective_ram_limit_bytes: int | None = None
    _v2_path: Path | None = None
    _v1_paths: dict[str, Path] = field(default_factory=dict)
    _cpu_time_seconds: float | None = None
    _peak_memory_bytes: int | None = None
    _throttled_time_seconds: float | None = None
    _oom_events: int | None = None
    _oom_kill_events: int | None = None
    _memory_limit_events: int | None = None
    _cpu_source: str | None = None
    _memory_source: str | None = None

    @classmethod
    def attach(
        cls,
        *,
        container_id: str,
        assigned_threads: int,
        assigned_ram_gb: float,
        requested_cpuset_cpus: tuple[int, ...] | None,
        task_started_monotonic_ns: int,
        task_started_at_utc: str,
    ) -> ContainerTelemetrySampler:
        sampler = cls(
            container_id=container_id,
            assigned_threads=int(assigned_threads),
            assigned_ram_gb=float(assigned_ram_gb),
            requested_cpuset_cpus=requested_cpuset_cpus,
            task_started_monotonic_ns=int(task_started_monotonic_ns),
            task_started_at_utc=task_started_at_utc,
        )
        inspect = _docker_inspect(container_id)
        if inspect is None:
            if requested_cpuset_cpus is not None:
                raise RuntimeError(
                    "Docker inspection failed; requested CPU affinity cannot be verified"
                )
            return sampler
        state = inspect.get("State")
        host_config = inspect.get("HostConfig")
        if isinstance(state, dict):
            started = state.get("StartedAt")
            if isinstance(started, str) and started and not started.startswith("0001-"):
                sampler.container_started_at_utc = started
            pid = state.get("Pid")
        else:
            pid = None
        if isinstance(host_config, dict):
            cpuset = host_config.get("CpusetCpus")
            if isinstance(cpuset, str) and cpuset.strip():
                try:
                    sampler.configured_cpuset_cpus = parse_linux_cpuset(cpuset)
                except ValueError as exc:
                    raise RuntimeError(
                        f"Docker returned an invalid configured CPU set: {cpuset!r}"
                    ) from exc
            nano_cpus = host_config.get("NanoCpus")
            if isinstance(nano_cpus, int) and nano_cpus > 0:
                sampler.effective_cpu_quota_cores = nano_cpus / 1_000_000_000.0
            else:
                cpu_quota = host_config.get("CpuQuota")
                cpu_period = host_config.get("CpuPeriod")
                if (
                    isinstance(cpu_quota, int)
                    and cpu_quota > 0
                    and isinstance(cpu_period, int)
                    and cpu_period > 0
                ):
                    sampler.effective_cpu_quota_cores = cpu_quota / cpu_period
            memory = host_config.get("Memory")
            if isinstance(memory, int) and memory > 0:
                sampler.effective_ram_limit_bytes = memory
        if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
            unified, v1_memberships = _cgroup_memberships(pid)
            if unified is not None:
                candidate = sampler.cgroup_root / unified.lstrip("/")
                if candidate.is_dir():
                    sampler._v2_path = candidate
                    effective_cpuset = None
                    try:
                        effective_cpuset = (
                            (candidate / "cpuset.cpus.effective")
                            .read_text(encoding="utf-8")
                            .strip()
                        )
                    except OSError:
                        pass
                    if effective_cpuset:
                        try:
                            sampler.effective_cpuset_cpus = parse_linux_cpuset(
                                effective_cpuset
                            )
                        except ValueError:
                            pass
            for controller, relative in v1_memberships.items():
                candidates = [sampler.cgroup_root / controller / relative.lstrip("/")]
                if controller in {"cpu", "cpuacct"}:
                    candidates.extend(
                        sampler.cgroup_root / combined / relative.lstrip("/")
                        for combined in ("cpu,cpuacct", "cpuacct,cpu")
                    )
                for candidate in candidates:
                    if candidate.is_dir():
                        sampler._v1_paths[controller] = candidate
                        break
            if sampler.effective_cpuset_cpus is None:
                cpuset_path = sampler._v1_paths.get("cpuset")
                if cpuset_path is not None:
                    for filename in ("cpuset.effective_cpus", "cpuset.cpus"):
                        try:
                            effective_cpuset = (
                                (cpuset_path / filename)
                                .read_text(encoding="utf-8")
                                .strip()
                            )
                        except OSError:
                            continue
                        if effective_cpuset:
                            try:
                                sampler.effective_cpuset_cpus = parse_linux_cpuset(
                                    effective_cpuset
                                )
                            except ValueError:
                                pass
                            break
            if sampler.effective_cpuset_cpus is None:
                sampler.effective_cpuset_cpus = _process_effective_cpuset(pid)
        if requested_cpuset_cpus is not None:
            expected = tuple(requested_cpuset_cpus)
            if sampler.configured_cpuset_cpus != expected:
                raise RuntimeError(
                    "Docker did not preserve the requested CPU affinity: "
                    f"requested={list(expected)}, "
                    f"configured={list(sampler.configured_cpuset_cpus or ())}"
                )
            if sampler.effective_cpuset_cpus is None:
                raise RuntimeError(
                    "The container's effective CPU affinity could not be measured"
                )
            if sampler.effective_cpuset_cpus != expected:
                raise RuntimeError(
                    "The container's effective CPU affinity differs from the request: "
                    f"requested={list(expected)}, "
                    f"effective={list(sampler.effective_cpuset_cpus)}"
                )
        sampler.sample()
        return sampler

    def sample(self) -> None:
        if self._v2_path is not None:
            cpu = _read_key_values(self._v2_path / "cpu.stat")
            if "usage_usec" in cpu:
                self._cpu_time_seconds = cpu["usage_usec"] / 1_000_000.0
                self._cpu_source = "cgroup_v2_cpu.stat.usage_usec"
            if "throttled_usec" in cpu:
                self._throttled_time_seconds = cpu["throttled_usec"] / 1_000_000.0
            peak = _read_int(self._v2_path / "memory.peak")
            current = _read_int(self._v2_path / "memory.current")
            observed = peak if peak is not None else current
            if observed is not None:
                self._peak_memory_bytes = max(self._peak_memory_bytes or 0, observed)
                self._memory_source = (
                    "cgroup_v2_memory.peak"
                    if peak is not None
                    else "cgroup_v2_memory.current_sampled_lower_bound"
                )
            events = _read_key_values(self._v2_path / "memory.events")
            if "oom" in events:
                self._oom_events = events["oom"]
            if "oom_kill" in events:
                self._oom_kill_events = events["oom_kill"]
            if "max" in events:
                self._memory_limit_events = events["max"]
            return

        cpu_path = self._v1_paths.get("cpuacct") or self._v1_paths.get("cpu")
        if cpu_path is not None:
            cpu_ns = _read_int(cpu_path / "cpuacct.usage")
            if cpu_ns is not None:
                self._cpu_time_seconds = cpu_ns / 1_000_000_000.0
                self._cpu_source = "cgroup_v1_cpuacct.usage"
        memory_path = self._v1_paths.get("memory")
        if memory_path is not None:
            peak = _read_int(memory_path / "memory.max_usage_in_bytes")
            current = _read_int(memory_path / "memory.usage_in_bytes")
            observed = peak if peak is not None else current
            if observed is not None:
                self._peak_memory_bytes = max(self._peak_memory_bytes or 0, observed)
                self._memory_source = (
                    "cgroup_v1_memory.max_usage_in_bytes"
                    if peak is not None
                    else "cgroup_v1_memory.usage_in_bytes_sampled_lower_bound"
                )
            failcnt = _read_int(memory_path / "memory.failcnt")
            if failcnt is not None:
                # memory.failcnt counts limit hits, not OOM kills. Keep those
                # semantics separate instead of presenting every failed charge
                # as a killed container.
                self._memory_limit_events = failcnt

    def finish(
        self,
        *,
        task_finished_monotonic_ns: int,
        task_finished_at_utc: str,
        output_dir: Path,
    ) -> dict[str, Any]:
        self.sample()
        inspect = _docker_inspect(self.container_id)
        state = inspect.get("State") if isinstance(inspect, dict) else None
        finished_at = None
        oom_killed = bool((self._oom_kill_events or 0) > 0)
        if isinstance(state, dict):
            raw_finished = state.get("FinishedAt")
            if (
                isinstance(raw_finished, str)
                and raw_finished
                and not raw_finished.startswith("0001-")
            ):
                finished_at = raw_finished
            oom_killed = oom_killed or state.get("OOMKilled") is True
        available = sum(
            value is not None
            for value in (self._cpu_time_seconds, self._peak_memory_bytes)
        )
        status = (
            "complete" if available == 2 else "partial" if available else "unavailable"
        )
        container_wall_time_seconds = _timestamp_delta_seconds(
            self.container_started_at_utc,
            finished_at,
        )
        return {
            "schema_version": "1.0",
            "scope": "physical_task",
            "wall_time_seconds": round(
                max(0, task_finished_monotonic_ns - self.task_started_monotonic_ns)
                / 1_000_000_000.0,
                6,
            ),
            "container_wall_time_seconds": container_wall_time_seconds,
            "cpu_time_seconds": (
                None
                if self._cpu_time_seconds is None
                else round(self._cpu_time_seconds, 6)
            ),
            "peak_memory_bytes": self._peak_memory_bytes,
            "output_bytes": directory_size_bytes(output_dir),
            "io_read_bytes": None,
            "io_write_bytes": None,
            "task_started_at_utc": self.task_started_at_utc,
            "task_finished_at_utc": task_finished_at_utc,
            "task_started_monotonic_ns": self.task_started_monotonic_ns,
            "task_finished_monotonic_ns": int(task_finished_monotonic_ns),
            "container_started_at_utc": self.container_started_at_utc,
            "container_finished_at_utc": finished_at,
            "assigned_resources": {
                "threads": self.assigned_threads,
                "ram_limit_bytes": round(self.assigned_ram_gb * 1024**3),
                "cpuset_cpus_requested": (
                    list(self.requested_cpuset_cpus)
                    if self.requested_cpuset_cpus is not None
                    else None
                ),
                "cpuset_cpus_configured": (
                    list(self.configured_cpuset_cpus)
                    if self.configured_cpuset_cpus is not None
                    else None
                ),
                "cpuset_cpus_effective": (
                    list(self.effective_cpuset_cpus)
                    if self.effective_cpuset_cpus is not None
                    else None
                ),
                "cpu_quota_cores_effective": self.effective_cpu_quota_cores,
                "ram_limit_bytes_effective": self.effective_ram_limit_bytes,
            },
            "telemetry": {
                "status": status,
                "wall_source": "andrea_monotonic_clock",
                "wall_semantics": (
                    "physical_task_end_to_end_including_container_launch_and_"
                    "exit_collection"
                ),
                "container_wall_source": (
                    "docker_state_timestamps"
                    if container_wall_time_seconds is not None
                    else None
                ),
                "cpu_source": self._cpu_source,
                "memory_source": self._memory_source,
                "memory_semantics": (
                    "container_cgroup_peak"
                    if self._memory_source
                    and "sampled_lower_bound" not in self._memory_source
                    else "sampled_container_memory_lower_bound"
                    if self._memory_source
                    else "unavailable"
                ),
                "io_source": None,
                "io_semantics": "unavailable",
                "throttled_time_seconds": self._throttled_time_seconds,
                "oom_events": self._oom_events,
                "oom_kill_events": self._oom_kill_events,
                "memory_limit_events": self._memory_limit_events,
                "oom_killed": oom_killed,
            },
        }


def not_started_measurement(
    *,
    threads: int,
    ram_gb: float,
    requested_cpuset_cpus: tuple[int, ...] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "scope": "physical_task",
        "wall_time_seconds": 0.0,
        "container_wall_time_seconds": None,
        "cpu_time_seconds": None,
        "peak_memory_bytes": None,
        "output_bytes": 0,
        "io_read_bytes": None,
        "io_write_bytes": None,
        "task_started_at_utc": None,
        "task_finished_at_utc": None,
        "task_started_monotonic_ns": None,
        "task_finished_monotonic_ns": None,
        "container_started_at_utc": None,
        "container_finished_at_utc": None,
        "assigned_resources": {
            "threads": int(threads),
            "ram_limit_bytes": round(float(ram_gb) * 1024**3),
            "cpuset_cpus_requested": (
                list(requested_cpuset_cpus)
                if requested_cpuset_cpus is not None
                else None
            ),
            "cpuset_cpus_configured": None,
            "cpuset_cpus_effective": None,
            "cpu_quota_cores_effective": None,
            "ram_limit_bytes_effective": None,
        },
        "telemetry": {
            "status": "not_started",
            "wall_source": None,
            "wall_semantics": "not_started",
            "container_wall_source": None,
            "cpu_source": None,
            "memory_source": None,
            "memory_semantics": "unavailable",
            "io_source": None,
            "io_semantics": "unavailable",
            "throttled_time_seconds": None,
            "oom_events": None,
            "oom_kill_events": None,
            "memory_limit_events": None,
            "oom_killed": False,
        },
    }
