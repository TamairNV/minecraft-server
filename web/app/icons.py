"""Item pictures for rewards, read from the Minecraft client jar BlueMap downloads into the server folder.

Items use textures/item/<id>.png; block items fall back to the block's own face (front, side, top).
"""
import re
import threading
import zipfile
from pathlib import Path

ID_RE = re.compile(r"^[a-z0-9_]{1,64}$")
TRIES = ("item/{}", "block/{}", "block/{}_front", "block/{}_side", "block/{}_top")
MAX_CACHED = 2000


class Icons:
    def __init__(self, server: Path):
        self.server = server
        self._cache: dict = {}
        self._lock = threading.Lock()

    def _jar(self) -> Path | None:
        jars = sorted((self.server / "bluemap").glob("minecraft-client-*.jar"), key=lambda p: p.stat().st_mtime)
        return jars[-1] if jars else None

    def get(self, item: str) -> bytes | None:
        """The PNG for an item id like "diamond" (no namespace), or None."""
        if not ID_RE.fullmatch(item or ""):
            return None
        with self._lock:
            if item in self._cache:
                return self._cache[item]
        found = None
        jar = self._jar()
        if jar:
            try:
                with zipfile.ZipFile(jar) as z:
                    names = set(z.namelist())
                    for t in TRIES:
                        path = f"assets/minecraft/textures/{t.format(item)}.png"
                        if path in names:
                            found = z.read(path)
                            break
            except (OSError, zipfile.BadZipFile):
                return None
        with self._lock:
            if len(self._cache) < MAX_CACHED:
                self._cache[item] = found
        return found
