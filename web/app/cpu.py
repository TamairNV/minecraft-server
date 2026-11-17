"""CPU use like htop: per thread for the whole machine, plus the Minecraft container.

/proc/stat is host-wide even inside the container. The Minecraft container runs in its own
cgroup slice (cgroup_parent: minecraft.slice in docker-compose.yml) and the host's cgroup tree is
mounted read-only, so its cpu.stat gives the exact CPU time used, the same number htop shows
(100% = one thread busy). Crafty's own figure is sampled badly (often 0) and isn't used.
"""
import os
import threading
import time

MC_CGROUP = os.environ.get("MC_CGROUP_CPU", "/host/cgroup/minecraft.slice/cpu.stat")


def read_stat(path: str = "/proc/stat") -> list[tuple[int, int, int]]:
    """(user, system, total) jiffies for each cpuN line, in order. Nice counts as user, iowait as idle."""
    out = []
    with open(path) as f:
        for line in f:
            if not line.startswith("cpu") or line.startswith("cpu "):
                continue
            v = [int(x) for x in line.split()[1:9]]  # user nice system idle iowait irq softirq steal
            v += [0] * (8 - len(v))
            out.append((v[0] + v[1], v[2] + v[5] + v[6] + v[7], sum(v)))
    return out


def percents(before: list, after: list) -> list[dict]:
    out = []
    for (u0, s0, t0), (u1, s1, t1) in zip(before, after):
        dt = t1 - t0
        if dt <= 0:
            out.append({"user": 0.0, "system": 0.0})
            continue
        user = min(100.0, max(0.0, 100.0 * (u1 - u0) / dt))
        system = min(100.0 - user, max(0.0, 100.0 * (s1 - s0) / dt))
        out.append({"user": round(user, 1), "system": round(system, 1)})
    return out


def read_cgroup_usec(path: str = MC_CGROUP) -> int:
    with open(path) as f:
        for line in f:
            if line.startswith("usage_usec "):
                return int(line.split()[1])
    raise ValueError("no usage_usec")


class CpuMeter:
    """Each sample() returns use since the previous one (or over a short first sample)."""

    def __init__(self, read=read_stat, read_mc=read_cgroup_usec, clock=time.monotonic, sleep=time.sleep,
                 first_wait: float = 0.5):
        self.read, self.read_mc, self.clock, self.sleep, self.first_wait = read, read_mc, clock, sleep, first_wait
        self._last = None
        self._lock = threading.Lock()

    def _now(self):
        try:
            threads = self.read()
        except (OSError, ValueError):
            threads = None
        try:
            mc = self.read_mc()
        except (OSError, ValueError):
            mc = None
        return self.clock(), threads, mc

    def sample(self) -> dict:
        with self._lock:
            if self._last is None:
                self._last = self._now()
                self.sleep(self.first_wait)
            now = self._now()
            (t0, th0, mc0), (t1, th1, mc1) = self._last, now
            self._last = now
        threads = percents(th0, th1) if th0 and th1 and len(th0) == len(th1) else None
        server = None
        if mc0 is not None and mc1 is not None and t1 > t0 and mc1 >= mc0:
            server = round((mc1 - mc0) / ((t1 - t0) * 1e6) * 100, 1)
        return {"threads": threads, "server": server}
