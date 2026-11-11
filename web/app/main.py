import asyncio
import hashlib
import html
import json
import logging
import os
import threading
import time
from pathlib import Path

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import admin, modpack, play, slp, stats
from . import progress as pg
from . import rewards
from .engine import Engine
from .icons import Icons
from .links import Links
from .settings import ServerSettings
from .crafty import Crafty
from .skins import Skins
from .tnv_access import AccessUser, current_user, require_role
from .worlds import Worlds, read_json

SERVER = Path(os.environ.get("MC_SERVER_DIR", "/server"))
BACKUPS = Path(os.environ.get("MC_BACKUP_DIR", "/backups"))
DATA = Path(os.environ.get("MC_DATA_DIR", "/data"))
CRAFTY_LAN_URL = os.environ.get("CRAFTY_LAN_URL", "https://192.168.1.199:8444/")
HOST = os.environ.get("MC_HOST", "crafty")
PORT = int(os.environ.get("MC_PORT", "25565"))
ADDRESS = os.environ.get("MC_PUBLIC_ADDRESS", "").strip()
BORDER = int(os.environ.get("MC_BORDER", "10000"))
HERE = Path(__file__).parent

crafty = Crafty()


def blocking_ping():
    return asyncio.run(asyncio.wait_for(slp.ping(HOST, PORT), 3))


worlds = Worlds(SERVER, BACKUPS, crafty, blocking_ping)
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
_cache: dict = {}
# Cloudflare caches static files, so asset URLs change whenever their content does.
ASSET_V = hashlib.sha256(b"".join(f.read_bytes() for f in sorted((HERE / "static").rglob("*")) if f.is_file())).hexdigest()[:10]
skins = Skins(DATA / "skins")
packs = modpack.Packs(SERVER, DATA / "modpack")
ONBOARD_DB = Path(os.environ.get("ONBOARD_DB", "/onboard/onboard.db"))
console = ServerSettings(SERVER, DATA, crafty)  # its query() sends a command and reads the answer from the log
progress_store = pg.Store(DATA / "progress.db")
links = Links(ONBOARD_DB, DATA, SERVER)
audit_lock = threading.Lock()


def player_audit(who: str, action: str, detail: str) -> None:
    """Players' claims go in the same audit log as admin actions."""
    line = json.dumps({"time": int(time.time()), "who": who, "action": action, "detail": detail})
    with audit_lock, open(DATA / "audit.jsonl", "a") as f:
        f.write(line + "\n")


claims = rewards.Claims(progress_store, console.query, links, audit=player_audit)
engine = Engine(SERVER, DATA, crafty, console.query, progress_store)
rewards.use_registry(SERVER)


async def cached(key: str, ttl: float, fn):
    """The page is public, so visits must never drive load onto the game server."""
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    value = await fn()
    _cache[key] = (time.monotonic(), value)
    return value


async def status() -> dict:
    try:
        raw = await asyncio.wait_for(slp.ping(HOST, PORT), 3)
    except (OSError, ValueError, asyncio.TimeoutError, asyncio.IncompleteReadError):
        return {"online": False, "players": [], "count": 0, "max": 0, "version": None}
    p = raw.get("players", {})
    return {
        "online": True,
        "count": p.get("online", 0),
        "max": p.get("max", 0),
        "players": [{"name": s["name"], "uuid": s["id"]} for s in p.get("sample", []) if s.get("id")],
        "version": raw.get("version", {}).get("name"),
    }


def build_overview(state: dict) -> dict:
    played = stats.collect(SERVER, mark=progress_store.mark("season") or {})
    wins = engine.hall_counts()
    for p in played:
        if wins.get(p["uuid"]):
            p["facts"].append(f"Weekly wins this season: {wins[p['uuid']]}")
    whitelist = read_json(SERVER / "whitelist.json", []) or []
    ops = {e.get("uuid") for e in read_json(SERVER / "ops.json", []) or []}
    players = stats.merge_players(whitelist, state["players"], played, ops)
    found = skins.get_many([p["uuid"] for p in players])
    for p in players:
        p["skin"] = found[p["uuid"]]
    return {
        "players": players,
        "awards": stats.awards(played),
        "week": engine.week_boards(),
        "goal": engine.goals_state()["current"],
        "totals": {
            "whitelisted": len(whitelist),
            "hours": round(sum(p["hours"] for p in played), 1),
            "deaths": sum(p["deaths"] for p in played),
            "mined": sum(p["mined"] for p in played),
            "km": round(sum(p["km"] for p in played), 1),
        },
    }


async def world_size() -> int:
    return await asyncio.to_thread(stats.world_bytes, SERVER)


@app.on_event("startup")
def start_worlds():
    worlds.setup()
    threading.Thread(target=recover_when_ready, daemon=True, name="world-recover").start()
    threading.Thread(target=progress_loop, daemon=True, name="progress").start()


def progress_loop():
    """Day/week rollovers, challenge completions and community goals, once a minute."""
    time.sleep(20)
    while True:
        try:
            engine.tick()
        except Exception:
            logging.getLogger("mc").exception("progress tick failed")
        time.sleep(60)


def recover_when_ready():
    for _ in range(60):
        try:
            crafty.stats()
            break
        except Exception:
            time.sleep(5)
    try:
        worlds.recover()
    except Exception:
        logging.getLogger("mc").exception("couldn't recover an interrupted world swap")


@app.exception_handler(StarletteHTTPException)
async def errors(request: Request, exc: StarletteHTTPException):
    if request.url.path.startswith(("/api/", "/admin/api/", "/authz")):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return HTMLResponse(f"<!doctype html><title>TNV SMP</title><p>{html.escape(str(exc.detail))}</p>", status_code=exc.status_code)


app.include_router(admin.build(SERVER, DATA, crafty, worlds, blocking_ping, packs, engine=engine, claims=claims, console=console))
_online = {"at": -1e9, "value": None}


def online_names():
    """Who's in game, from the same ping as the home page (cached 10 s). None if the ping can't say for sure."""
    if time.monotonic() - _online["at"] < 10:
        return _online["value"]
    try:
        raw = blocking_ping()
    except (OSError, ValueError, asyncio.TimeoutError, asyncio.IncompleteReadError):
        value = set()  # server down: nobody can claim
    else:
        p = raw.get("players", {})
        named = [s["name"] for s in p.get("sample", []) if s.get("name") and s.get("id") != "00000000-0000-0000-0000-000000000000"]
        value = {n.lower() for n in named} if len(named) >= p.get("online", 0) else None
    _online.update(at=time.monotonic(), value=value)
    return value


app.include_router(play.build(engine, claims, online=online_names, icons=Icons(SERVER)))


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/authz")
def authz(user: AccessUser = Depends(current_user)):
    """nginx asks this before serving each map request."""
    return Response(status_code=204)


@app.get("/api/status", dependencies=[Depends(current_user)])
async def api_status():
    return await cached("status", 10, status)


@app.get("/api/overview", dependencies=[Depends(current_user)])
async def api_overview():
    state = await cached("status", 10, status)
    board = await cached("overview", 15, lambda: asyncio.to_thread(build_overview, state))
    size = await cached("size", 600, world_size)
    return {**board, "status": state, "world_bytes": size, "border": BORDER}


@app.get("/api/skin/{uuid}.png", dependencies=[Depends(current_user)])
def api_skin(uuid: str):
    path = skins.file(uuid)
    if path is None:
        raise StarletteHTTPException(404, "No skin saved for that player.")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


def page(request: Request, user: AccessUser, name: str, active: str, **extra):
    return templates.TemplateResponse(request, name, {
        "address": ADDRESS, "border": BORDER, "v": ASSET_V, "email": user.email,
        "is_admin": user.role == "admin", "active": active, **extra})


@app.get("/")
def home(request: Request, user: AccessUser = Depends(current_user)):
    return page(request, user, "home.html", "home")


@app.get("/players")
def players_page(request: Request, user: AccessUser = Depends(current_user)):
    return page(request, user, "players.html", "players")


@app.get("/challenges")
def challenges_page(request: Request, user: AccessUser = Depends(current_user)):
    return page(request, user, "challenges.html", "challenges")


@app.get("/world")
def world_page(request: Request, user: AccessUser = Depends(current_user)):
    return page(request, user, "world.html", "world")


@app.get("/mods")
def mods_page(request: Request, user: AccessUser = Depends(current_user)):
    return page(request, user, "mods.html", "mods", pack=packs.current())


@app.get("/mods/download", dependencies=[Depends(current_user)])
def mods_download():
    pack = packs.current_file()
    if pack is None:
        raise StarletteHTTPException(404, "The pack hasn't been built yet.")
    return FileResponse(pack, media_type="application/x-modrinth-modpack+zip", filename=pack.name, headers={"Cache-Control": "private, no-cache"})


@app.get("/admin")
def admin_page(request: Request, user: AccessUser = Depends(require_role("admin"))):
    return page(request, user, "admin.html", "admin", crafty_url=CRAFTY_LAN_URL)
