"""Admin area. Every route needs the `admin` role from the access panel."""
import json
import os
import re
import shutil
import threading
import time
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .crafty import CraftyError
from .pregen import Pregen
from .settings import ServerSettings, SettingsError
from .tnv_access import AccessUser, require_role
from .worlds import WorldError, read_json

PLAYER_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
LOG_START = 64 * 1024
LOG_MAX = 512 * 1024


def same_origin(request: Request) -> None:
    """A login cookie alone must not be able to fire admin actions from another site."""
    origin = urlparse(request.headers.get("origin", "")).netloc
    if not origin or origin != request.headers.get("host"):
        raise HTTPException(403, "That request didn't come from this site.")


admin_user = require_role("admin")


class Command(BaseModel):
    command: str


class Player(BaseModel):
    name: str
    action: str


class NewWorld(BaseModel):
    name: str
    seed: str = ""


class Restore(BaseModel):
    name: str
    backup: str


class Swap(BaseModel):
    slug: str
    confirm: str


class Settings(BaseModel):
    values: dict[str, str | bool | int]


class Border(BaseModel):
    dim: str
    size: float
    center_x: float = 0
    center_z: float = 0


class PregenStart(BaseModel):
    dim: str
    radius: float
    center_x: float = 0
    center_z: float = 0
    shape: str = "square"


def build(server: Path, data: Path, crafty, worlds, ping, packs) -> APIRouter:
    router = APIRouter(prefix="/admin/api", dependencies=[Depends(admin_user)])
    log_file = server / "logs" / "latest.log"
    audit_file = data / "audit.jsonl"
    audit_lock = threading.Lock()
    settings = ServerSettings(server, data, crafty)
    chunks = Pregen(server, data, crafty)

    def audit(user: AccessUser, action: str, detail: str = "") -> None:
        line = json.dumps({"time": int(time.time()), "who": user.email, "action": action, "detail": detail})
        with audit_lock, open(audit_file, "a") as f:
            f.write(line + "\n")

    def idle() -> None:
        if worlds.busy():
            raise HTTPException(409, "A world job is running. Try again when it finishes.")

    def need_running() -> None:
        idle()
        try:
            if not crafty.running():
                raise HTTPException(409, "The server is stopped.")
        except CraftyError as e:
            raise HTTPException(502, str(e)) from None

    def crafty_do(fn, *args) -> None:
        try:
            fn(*args)
        except CraftyError as e:
            raise HTTPException(502, str(e)) from None

    @router.get("/logs")
    def logs(after: int = -1):
        try:
            size = log_file.stat().st_size
        except OSError:
            return {"lines": [], "next": 0, "reset": False}
        reset = after < 0 or after > size
        start = max(0, size - LOG_START) if reset else after
        if size - start > LOG_MAX:
            start = size - LOG_MAX
        with open(log_file, "rb") as f:
            f.seek(start)
            raw = f.read(size - start)
        if reset and start > 0 and b"\n" in raw:
            consumed = raw.index(b"\n") + 1
            raw, start = raw[consumed:], start + consumed
        # Only hand out complete lines, so a half-written one isn't split across polls.
        end = raw.rfind(b"\n") + 1
        text = ANSI_RE.sub("", raw[:end].decode(errors="replace"))
        return {"lines": text.splitlines(), "next": start + end, "reset": reset}

    @router.post("/command", dependencies=[Depends(same_origin)])
    def command(body: Command, user: AccessUser = Depends(admin_user)):
        text = body.command.strip().removeprefix("/")
        if not text or len(text) > 500 or any(c in text for c in "\r\n"):
            raise HTTPException(400, "A command is a single line of up to 500 characters.")
        need_running()
        audit(user, "command", text)
        crafty_do(crafty.command, text)
        return {"ok": True}

    @router.post("/server/{action}", dependencies=[Depends(same_origin)])
    def server_action(action: str, user: AccessUser = Depends(admin_user)):
        if action not in ("start", "stop", "restart"):
            raise HTTPException(404, "Unknown action.")
        idle()
        audit(user, "server", action)
        crafty_do(crafty.action, f"{action}_server")
        return {"ok": True}

    @router.get("/whitelist")
    def whitelist():
        ops = {e.get("uuid") for e in read_json(server / "ops.json", []) or []}
        players = read_json(server / "whitelist.json", []) or []
        rows = [{"name": p.get("name"), "uuid": p.get("uuid"), "op": p.get("uuid") in ops} for p in players]
        return {"players": sorted(rows, key=lambda p: (p["name"] or "").lower())}

    @router.post("/whitelist", dependencies=[Depends(same_origin)])
    def whitelist_edit(body: Player, user: AccessUser = Depends(admin_user)):
        if not PLAYER_RE.match(body.name):
            raise HTTPException(400, "Minecraft names are 3 to 16 letters, numbers or underscores.")
        if body.action not in ("add", "remove"):
            raise HTTPException(400, "Choose add or remove.")
        need_running()
        audit(user, f"whitelist {body.action}", body.name)
        crafty_do(crafty.command, f"whitelist {body.action} {body.name}")
        return {"ok": True}

    @router.get("/backups")
    def backups():
        return {"backups": worlds.list_backups(), "busy": worlds.busy()}

    @router.post("/backups", dependencies=[Depends(same_origin)])
    def backup_now(user: AccessUser = Depends(admin_user)):
        idle()
        audit(user, "backup")
        crafty_do(crafty.backup)
        return {"ok": True}

    @router.get("/worlds")
    def world_list():
        return {"worlds": worlds.list(), "job": worlds.job(), "busy": worlds.busy()}

    def world_do(fn, *args):
        try:
            return fn(*args)
        except WorldError as e:
            raise HTTPException(400, str(e)) from None

    @router.post("/worlds", dependencies=[Depends(same_origin)])
    def world_new(body: NewWorld, user: AccessUser = Depends(admin_user)):
        slug = world_do(worlds.create_new, body.name, body.seed)
        audit(user, "world new", slug)
        return {"slug": slug}

    @router.post("/worlds/restore", dependencies=[Depends(same_origin)])
    def world_restore(body: Restore, user: AccessUser = Depends(admin_user)):
        world_do(worlds.start_restore, body.name, body.backup, user.email)
        audit(user, "world restore", f"{body.name} from {body.backup}")
        return {"ok": True}

    @router.post("/worlds/swap", dependencies=[Depends(same_origin)])
    def world_swap(body: Swap, user: AccessUser = Depends(admin_user)):
        target = next((w for w in worlds.list() if w["slug"] == body.slug), None)
        if target is None:
            raise HTTPException(404, "That world doesn't exist.")
        if body.confirm != target["name"]:
            raise HTTPException(400, "Type the world's name exactly to confirm.")
        world_do(worlds.start_swap, body.slug, user.email)
        audit(user, "world swap", body.slug)
        return {"ok": True}

    def is_running() -> bool:
        try:
            return crafty.running()
        except CraftyError:
            return False

    def settings_do(fn, *args):
        try:
            return fn(*args)
        except SettingsError as e:
            raise HTTPException(400, str(e)) from None
        except CraftyError as e:
            raise HTTPException(502, str(e)) from None

    @router.get("/settings")
    def settings_view():
        started, online = None, None
        try:
            s = crafty.stats()
            running, started = bool(s.get("running")), s.get("started")
        except CraftyError:
            running = False
        if running:
            try:
                online = ping().get("players", {}).get("online", 0)
            except Exception:  # still starting or shutting down
                pass
        return {**settings_do(settings.view, running, started), "online": online}

    @router.post("/settings", dependencies=[Depends(same_origin)])
    def settings_save(body: Settings, user: AccessUser = Depends(admin_user)):
        idle()
        result = settings_do(settings.save, body.values, is_running(), user.email)
        if result["changed"]:
            detail = ", ".join(f"{k}={body.values[k]}" for k in result["changed"])
            audit(user, "settings", detail + (f" (live: {', '.join(result['live'])})" if result["live"] else ""))
        return result

    @router.get("/borders")
    def borders():
        running = is_running()
        return {"borders": settings.borders(running), "running": running}

    @router.post("/borders", dependencies=[Depends(same_origin)])
    def border_set(body: Border, user: AccessUser = Depends(admin_user)):
        need_running()
        commands = settings_do(settings.set_border, body.dim, body.size, body.center_x, body.center_z)
        audit(user, "border", "; ".join(commands))
        return {"ok": True}

    @router.get("/pregen")
    def pregen_state():
        running = is_running()
        return {"worlds": chunks.state(running), "running": running}

    @router.post("/pregen/start", dependencies=[Depends(same_origin)])
    def pregen_start(body: PregenStart, user: AccessUser = Depends(admin_user)):
        need_running()
        commands = settings_do(chunks.start, body.dim, body.radius, body.center_x, body.center_z, body.shape, user.email)
        audit(user, "pregen start", "; ".join(commands))
        return {"ok": True}

    @router.post("/pregen/{action}", dependencies=[Depends(same_origin)])
    def pregen_control(action: str, body: dict, user: AccessUser = Depends(admin_user)):
        need_running()
        commands = settings_do(chunks.control, str(body.get("dim", "")), action)
        audit(user, f"pregen {action}", "; ".join(commands))
        return {"ok": True}

    @router.get("/modpack")
    def modpack_state():
        return {"job": packs.job(), "current": packs.current(), "builds": packs.builds()}

    @router.post("/modpack/build", dependencies=[Depends(same_origin)])
    def modpack_build(user: AccessUser = Depends(admin_user)):
        def finished(job: dict) -> None:
            if job["status"] == "done":
                m = job["manifest"]
                skipped = ", ".join(s["slug"] for s in m["skipped"]) or "none"
                audit(user, "modpack built", f"{m['file']}: {len(m['mods'])} mods, skipped: {skipped}")
            else:
                audit(user, "modpack failed", job["error"])

        job = packs.job()
        if job and job["status"] == "running":
            raise HTTPException(409, "A pack build is already running.")
        # Logged first: a build that fails fast would otherwise write its result before this.
        audit(user, "modpack build")
        if not packs.start(user.email, finished):
            raise HTTPException(409, "A pack build is already running.")
        return {"ok": True}

    @router.get("/system")
    def system():
        out = {"crafty": None, "disks": [], "memory": None, "last_backup": None}
        try:
            s = crafty.stats()
            out["crafty"] = {k: s.get(k) for k in ("running", "crashed", "cpu", "mem", "started", "version", "online", "max")}
            out["joinable"] = False
            if s.get("running"):
                try:
                    live = ping().get("players", {})
                    out["joinable"] = True
                    out["crafty"].update(online=live.get("online", 0), max=live.get("max", 0))
                except Exception:  # still starting or shutting down
                    pass
        except CraftyError:
            pass
        for label, path in (("SSD (live world)", server), ("Hard drive (backups)", worlds.backups)):
            try:
                d = shutil.disk_usage(path)
                out["disks"].append({"label": label, "used": d.used, "total": d.total})
            except OSError:
                pass
        try:
            info = dict(line.split(":", 1) for line in open("/proc/meminfo"))
            kb = lambda k: int(info[k].split()[0]) * 1024
            out["memory"] = {"total": kb("MemTotal"), "available": kb("MemAvailable")}
        except (OSError, KeyError, ValueError):
            pass
        files = worlds.list_backups()
        if files:
            out["last_backup"] = files[0]["time"]
        return out

    @router.get("/audit")
    def audit_log():
        try:
            lines = audit_file.read_text().splitlines()[-40:]
        except OSError:
            return {"entries": []}
        return {"entries": [json.loads(line) for line in reversed(lines)]}

    return router
