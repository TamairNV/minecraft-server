import json
import os
import random
from datetime import datetime
from zoneinfo import ZoneInfo

os.environ.update(ACCESS_SITE="mc", CF_TEAM_DOMAIN="t.example", CF_AUD="aud", MC_SERVER_DIR="", MC_BACKUP_DIR="", MC_DATA_DIR="")

from app import progress as pg  # noqa: E402
from app import stats  # noqa: E402

LONDON = ZoneInfo("Europe/London")
NOW = datetime(2026, 11, 11, 15, tzinfo=LONDON).timestamp()


def write(server, uuid, cats):
    d = server / "world" / "players" / "stats"
    d.mkdir(parents=True, exist_ok=True)
    stats_json = {f"minecraft:{cat}": {f"minecraft:{k}": v for k, v in vals.items()} for cat, vals in cats.items()}
    (d / f"{uuid}.json").write_text(json.dumps({"stats": stats_json}))


def test_awards_use_season_delta(tmp_path):
    (tmp_path / "usercache.json").write_text(json.dumps([{"uuid": "a", "name": "Alex"}, {"uuid": "b", "name": "Bea"}]))
    write(tmp_path, "a", {"custom": {"jump": 500, "play_time": 72000}})
    write(tmp_path, "b", {"custom": {"jump": 100}})
    season = {"a": {"custom:minecraft:jump": 480, "custom:minecraft:play_time": 72000}}
    played = stats.collect(tmp_path, mark=season)
    by = {p["name"]: p for p in played}
    assert by["Alex"]["hours"] == 0 and by["Bea"]["hours"] == 0
    got = {a["title"]: a for a in stats.awards(played)}
    assert got["Bouncy"]["name"] == "Bea"  # 100 jumps beats Alex's 20 since the season started
    assert "Never logs off" not in got
    assert len(stats.ALL_AWARDS) >= 25
    assert all("flat" not in p for p in played)


def test_fun_facts():
    flat = {"custom:minecraft:walk_one_cm": 70_350_000, "mined:minecraft:stone": 800, "mined:minecraft:dirt": 30,
            "killed_by:minecraft:creeper": 4, "killed_by:minecraft:zombie": 1, "used:minecraft:torch": 312,
            "killed:minecraft:zombie": 40}
    facts = stats.fun_facts(flat)
    assert "Travelled 703.5 km: half the length of Great Britain" in facts
    assert "Favourite block to mine: Stone (800)" in facts
    assert "Nemesis: Creeper (4 deaths)" in facts
    assert "Most used: Torch (312 times)" in facts
    assert "Favourite target: Zombie (40)" in facts
    assert stats.fun_facts({}) == []


# ---- /api/challenges and /api/claim
def client(tmp_path, email="alex@example.com"):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app import play, rewards, tnv_access
    from app.engine import Engine
    from app.links import Links
    from app.tnv_access import AccessDenied, AccessUser

    class Guard:
        def check(self, token, required=None):
            if not token:
                raise AccessDenied(401, "no")
            return AccessUser(email, "user")

    tnv_access.configure(Guard())
    server = tmp_path / "server"
    server.mkdir()
    (server / "whitelist.json").write_text(json.dumps([{"uuid": "u-a", "name": "Alex"}]))
    data = tmp_path / "data"
    data.mkdir()
    (data / "links.json").write_text(json.dumps({"alex@example.com": "Alex"}))
    write(server, "u-a", {"custom": {"jump": 0}})
    sent = []

    class Crafty:
        def command(self, text):
            sent.append(text)

    def query(command, pattern, timeout=3.0):
        sent.append(command)
        text = {"list": "There are 1 of a max of 25 players online: Alex", "save-all": "Saved the game"}.get(command)
        if command.startswith("give "):
            _, name, item, count = command.split(" ", 3)
            text = f"Gave {count} [X] to {name}"
        return pattern.search("[12:00:00] [Server thread/INFO]: System chat: " + text) if text else None

    store = pg.Store(data / "progress.db")
    engine = Engine(server, data, Crafty(), query, store, clock=lambda: NOW, rng=random.Random(1))
    engine.tick()
    claims = rewards.Claims(store, query, Links(tmp_path / "none.db", data, server), sleep=lambda s: None)
    app = FastAPI()
    clock = [NOW]
    app.include_router(play.build(engine, claims, clock=lambda: clock[0]))
    app.state.claims = claims
    client.clock = clock
    return TestClient(app), engine, server, sent


HEAD = {"Cf-Access-Jwt-Assertion": "x", "Origin": "http://testserver"}


def test_challenges_payload_shape(tmp_path):
    c, engine, server, sent = client(tmp_path)
    d = c.get("/api/challenges", headers=HEAD).json()
    assert len(d["daily"]) == 8 and len(d["weekly"]) == 3
    assert [c["tier"] for c in d["daily"]] == ["hard"] * 3 + ["easy"] * 5
    card = d["weekly"][0]
    assert {"iid", "text", "target", "reward", "bonus", "bonus_winner", "progress", "mine"} <= set(card)
    assert d["me"]["linked"] and d["me"]["name"] == "Alex"
    assert d["ends"]["day"] > NOW and d["ends"]["week"] > NOW
    assert d["goal"]["target"] > 0


def test_claim_requires_same_origin(tmp_path):
    c, *_ = client(tmp_path)
    r = c.post("/api/claim", json={"key": "1:reward"}, headers={**HEAD, "Origin": "https://evil.example"})
    assert r.status_code == 403


def test_claim_flow_over_http(tmp_path):
    c, engine, server, sent = client(tmp_path)
    d = c.get("/api/challenges", headers=HEAD).json()
    card = d["daily"][0]
    r = c.post("/api/claim", json={"key": f"{card['iid']}:reward"}, headers=HEAD)
    assert r.status_code == 400  # not finished yet
    pg.record_completions(engine.store, card["iid"], {"u-a": 10 ** 9}, 1, NOW, {})
    client.clock[0] = NOW + 60
    d = c.get("/api/challenges", headers=HEAD).json()
    keys = [x["key"] for x in d["me"]["claimable"]]
    assert keys
    r = c.post("/api/claim", json={"key": keys[0]}, headers=HEAD)
    assert r.status_code == 200 and r.json()["given"]
    assert any(s.startswith("give Alex ") for s in sent)


def test_unlinked_user_sees_not_linked(tmp_path):
    c, *_ = client(tmp_path, email="stranger@example.com")
    d = c.get("/api/challenges", headers=HEAD).json()
    assert d["me"] == {"linked": False, "name": None, "claimable": [], "online": None}


def test_me_online_flag(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app import play
    c, engine, server, sent = client(tmp_path)
    claims = c.app.state.claims
    for online, want in ((lambda: {"alex"}, True), (lambda: set(), False), (lambda: None, None)):
        app = FastAPI()
        app.include_router(play.build(engine, claims, clock=lambda: NOW, online=online))
        assert TestClient(app).get("/api/challenges", headers=HEAD).json()["me"]["online"] is want


def png(color, size=16):
    import io
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGBA", (size, size), color).save(b, "PNG")
    return b.getvalue()


def make_jar(path, names=(), models=None):
    """names: flat textures; models: {item: (item json, {model path: json})}."""
    import zipfile
    with zipfile.ZipFile(path, "w") as z:
        for n in names:
            z.writestr(f"assets/minecraft/textures/{n}.png", png((200, 100, 50, 255)))
        for item, (item_json, files) in (models or {}).items():
            z.writestr(f"assets/minecraft/items/{item}.json", json.dumps(item_json))
            for mp, mj in files.items():
                z.writestr(f"assets/minecraft/models/{mp}.json", json.dumps(mj))


def cube_jar(path):
    import zipfile
    make_jar(path, models={
        "test_block": ({"model": {"type": "minecraft:model", "model": "minecraft:block/test_block"}},
                       {"block/test_block": {"parent": "minecraft:block/cube_column", "textures": {"end": "minecraft:block/t_top", "side": "minecraft:block/t_side"}},
                        "block/cube_column": {"parent": "block/cube", "textures": {"up": "#end", "north": "#side", "east": "#side"}},
                        "block/cube": {"parent": "block/block"}}),
        "table": ({"model": {"type": "minecraft:model", "model": "minecraft:block/table"}},
                  {"block/table": {"parent": "block/block", "textures": {"top": "block/r", "front": "block/b", "side": "block/g"}}}),
        "stick": ({"model": {"type": "minecraft:model", "model": "minecraft:item/stick"}},
                  {"item/stick": {"parent": "minecraft:item/generated", "textures": {"layer0": "minecraft:item/stick"}}}),
    })
    with zipfile.ZipFile(path, "a") as z:
        for name, color in {"t_top": (255, 0, 0, 255), "t_side": (0, 0, 255, 255), "r": (255, 0, 0, 255),
                            "b": (0, 0, 255, 255), "g": (0, 255, 0, 255)}.items():
            z.writestr(f"assets/minecraft/textures/block/{name}.png", png(color))
        z.writestr("assets/minecraft/textures/item/stick.png", png((10, 20, 30, 255), size=16))


def load(data):
    import io
    from PIL import Image
    return Image.open(io.BytesIO(data)).convert("RGBA")


def test_icons_from_client_jar(tmp_path):
    from app.icons import SIZE, Icons
    (tmp_path / "bluemap").mkdir()
    make_jar(tmp_path / "bluemap" / "minecraft-client-26.3.jar", ["item/diamond", "block/oak_log", "block/crafting_table_front"])
    icons = Icons(tmp_path)
    assert load(icons.get("diamond")).size == (SIZE, SIZE)
    assert icons.get("oak_log") and icons.get("crafting_table")  # no model files: flat fallback
    assert icons.get("nope") is None
    assert icons.get("../../etc/passwd") is None
    assert Icons(tmp_path / "missing").get("diamond") is None


def test_block_icons_are_shaded_cubes(tmp_path):
    from app.icons import SIZE, Icons
    (tmp_path / "bluemap").mkdir()
    cube_jar(tmp_path / "bluemap" / "minecraft-client-26.3.jar")
    icons = Icons(tmp_path)
    for item, left, right in (("table", (0, 0, 204), (0, 153, 0)), ("test_block", (0, 0, 204), (0, 0, 153))):
        im = load(icons.get(item))
        assert im.size == (SIZE, SIZE)
        assert im.getpixel((0, 0))[3] == 0 and im.getpixel((SIZE - 1, 0))[3] == 0  # corners are see-through
        assert im.getpixel((SIZE // 2, SIZE // 4))[:3] == (255, 0, 0)  # top
        assert im.getpixel((SIZE // 4, SIZE * 5 // 8))[:3] == left  # front face, a bit darker
        assert im.getpixel((SIZE * 3 // 4, SIZE * 5 // 8))[:3] == right  # side face, darker still
    flat = load(icons.get("stick"))
    assert flat.getpixel((SIZE // 2, SIZE // 2))[:3] == (10, 20, 30)


def test_icon_route(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app import play
    from app.icons import Icons
    c, engine, server, sent = client(tmp_path)
    (server / "bluemap").mkdir()
    make_jar(server / "bluemap" / "minecraft-client-26.3.jar", ["item/diamond"])
    app = FastAPI()
    app.include_router(play.build(engine, c.app.state.claims, clock=lambda: NOW, icons=Icons(server)))
    t = TestClient(app)
    r = t.get("/api/icon/diamond.png", headers=HEAD)
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and "max-age" in r.headers["cache-control"]
    assert t.get("/api/icon/stone.png", headers=HEAD).status_code == 404
    assert t.get("/api/icon/diamond.png").status_code == 401
