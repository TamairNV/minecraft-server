"""Server settings: server.properties, and the live world border of each dimension.

Saving rewrites only the lines that changed, so comments, order and keys the
site doesn't know stay as they were. The game rewrites the file itself when it
starts, though, and that drops comments.
"""
import gzip
import io
import json
import os
import re
import struct
import threading
import time
from datetime import datetime
from pathlib import Path

from .worlds import read_json, write_json

# (key, label, group, kind, choices or (min, max), live console command, help)
FIELDS = (
    ("difficulty", "Difficulty", "Gameplay", "enum", ("peaceful", "easy", "normal", "hard"), "difficulty {}", ""),
    ("gamemode", "Default game mode", "Gameplay", "enum", ("survival", "creative", "adventure", "spectator"), "defaultgamemode {}",
     "For players joining for the first time."),
    ("hardcore", "Hardcore", "Gameplay", "bool", None, None, "Dead players can only spectate."),
    ("pvp", "Player vs player", "Gameplay", "rule", None, "gamerule pvp {}", "A game rule in this version, so it's saved with the world."),
    ("spawn-protection", "Spawn protection radius", "Gameplay", "int", (0, 1000), None, "Blocks around spawn only operators can change. 0 turns it off."),
    ("allow-flight", "Allow flight", "Gameplay", "bool", None, None, "Stops the server kicking players for flying, e.g. with a mod."),
    ("force-gamemode", "Force game mode", "Gameplay", "bool", None, None, "Put everyone in the default mode each time they join."),
    ("view-distance", "View distance (chunks)", "Performance", "int", (2, 32), None, "Each step costs a lot of memory and CPU."),
    ("simulation-distance", "Simulation distance (chunks)", "Performance", "int", (2, 32), None, "How far away mobs and farms keep running."),
    ("max-players", "Max players", "Performance", "int", (1, 100), None, ""),
    ("entity-broadcast-range-percentage", "Entity visibility range (%)", "Performance", "int", (10, 1000), None, "How far away players see mobs."),
    ("white-list", "Whitelist on", "Access", "bool", None, "whitelist {}", ""),
    ("enforce-whitelist", "Kick players not on the whitelist", "Access", "bool", None, None, "Applies when the whitelist is reloaded or turned on."),
    ("online-mode", "Online mode", "Access", "locked", None, None,
     "Checks every player's Minecraft account. Turning it off lets anyone join with any name, so it can't be changed here."),
    ("motd", "Server list message (MOTD)", "Display", "text", (0, 150), None, 'Shown under the server name in the list, e.g. "TNV SMP".'),
)
GROUPS = ("Gameplay", "Performance", "Access", "Display")
BY_KEY = {f[0]: f for f in FIELDS}
# Never sent to the browser.
HIDDEN = re.compile(r"password|secret|token", re.I)
# Shown, but managed elsewhere (Worlds tab, compose ports) or a security switch.
LOCKED = {"online-mode", "level-name", "level-seed", "server-ip", "server-port", "query.port", "enable-query",
          "rcon.port", "enable-rcon", "management-server-enabled", "management-server-host",
          "management-server-port", "management-server-tls-enabled", "management-server-tls-keystore",
          "management-server-allowed-origins"}
LIVE_VALUE = {"white-list": lambda v: "on" if v == "true" else "off"}
KEEP_BACKUPS = 50

DIMENSIONS = (("overworld", "Overworld"), ("the_nether", "Nether"), ("the_end", "The End"))
BORDER_MAX = 59_999_968
COORD_MAX = 29_999_984
BORDER_RE = re.compile(r"world border is currently ([\d.,]+) block")
RULE_RE = re.compile(r"Game rule pvp is currently set to (true|false)")


class SettingsError(Exception):
    """Something the admin needs to read."""


# ---- server.properties --------------------------------------------------------
def unescape(raw: str) -> str:
    out, i = [], 0
    while i < len(raw):
        c = raw[i]
        if c == "\\" and i + 1 < len(raw):
            n = raw[i + 1]
            if n == "u" and re.fullmatch(r"[0-9a-fA-F]{4}", raw[i + 2:i + 6]):
                out.append(chr(int(raw[i + 2:i + 6], 16)))
                i += 6
                continue
            out.append({"t": "\t", "n": "\n", "r": "\r", "f": "\f"}.get(n, n))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def escape(value: str) -> str:
    text = value.replace("\\", "\\\\").replace(":", "\\:").replace("=", "\\=")
    return "\\" + text if text.startswith(" ") else text


def parse(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        stripped = line.lstrip()
        if not stripped or stripped[0] in "#!" or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = unescape(v)
    return out


def rewrite(text: str, changes: dict) -> str:
    """Replace only the changed keys' lines."""
    lines = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped and stripped[0] not in "#!" and "=" in line:
            k = line.split("=", 1)[0].strip()
            if k in changes:
                line = f"{k}={escape(changes[k])}"
        lines.append(line)
    return "\n".join(lines) + "\n"


def kind_of(key: str):
    return BY_KEY[key][3] if key in BY_KEY else None


def check(key: str, value, current: str) -> str:
    """The value as the file stores it, or a SettingsError saying what's wrong."""
    field = BY_KEY.get(key)
    kind = field[3] if field else "bool" if current in ("true", "false") else "int" if re.fullmatch(r"-?\d+", current or "") else "raw"
    label = field[1] if field else key
    if isinstance(value, bool):
        value = "true" if value else "false"
    value = str(value).strip() if kind != "text" else str(value)
    if any(c in value for c in "\r\n"):
        raise SettingsError(f"{label} must be a single line.")
    if kind in ("bool", "rule"):
        if value not in ("true", "false"):
            raise SettingsError(f"{label} must be on or off.")
    elif kind == "enum":
        if value not in field[4]:
            raise SettingsError(f"{label} must be one of {', '.join(field[4])}.")
    elif kind == "int":
        if not re.fullmatch(r"-?\d{1,9}", value):
            raise SettingsError(f"{label} must be a whole number.")
        if field:
            low, high = field[4]
            if not low <= int(value) <= high:
                raise SettingsError(f"{label} must be between {low} and {high}.")
    elif kind == "text":
        if len(value) > field[4][1]:
            raise SettingsError(f"{label} can be up to {field[4][1]} characters.")
    elif len(value) > 1000:
        raise SettingsError(f"{label} is too long.")
    return value


# ---- world borders --------------------------------------------------------------
def read_nbt(data: bytes) -> dict:
    """Just enough NBT for the small data files: the root compound as a dict."""
    f = io.BytesIO(data)

    def read(fmt):
        return struct.unpack(">" + fmt, f.read(struct.calcsize(fmt)))[0]

    def string():
        return f.read(read("H")).decode(errors="replace")

    def payload(tag):
        if tag == 1: return read("b")
        if tag == 2: return read("h")
        if tag == 3: return read("i")
        if tag == 4: return read("q")
        if tag == 5: return read("f")
        if tag == 6: return read("d")
        if tag in (7, 11, 12):
            n = read("i")
            return f.read(n * {7: 1, 11: 4, 12: 8}[tag])
        if tag == 8: return string()
        if tag == 9:
            inner, n = read("b"), read("i")
            return [payload(inner) for _ in range(n)]
        if tag == 10:
            out = {}
            while (t := read("b")) != 0:
                name = string()
                out[name] = payload(t)
            return out
        raise ValueError(f"unknown NBT tag {tag}")

    if read("b") != 10:
        raise ValueError("not an NBT compound")
    string()
    return payload(10)


def num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:.1f}"


class ServerSettings:
    def __init__(self, server: Path, data: Path, crafty, sleep=time.sleep):
        self.server, self.data, self.crafty, self.sleep = Path(server), Path(data), crafty, sleep
        self.file = self.server / "server.properties"
        self.log_file = self.server / "logs" / "latest.log"
        self.backup_dir = self.data / "settings-backups"
        self.pending_file = self.data / "settings-pending.json"
        self.borders_file = self.data / "borders.json"
        self._lock = threading.Lock()
        self._query_lock = threading.Lock()

    # ---- console queries
    def query(self, command: str, pattern: re.Pattern, timeout: float = 3.0):
        """Send a read-only command and pick its answer out of the console log."""
        with self._query_lock:
            try:
                start = self.log_file.stat().st_size
            except OSError:
                return None
            self.crafty.command(command)
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                self.sleep(0.15)
                try:
                    with open(self.log_file, "rb") as f:
                        f.seek(start)
                        text = f.read(64 * 1024).decode(errors="replace")
                except OSError:
                    return None
                m = pattern.search(text)
                if m:
                    return m
            return None

    # ---- properties
    def _pending(self, started: str | None) -> list:
        pending = read_json(self.pending_file, {}) or {}
        try:
            since = datetime.strptime(started, "%Y-%m-%d %H:%M:%S").timestamp() if started else 0
        except ValueError:
            since = 0
        if since and since > pending.get("time", 0):
            return []
        return pending.get("keys", [])

    def view(self, running: bool, started: str | None) -> dict:
        props = parse(self.file.read_text())
        groups = {g: [] for g in GROUPS}
        for key, label, group, kind, limits, live, hint in FIELDS:
            if kind == "rule":
                m = self.query("gamerule pvp", RULE_RE) if running else None
                value = m.group(1) if m else None
            elif key in props:
                value = props[key]
            else:
                continue
            item = {"key": key, "label": label, "kind": kind, "value": value, "help": hint,
                    "live": bool(live), "locked": kind == "locked" or key in LOCKED}
            if kind == "enum":
                item["choices"] = list(limits)
            elif kind == "int":
                item["min"], item["max"] = limits
            elif kind == "text":
                item["max"] = limits[1]
            groups[group].append(item)
        advanced = [{"key": k, "value": v, "locked": k in LOCKED}
                    for k, v in props.items() if k not in BY_KEY and not HIDDEN.search(k)]
        return {"groups": [{"name": g, "fields": f} for g, f in groups.items() if f],
                "advanced": advanced, "pending": self._pending(started), "running": running}

    def save(self, values: dict, running: bool, by: str) -> dict:
        with self._lock:
            text = self.file.read_text()
            props = parse(text)
            changes, rules = {}, {}
            for key, value in values.items():
                if HIDDEN.search(key) or (key not in props and key not in BY_KEY):
                    raise SettingsError(f"{key} can't be changed here.")
                if key in LOCKED or kind_of(key) == "locked":
                    raise SettingsError(f"{BY_KEY[key][1] if key in BY_KEY else key} can't be changed here.")
                if kind_of(key) == "rule":
                    rules[key] = check(key, value, "")
                    continue
                if key not in props:
                    raise SettingsError(f"{key} isn't in server.properties.")
                clean = check(key, value, props[key])
                if clean != props[key]:
                    changes[key] = clean
            if rules and not running:
                raise SettingsError("Game rules can only be changed while the server is running.")
            if changes:
                self._backup(text)
                tmp = self.file.with_name("server.properties.tmp")
                tmp.write_text(rewrite(text, changes))
                os.replace(tmp, self.file)
            live, restart = [], []
            for key, value in {**changes, **rules}.items():
                command = BY_KEY[key][5] if key in BY_KEY else None
                if command and running:
                    self.crafty.command(command.format(LIVE_VALUE.get(key, lambda v: v)(value)))
                    live.append(key)
                elif key in changes:
                    restart.append(key)
            if restart:
                pending = read_json(self.pending_file, {}) or {}
                keys = sorted(set(pending.get("keys", [])) | set(restart))
                write_json(self.pending_file, {"keys": keys, "time": time.time(), "by": by})
            return {"changed": sorted(changes) + sorted(rules), "live": sorted(live), "restart": sorted(restart)}

    def _backup(self, text: str) -> None:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        path = self.backup_dir / f"server.properties.{time.strftime('%Y%m%d-%H%M%S')}"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(text)
        for old in sorted(self.backup_dir.glob("server.properties.*"))[:-KEEP_BACKUPS]:
            old.unlink(missing_ok=True)

    # ---- borders
    def _border_file(self, dim: str) -> Path:
        level = parse(self.file.read_text()).get("level-name", "world")
        return self.server / level / "dimensions" / "minecraft" / dim / "data" / "minecraft" / "world_border.dat"

    def borders(self, running: bool) -> list:
        set_here = read_json(self.borders_file, {}) or {}
        out = []
        for dim, name in DIMENSIONS:
            row = {"dim": dim, "name": name, "size": None, "center_x": None, "center_z": None, "live": None, "saved": None}
            path = self._border_file(dim)
            try:
                with gzip.open(path) as f:
                    b = read_nbt(f.read()).get("data", {})
                size = b.get("lerp_target") if b.get("lerp_time") else b.get("size")
                row.update(size=size, center_x=b.get("center_x"), center_z=b.get("center_z"),
                           saved=int(path.stat().st_mtime))
            except (OSError, EOFError, ValueError, struct.error):
                pass
            # The world only writes the file when it saves, so a change made here is newer until then.
            mine = set_here.get(dim)
            if mine and mine["time"] > (row["saved"] or 0):
                row.update(size=mine["size"], center_x=mine["center_x"], center_z=mine["center_z"])
            if running:
                m = self.query(f"execute in minecraft:{dim} run worldborder get", BORDER_RE)
                if m:
                    row["live"] = float(m.group(1).replace(",", ""))
            if row["size"] is not None:
                row["chunky"] = chunky(dim, row["live"] or row["size"], row["center_x"] or 0, row["center_z"] or 0)
            out.append(row)
        return out

    def set_border(self, dim: str, size: float, x: float, z: float) -> list:
        if dim not in dict(DIMENSIONS):
            raise SettingsError("Unknown dimension.")
        if not 1 <= size <= BORDER_MAX:
            raise SettingsError(f"The border size must be between 1 and {BORDER_MAX:,} blocks.")
        if not (abs(x) <= COORD_MAX and abs(z) <= COORD_MAX):
            raise SettingsError(f"The centre must be within {COORD_MAX:,} blocks of 0.")
        commands = [f"execute in minecraft:{dim} run worldborder center {num(x)} {num(z)}",
                    f"execute in minecraft:{dim} run worldborder set {num(size)}"]
        for command in commands:
            self.crafty.command(command)
        with self._lock:
            mine = read_json(self.borders_file, {}) or {}
            mine[dim] = {"size": size, "center_x": x, "center_z": z, "time": time.time()}
            write_json(self.borders_file, mine)
        return commands


def chunky(dim: str, size: float, x: float, z: float) -> list:
    """The commands to pre-generate inside a border, for the admin to run when they choose."""
    return [f"chunky world minecraft:{dim}", "chunky shape square", f"chunky center {num(x)} {num(z)}",
            f"chunky radius {num(size / 2)}", "chunky start"]
