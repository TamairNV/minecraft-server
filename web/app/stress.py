"""Network tab stress test: lots of real Minecraft TCP connections, to see what the route can take.

Each worker opens a TCP connection, sends a handshake + status request, reads the reply, sends a
ping, reads the pong and closes, then starts again until time is up. That is the same exchange a
client does for the server list, so it goes through the friend's proxy and WireGuard exactly like
a player's join does, without logging in or touching the world. One test runs at a time, in a
background thread, with hard limits on connections and duration.

What it records:
- connect: time for the TCP handshake. A lost SYN costs a 1 s retry, so a tail here is packet loss.
- total: the whole exchange (connect + status + ping/pong), what a player waits for in the list.
- failures by kind (timeout, refused, reset, other), and a per-second timeline.
"""
import asyncio
import json
import os
import struct
import threading
import time

from .slp import packet, read_varint, varint

TARGETS = {
    "proxy": (os.environ.get("STRESS_PROXY_HOST", "server.tamairnv.co.uk"), 25565),
    "direct": (os.environ.get("STRESS_DIRECT_HOST", "crafty"), 25565),
}
MAX_CONNECTIONS = 50
MAX_SECONDS = 60
TIMEOUT = 5.0


def pct(sorted_vals: list, p: float):
    if not sorted_vals:
        return None
    return round(sorted_vals[min(len(sorted_vals) - 1, int(p * len(sorted_vals)))], 1)


def kind(e: BaseException) -> str:
    if isinstance(e, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"
    if isinstance(e, ConnectionRefusedError):
        return "refused"
    if isinstance(e, (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError)):
        return "reset"
    return "other"


async def exchange(host: str, port: int, timeout: float = TIMEOUT) -> tuple[float, float, int, int]:
    """One full status + ping exchange. Returns (connect ms, total ms, bytes sent, bytes received)."""
    t0 = time.perf_counter()
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    t1 = time.perf_counter()
    try:
        async def talk():
            h = host.encode()
            hs = varint(-1 & 0xFFFFFFFF) + varint(len(h)) + h + struct.pack(">H", port) + varint(1)
            out = packet(0, hs) + packet(0, b"")
            writer.write(out)
            await writer.drain()
            await read_varint(reader)
            await read_varint(reader)
            size = await read_varint(reader)
            body = await reader.readexactly(size)
            json.loads(body)
            ping = packet(1, struct.pack(">q", int(t0 * 1000)))
            writer.write(ping)
            await writer.drain()
            n = await read_varint(reader)
            pong = await reader.readexactly(n)
            return len(out) + len(ping), size + len(pong)  # bytes out, bytes in (bodies; headers are a few more)
        sent, got = await asyncio.wait_for(talk(), timeout)
    finally:
        writer.close()
    t2 = time.perf_counter()
    return (t1 - t0) * 1000, (t2 - t0) * 1000, sent, got


class StressTest:
    def __init__(self, targets: dict = TARGETS, clock=time.time):
        self.targets = targets
        self.clock = clock
        self._lock = threading.Lock()
        self._state = {"running": False, "last": None}
        self._live = None

    def status(self) -> dict:
        with self._lock:
            out = dict(self._state)
            if self._live is not None:
                out["live"] = self._summary(self._live, final=False)
            return out

    def start(self, target: str, connections: int, seconds: int, who: str = "") -> dict:
        if target not in self.targets:
            raise ValueError("Unknown target.")
        if not 1 <= connections <= MAX_CONNECTIONS:
            raise ValueError(f"Connections must be 1 to {MAX_CONNECTIONS}.")
        if not 5 <= seconds <= MAX_SECONDS:
            raise ValueError(f"Duration must be 5 to {MAX_SECONDS} seconds.")
        with self._lock:
            if self._state["running"]:
                raise RuntimeError("A stress test is already running.")
            host, port = self.targets[target]
            self._live = {"target": target, "host": host, "port": port, "connections": connections,
                          "seconds": seconds, "started": self.clock(), "who": who,
                          "connect": [], "total": [], "fails": {}, "sent": 0, "got": 0, "timeline": {}}
            self._state["running"] = True
        threading.Thread(target=self._run, daemon=True).start()
        return self.status()

    def _run(self) -> None:
        live = self._live
        try:
            asyncio.run(self._drive(live))
        except Exception as e:  # never leave the test stuck "running"
            live["fails"]["other"] = live["fails"].get("other", 0) + 1
            live["error"] = str(e)[:200]
        with self._lock:
            self._state["last"] = self._summary(live, final=True)
            self._state["running"] = False
            self._live = None

    async def _drive(self, live: dict) -> None:
        start = time.perf_counter()
        end = start + live["seconds"]

        async def worker():
            while time.perf_counter() < end:
                sec = int(time.perf_counter() - start)
                try:
                    c, t, s, g = await exchange(live["host"], live["port"])
                except Exception as e:  # count it and go again
                    k = kind(e)
                    live["fails"][k] = live["fails"].get(k, 0) + 1
                    slot = live["timeline"].setdefault(sec, [0, 0, 0.0])
                    slot[1] += 1
                    await asyncio.sleep(0.2)  # don't spin on a dead route
                    continue
                live["connect"].append(c)
                live["total"].append(t)
                live["sent"] += s
                live["got"] += g
                slot = live["timeline"].setdefault(sec, [0, 0, 0.0])
                slot[0] += 1
                slot[2] += t

        await asyncio.gather(*(worker() for _ in range(live["connections"])))

    def _summary(self, live: dict, final: bool) -> dict:
        conn, tot = sorted(live["connect"]), sorted(live["total"])
        fails = sum(live["fails"].values())
        n = len(tot) + fails
        elapsed = max(0.001, min(live["seconds"], self.clock() - live["started"]))
        timeline = [{"s": s, "ok": v[0], "fail": v[1], "avg": round(v[2] / v[0], 1) if v[0] else None}
                    for s, v in sorted(live["timeline"].items())]
        return {
            "target": live["target"], "host": live["host"], "port": live["port"],
            "connections": live["connections"], "seconds": live["seconds"], "started": int(live["started"]),
            "elapsed": round(elapsed, 1), "done": final, "attempts": n, "ok": len(tot), "failed": fails,
            "fail_kinds": dict(live["fails"]), "fail_pct": round(100 * fails / n, 2) if n else 0,
            "per_second": round(len(tot) / elapsed, 1),
            "connect_ms": {"p50": pct(conn, .5), "p95": pct(conn, .95), "p99": pct(conn, .99), "max": pct(conn, 1)},
            "total_ms": {"p50": pct(tot, .5), "p95": pct(tot, .95), "p99": pct(tot, .99), "max": pct(tot, 1)},
            # connects over 900 ms almost always mean a lost SYN and a retransmit
            "slow_connects": sum(1 for c in conn if c >= 900),
            "kbytes": round((live["sent"] + live["got"]) / 1024),
            "timeline": timeline, "error": live.get("error"),
        }
