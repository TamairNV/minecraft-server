"""Chunk pre-generation with the Chunky plugin, one task per dimension, and the
BlueMap render that turns those chunks into the website map.

Progress comes from Chunky's own lines in the console log, so watching it never
sends anything to the server. Chunky only generates chunks that are missing.
BlueMap notices changed region files by itself within a few minutes; the map
render here only queues that sooner.
"""
import re
import threading
import time
from pathlib import Path

from .settings import COORD_MAX, DIMENSIONS, SettingsError, num, parse
from .worlds import read_json, write_json

SHAPES = ("square", "circle")
RADIUS_MAX = COORD_MAX
TAIL = 1024 * 1024
WORLD = r"minecraft:(\w+)"
EVENTS = (
    ("started", re.compile(rf"\[Chunky\] Task started in {WORLD} for the (\w+) region centered at ([-\d.,]+), ([-\d.,]+) with radius ([\d.,]+)")),
    ("running", re.compile(rf"\[Chunky\] Task running for {WORLD}\. Processed: ([\d,]+) chunks \(([\d.,]+)%\), ETA: ([\d:]+), Rate: ([\d.,]+) cps")),
    ("finished", re.compile(rf"\[Chunky\] Task finished for {WORLD}\. Processed: ([\d,]+) chunks \(([\d.,]+)%\), Total time: ([\d:]+)")),
    ("paused", re.compile(rf"\[Chunky\] Task (?:paused|stopped) for {WORLD}\.")),
    ("running", re.compile(rf"\[Chunky\] Task continuing for {WORLD}\.")),
    ("cancelled", re.compile(rf"\[Chunky\] Task cancelled for {WORLD}\.")),
)
TIME_RE = re.compile(r"^\[(\d\d:\d\d:\d\d)\]")
# The answer to `bluemap` is one multi-line log entry: the header, then lines without a timestamp.
MAP_STATUS_RE = re.compile(r"BlueMap Status >[^\n]*\n((?:(?!\[\d\d:\d\d:\d\d\]).*(?:\n|$))*)")
MAP_DIM_RE = re.compile(r'^dimension:\s*"minecraft:(\w+)"', re.M)


def number(text: str) -> float:
    return float(text.replace(",", ""))


class Pregen:
    def __init__(self, server: Path, data: Path, crafty, sleep=time.sleep, query=None):
        self.server, self.crafty, self.sleep, self.query = Path(server), crafty, sleep, query
        self.bluemap_maps = self.server / "plugins" / "BlueMap" / "maps"
        self.log_file = self.server / "logs" / "latest.log"
        self.tasks = self.server / "plugins" / "Chunky" / "tasks" / "minecraft"
        self.last_file = Path(data) / "pregen.json"
        self._lock = threading.Lock()

    def _log_events(self) -> dict:
        try:
            with open(self.log_file, "rb") as f:
                size = f.seek(0, 2)
                f.seek(max(0, size - TAIL))
                text = f.read().decode(errors="replace")
        except OSError:
            return {}
        out: dict = {}
        for line in text.splitlines():
            if "[Chunky]" not in line:
                continue
            for status, pattern in EVENTS:
                m = pattern.search(line)
                if not m:
                    continue
                t = TIME_RE.match(line)
                row = out.setdefault(m.group(1), {})
                row.update(status=status, at=t.group(1) if t else None)
                if pattern is EVENTS[0][1]:
                    row.update(shape=m.group(2), center_x=number(m.group(3)), center_z=number(m.group(4)),
                               radius=number(m.group(5)), chunks=0, percent=0.0, eta=None, rate=None)
                elif status == "running" and m.lastindex >= 5:
                    row.update(chunks=int(number(m.group(2))), percent=number(m.group(3)), eta=m.group(4), rate=number(m.group(5)))
                elif status == "finished":
                    row.update(chunks=int(number(m.group(2))), percent=number(m.group(3)), eta=None, rate=None, took=m.group(4))
                break
        return out

    def _saved_task(self, dim: str):
        """Chunky's saved task file: what's left to continue after a pause or restart."""
        try:
            props = parse((self.tasks / f"{dim}.properties").read_text())
        except OSError:
            return None
        try:
            return {"cancelled": props.get("cancelled") == "true", "shape": props.get("shape"),
                    "center_x": float(props.get("center-x", 0)), "center_z": float(props.get("center-z", 0)),
                    "radius": float(props.get("radius", 0)), "chunks": int(props.get("chunks", 0))}
        except ValueError:
            return None

    def state(self, running: bool) -> list:
        events = self._log_events()
        last = read_json(self.last_file, {}) or {}
        out = []
        for dim, name in DIMENSIONS:
            row = {"dim": dim, "name": name, "status": "idle", "last": last.get(dim)}
            ev = events.get(dim)
            saved = self._saved_task(dim)
            if ev:
                row.update(ev)
            elif saved and not saved["cancelled"]:
                row.update(status="paused", shape=saved["shape"], center_x=saved["center_x"],
                           center_z=saved["center_z"], radius=saved["radius"], chunks=saved["chunks"])
            # Chunky doesn't carry on after a restart, so a stopped server means nothing is running.
            if row["status"] == "running" and not running:
                row["status"] = "paused"
            row["can_continue"] = row["status"] == "paused" or (saved is not None and not saved["cancelled"] and row["status"] != "running")
            out.append(row)
        return out

    def start(self, dim: str, radius: float, x: float, z: float, shape: str, by: str) -> list:
        if dim not in dict(DIMENSIONS):
            raise SettingsError("Unknown dimension.")
        if shape not in SHAPES:
            raise SettingsError("The shape must be square or circle.")
        if not 16 <= radius <= RADIUS_MAX:
            raise SettingsError(f"The radius must be between 16 and {RADIUS_MAX:,} blocks.")
        if not (abs(x) <= COORD_MAX and abs(z) <= COORD_MAX):
            raise SettingsError(f"The centre must be within {COORD_MAX:,} blocks of 0.")
        commands = [f"chunky world minecraft:{dim}", f"chunky shape {shape}", f"chunky center {num(x)} {num(z)}",
                    f"chunky radius {num(radius)}", "chunky start", "chunky confirm"]
        # Chunky keeps one selection for the console, so two starts mustn't interleave.
        with self._lock:
            for command in commands:
                self.crafty.command(command)
                self.sleep(0.3)
            last = read_json(self.last_file, {}) or {}
            last[dim] = {"radius": radius, "center_x": x, "center_z": z, "shape": shape, "time": int(time.time()), "by": by}
            write_json(self.last_file, last)
        return commands

    def control(self, dim: str, action: str) -> list:
        if dim not in dict(DIMENSIONS):
            raise SettingsError("Unknown dimension.")
        if action not in ("pause", "continue", "cancel"):
            raise SettingsError("Unknown action.")
        commands = [f"chunky {action} minecraft:{dim}"] + (["chunky confirm"] if action == "cancel" else [])
        with self._lock:
            for command in commands:
                self.crafty.command(command)
                self.sleep(0.3)
        return commands

    # ---- BlueMap
    def map_ids(self) -> dict:
        """Dimension -> BlueMap map id, from BlueMap's map configs."""
        out = {}
        for conf in sorted(self.bluemap_maps.glob("*.conf")):
            try:
                m = MAP_DIM_RE.search(conf.read_text())
            except OSError:
                continue
            if m:
                out.setdefault(m.group(1), conf.stem)
        return out

    def render(self, dim: str) -> list:
        map_id = self.map_ids().get(dim)
        if dim not in dict(DIMENSIONS) or not map_id:
            raise SettingsError("That dimension has no map.")
        commands = [f"bluemap update {map_id}"]
        self.crafty.command(commands[0])
        return commands

    def map_status(self) -> dict:
        """BlueMap's own status report, e.g. idle threads or a queued render with its progress."""
        m = self.query("bluemap", MAP_STATUS_RE) if self.query else None
        if not m:
            return {"lines": [], "busy": None}
        lines = [line.rstrip() for line in m.group(1).splitlines() if line.strip()]
        text = " ".join(lines).lower()
        return {"lines": lines, "busy": "idle" not in text}
