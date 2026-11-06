"""Fetch player skins from Mojang and keep a copy, so visitors never talk to a third party."""
import base64
import json
import re
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import httpx

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
PROFILE = "https://sessionserver.mojang.com/session/minecraft/profile/"
FRESH = 6 * 3600
RETRY = 600
MAX_BYTES = 256 * 1024


def png_size(data: bytes):
    """(width, height) if this is a PNG, otherwise None."""
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


def texture_info(profile: dict):
    """(https skin url, slim) from a Mojang profile, or (None, False) if it has no custom skin."""
    for prop in profile.get("properties", []):
        if prop.get("name") == "textures":
            skin = json.loads(base64.b64decode(prop["value"])).get("textures", {}).get("SKIN")
            if not skin:
                return None, False
            url = urlparse(skin["url"])
            if url.hostname != "textures.minecraft.net":
                raise ValueError("unexpected skin host")
            slim = skin.get("metadata", {}).get("model") == "slim"
            return f"https://textures.minecraft.net{url.path}", slim
    return None, False


class Skins:
    def __init__(self, folder: Path, http=None):
        self.folder = Path(folder)
        self.http = http or httpx.Client(timeout=5)

    def file(self, uuid: str) -> Path | None:
        if not UUID_RE.match(uuid):
            return None
        path = self.folder / f"{uuid}.png"
        return path if path.exists() else None

    def _download(self, uuid: str) -> dict:
        r = self.http.get(PROFILE + uuid.replace("-", ""))
        r.raise_for_status()
        url, slim = texture_info(r.json())
        meta = {"checked": time.time(), "slim": slim, "legacy": False, "ok": False, "version": ""}
        if url:
            data = self.http.get(url)
            data.raise_for_status()
            size = png_size(data.content)
            if len(data.content) > MAX_BYTES or size not in ((64, 64), (64, 32)):
                raise ValueError("not a skin image")
            self.folder.mkdir(parents=True, exist_ok=True)
            (self.folder / f"{uuid}.png").write_bytes(data.content)
            meta.update(ok=True, legacy=size[1] == 32, version=url.rsplit("/", 1)[-1][:12])
        return meta

    def get(self, uuid: str) -> dict:
        meta_file = self.folder / f"{uuid}.json"
        meta = None
        if UUID_RE.match(uuid):
            try:
                meta = json.loads(meta_file.read_text())
            except (OSError, ValueError):
                pass
            age = time.time() - meta["checked"] if meta else None
            if age is None or age > (FRESH if meta.get("ok") else RETRY):
                try:
                    meta = self._download(uuid)
                except (httpx.HTTPError, ValueError, KeyError, TypeError, OSError):
                    # Keep showing the old skin, and don't ask again for a while.
                    meta = {**(meta or {"slim": False, "legacy": False, "ok": False, "version": ""}), "checked": time.time()}
                self.folder.mkdir(parents=True, exist_ok=True)
                meta_file.write_text(json.dumps(meta))
        if not meta or not meta.get("ok"):
            return {"url": None, "slim": False, "legacy": False}
        return {"url": f"/api/skin/{uuid}.png?v={meta['version']}", "slim": meta["slim"], "legacy": meta["legacy"]}

    def get_many(self, uuids: list) -> dict:
        with ThreadPoolExecutor(6) as pool:
            return dict(zip(uuids, pool.map(self.get, uuids)))
