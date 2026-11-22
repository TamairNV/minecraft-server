"""Item pictures for rewards, drawn the way the game's inventory shows them.

Textures and models come from the Minecraft client jar BlueMap downloads into the server folder.
Flat items (item/generated models) are the texture scaled up; blocks are drawn as a shaded isometric cube
(top 100%, front on the left 80%, side on the right 60%, like the inventory). Each picture is SIZE x SIZE PNG.
"""
import io
import json
import math
import re
import threading
import zipfile
from pathlib import Path

from PIL import Image

ID_RE = re.compile(r"^[a-z0-9_]{1,64}$")
FALLBACK = ("item/{}", "block/{}", "block/{}_front", "block/{}_side", "block/{}_top")
FLAT_PARENTS = {"item/generated", "item/handheld", "item/handheld_rod", "builtin/generated"}
SIZE = 128
MAX_CACHED = 2000


def _path(ref: str) -> str:
    return ref.split(":")[-1]


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
        jar = self._jar()
        found = None
        if jar:
            try:
                with zipfile.ZipFile(jar) as z:
                    found = _render(z, set(z.namelist()), item)
            except (OSError, zipfile.BadZipFile, ValueError, KeyError):
                return None
        with self._lock:
            if len(self._cache) < MAX_CACHED:
                self._cache[item] = found
        return found


# ---- reading the jar
def _json(z, names, path):
    full = f"assets/minecraft/{path}.json"
    return json.loads(z.read(full)) if full in names else None


def _texture(z, names, ref) -> Image.Image | None:
    if isinstance(ref, dict):
        ref = ref.get("sprite", "")
    full = f"assets/minecraft/textures/{_path(ref)}.png"
    if full not in names:
        return None
    im = Image.open(io.BytesIO(z.read(full))).convert("RGBA")
    w = im.width
    return im.crop((0, 0, w, w)) if im.height > w else im  # animated textures: first frame


def _model(z, names, item):
    """(flat parent reached?, merged textures) for the item's model, or None if it isn't a plain model."""
    entry = _json(z, names, f"items/{item}")
    if not entry or entry.get("model", {}).get("type") != "minecraft:model":
        return None
    path, textures, flat = _path(entry["model"]["model"]), {}, False
    for _ in range(10):
        if path in FLAT_PARENTS:
            flat = True
            break
        m = _json(z, names, f"models/{path}")
        if m is None:
            break
        for k, v in (m.get("textures") or {}).items():
            textures.setdefault(k, v)
        if "parent" not in m:
            break
        path = _path(m["parent"])

    def resolve(v, depth=0):
        while isinstance(v, str) and v.startswith("#") and depth < 10:
            v, depth = textures.get(v[1:]), depth + 1
        return v

    return flat, {k: resolve(v) for k, v in textures.items()}


CHESTS = {"chest": "normal", "trapped_chest": "trapped", "ender_chest": "ender"}


def _chest(z, names, kind) -> Image.Image | None:
    """Chests are drawn from an entity texture: lid strip 5 tall, base strip 10 tall, both stored upside down."""
    atlas = _texture(z, names, f"entity/chest/{kind}")
    if atlas is None or atlas.size != (64, 64):
        return None
    flip = lambda im: im.transpose(Image.FLIP_TOP_BOTTOM)

    def face(x, latch=False):
        out = Image.new("RGBA", (14, 14), (0, 0, 0, 0))
        out.alpha_composite(flip(atlas.crop((x, 33, x + 14, 43))), (0, 4))
        out.alpha_composite(flip(atlas.crop((x, 14, x + 14, 19))), (0, 0))
        if latch:
            out.alpha_composite(atlas.crop((1, 1, 3, 5)), (6, 2))
        return out

    return _cube(atlas.crop((28, 0, 42, 14)), face(42, latch=True), face(0))


def _render(z, names, item) -> bytes | None:
    if item in CHESTS:
        im = _chest(z, names, CHESTS[item])
        if im:
            return _png(im)
    model = _model(z, names, item)
    if model:
        flat, tex = model
        if flat and tex.get("layer0"):
            im = _texture(z, names, tex["layer0"])
            return _png(_flat(im)) if im else None
        pick = lambda *keys: next((t for k in keys if (t := tex.get(k)) and _texture(z, names, t)), None)
        top, front, side = pick("up", "top", "end", "all"), pick("north", "front", "side", "all"), pick("east", "side", "all")
        if top and front and side:
            return _png(_cube(*(_texture(z, names, t) for t in (top, front, side))))
    for t in FALLBACK:
        im = _texture(z, names, t.format(item))
        if im:
            return _png(_flat(im))
    return None


# ---- drawing
def _png(im: Image.Image) -> bytes:
    b = io.BytesIO()
    im.save(b, "PNG", optimize=True)
    return b.getvalue()


def _flat(tex: Image.Image) -> Image.Image:
    return tex.resize((SIZE, SIZE), Image.NEAREST)


def _shade(tex: Image.Image, k: float) -> Image.Image:
    r, g, b, a = tex.split()
    r, g, b = (c.point(lambda v: int(v * k)) for c in (r, g, b))
    return Image.merge("RGBA", (r, g, b, a))


def _face(canvas: Image.Image, tex: Image.Image, origin, du, dv) -> None:
    """Paints tex so its (0,0) lands on origin and its u/v edges run along du/dv (canvas pixels)."""
    n = tex.width
    big = tex.resize((n * 8, n * 8), Image.NEAREST)  # crisp pixels after the affine resample
    s = big.width
    ax, ay, bx, by = du[0] / s, du[1] / s, dv[0] / s, dv[1] / s
    det = ax * by - bx * ay
    ia, ib, id_, ie = by / det, -bx / det, -ay / det, ax / det
    ox, oy = origin
    data = (ia, ib, -(ia * ox + ib * oy), id_, ie, -(id_ * ox + ie * oy))
    layer = big.transform(canvas.size, Image.AFFINE, data, resample=Image.NEAREST, fillcolor=(0, 0, 0, 0))
    canvas.alpha_composite(layer)


def _cube(top: Image.Image, front: Image.Image, side: Image.Image) -> Image.Image:
    """Isometric cube like the inventory: yaw 45, pitch 30 (top diamond twice as wide as tall)."""
    w = 56.0  # half width
    q = w / 2  # top diamond half height
    h = w * math.sqrt(2) * math.cos(math.radians(30))  # vertical edge
    cx, t0 = SIZE / 2, (SIZE - (2 * q + h)) / 2
    back, right, near, left = (cx, t0), (cx + w, t0 + q), (cx, t0 + 2 * q), (cx - w, t0 + q)
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    vec = lambda a, b, pad=1.0: ((b[0] - a[0]) * pad, (b[1] - a[1]) * pad)
    # Slightly oversized faces so no hairline gaps show along the shared edges.
    _face(canvas, _shade(front, 0.8), left, vec(left, near, 1.01), (0, h + 0.5))
    _face(canvas, _shade(side, 0.6), near, vec(near, right, 1.01), (0, h + 0.5))
    _face(canvas, top, left, vec(left, back, 1.01), vec(left, near, 1.01))
    return canvas
