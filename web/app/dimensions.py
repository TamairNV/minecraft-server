"""Nether / End lock, enforced by the DimLock plugin (~/minecraft/plugins-src/dimlock).

The plugin's config.yml is the source of truth. While the server runs, the site sends
`dimlock <dim> lock|unlock` (the plugin saves the file and announces unlocks); while it's
stopped, the site edits the file and the plugin picks it up on start.
"""
import os
import re
import threading
from pathlib import Path

DIMS = {"nether": "The Nether", "end": "The End"}
LINE_RE = re.compile(r"^([ \t]+)(nether|end):[ \t]*(true|false)[ \t]*$", re.M)


class DimensionError(Exception):
    pass


class Dimensions:
    def __init__(self, server: Path, crafty, query):
        self.file = server / "plugins" / "DimLock" / "config.yml"
        self.plugins = server / "plugins"
        self.crafty, self.query = crafty, query
        self._lock = threading.Lock()

    def _read(self) -> dict | None:
        try:
            text = self.file.read_text()
        except OSError:
            return None
        found = {m[2]: m[3] == "true" for m in LINE_RE.finditer(text)}
        return {dim: found.get(dim, True) for dim in DIMS}  # the plugin treats missing as locked

    def view(self) -> dict:
        locked = self._read()
        plugin = locked is not None or any(self.plugins.glob("DimLock-*.jar"))
        return {
            "installed": plugin,
            "dims": [{"dim": d, "name": n, "locked": (locked or {}).get(d, True)} for d, n in DIMS.items()],
        }

    def set(self, dim: str, locked: bool, running: bool) -> bool:
        """Returns the state the plugin (or the file) now reports."""
        if dim not in DIMS:
            raise DimensionError("Unknown dimension.")
        if not self.view()["installed"]:
            raise DimensionError("The DimLock plugin isn't installed on the server.")
        action = "lock" if locked else "unlock"
        with self._lock:
            if running:
                m = self.query(f"dimlock {dim} {action}", re.compile(rf"DimLock: {dim} is (locked|unlocked)"))
                if m is None:
                    raise DimensionError("The server didn't answer. Check the console, then refresh.")
                return m[1] == "locked"
            self._write(dim, locked)
            return locked

    def _write(self, dim: str, locked: bool) -> None:
        current = self._read()
        if current is None:  # plugin jar is there but has never run
            current = {d: True for d in DIMS}
        current[dim] = locked
        text = "locked:\n" + "".join(f"  {d}: {'true' if v else 'false'}\n" for d, v in current.items())
        if self.file.exists():
            old = self.file.read_text()
            if LINE_RE.search(old):  # keep the plugin's own layout and comments, change just the values
                text = LINE_RE.sub(lambda m: f"{m[1]}{m[2]}: {'true' if current[m[2]] else 'false'}", old)
                missing = [d for d in DIMS if not re.search(rf"^[ \t]+{d}:", text, re.M)]
                if missing:
                    text = text.replace("locked:\n", "locked:\n" + "".join(
                        f"  {d}: {'true' if current[d] else 'false'}\n" for d in missing), 1)
        self.file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(text)
        os.replace(tmp, self.file)
