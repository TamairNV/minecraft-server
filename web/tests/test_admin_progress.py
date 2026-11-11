import json
import os
import random
from datetime import datetime
from zoneinfo import ZoneInfo

os.environ.update(ACCESS_SITE="mc", CF_TEAM_DOMAIN="t.example", CF_AUD="aud", MC_SERVER_DIR="", MC_BACKUP_DIR="", MC_DATA_DIR="")

from app import progress as pg  # noqa: E402

NOW = datetime(2026, 11, 11, 15, tzinfo=ZoneInfo("Europe/London")).timestamp()
HEAD = {"Cf-Access-Jwt-Assertion": "x", "Origin": "http://testserver"}


def client(tmp_path, role="admin"):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app import admin, modpack, rewards, tnv_access
    from app.engine import Engine
    from app.links import Links
    from app.tnv_access import AccessDenied, AccessUser

    class Guard:
        def check(self, token, required=None):
            if not token:
                raise AccessDenied(401, "no")
            order = ["user", "admin"]
            if required and order.index(role) < order.index(required):
                raise AccessDenied(403, "no")
            return AccessUser("boss@example.com", role)

    tnv_access.configure(Guard())
    server = tmp_path / "server"
    (server / "logs").mkdir(parents=True)
    (server / "logs" / "latest.log").write_text("")
    (server / "whitelist.json").write_text(json.dumps([{"uuid": "u-t", "name": "Tamair"}]))
    data = tmp_path / "data"
    data.mkdir()
    sent = []

    class Crafty:
        running = lambda self: True
        command = lambda self, text: sent.append(text)

    class Worlds:
        backups = tmp_path
        busy = lambda self: False

    def query(command, pattern, timeout=3.0):
        sent.append(command)
        text = {"list": "There are 1 of a max of 25 players online: Tamair", "save-all": "Saved the game"}.get(command)
        if command.startswith("give "):
            _, name, item, count = command.split(" ", 3)
            text = f"Gave {count} [X] to {name}"
        return pattern.search(text) if text else None

    store = pg.Store(data / "progress.db")
    engine = Engine(server, data, Crafty(), query, store, clock=lambda: NOW, rng=random.Random(2))
    engine.tick()
    claims = rewards.Claims(store, query, Links(tmp_path / "none.db", data, server), sleep=lambda s: None)
    app = FastAPI()
    app.include_router(admin.build(server, data, Crafty(), Worlds(), None, modpack.Packs(server, data / "modpack", fetch=lambda u: None),
                                   engine=engine, claims=claims, clock=lambda: NOW))
    return TestClient(app), engine, data, sent


def test_admin_only(tmp_path):
    c, *_ = client(tmp_path, role="user")
    assert c.get("/admin/api/progress", headers=HEAD).status_code == 403
    assert c.post("/admin/api/progress/reset", json={"confirm": "reset"}, headers=HEAD).status_code == 403


def test_overview(tmp_path):
    c, *_ = client(tmp_path)
    d = c.get("/admin/api/progress", headers=HEAD).json()
    assert len(d["daily"]) == 2 and len(d["weekly"]) == 3
    assert len(d["pools"]["daily"]) >= 20 and d["goals"] and d["links"] == {"onboard": [], "manual": []}


def test_bad_pool_rejected_and_file_unchanged(tmp_path):
    c, engine, data, _ = client(tmp_path)
    pools = c.get("/admin/api/progress", headers=HEAD).json()["pools"]
    pools["daily"][0]["reward"] = [{"item": "minecraft:dirt\nop me", "count": 1}]
    r = c.post("/admin/api/progress/pools", json={"pools": pools}, headers=HEAD)
    assert r.status_code == 400 and "item id" in r.json()["detail"]
    assert not (data / "challenges.json").exists()
    pools["daily"][0]["reward"] = [{"item": "minecraft:dirt", "count": 5}]
    assert c.post("/admin/api/progress/pools", json={"pools": pools}, headers=HEAD).status_code == 200
    assert json.loads((data / "challenges.json").read_text())["daily"][0]["reward"][0]["count"] == 5


def test_reset_needs_typed_confirm(tmp_path):
    c, engine, data, _ = client(tmp_path)
    assert c.post("/admin/api/progress/reset", json={"confirm": "yes"}, headers=HEAD).status_code == 400
    assert c.post("/admin/api/progress/reset", json={"confirm": "reset"}, headers=HEAD).status_code == 200
    assert list((data / "progress-backups").glob("*.db"))


def test_swap_keeps_claims(tmp_path):
    c, engine, data, _ = client(tmp_path)
    d = c.get("/admin/api/progress", headers=HEAD).json()
    iid = d["daily"][0]["iid"]
    pg.record_completions(engine.store, iid, {"u-t": 1}, 1, NOW, {})
    other = next(e["id"] for e in d["pools"]["daily"] if e["id"] not in {x["id"] for x in d["daily"]})
    assert c.post("/admin/api/progress/swap", json={"iid": iid, "pool_id": other}, headers=HEAD).status_code == 200
    assert engine.store.instance(iid)["id"] == other and pg.completed(engine.store, iid) == {"u-t"}


def test_links_and_test_reward(tmp_path):
    c, engine, data, sent = client(tmp_path)
    r = c.post("/admin/api/progress/test-reward", json={"items": [{"item": "minecraft:dirt", "count": 2}]}, headers=HEAD)
    assert r.status_code == 400 and "linked" in r.json()["detail"]
    assert c.post("/admin/api/progress/links", json={"email": "boss@example.com", "name": "Tamair"}, headers=HEAD).status_code == 200
    assert c.post("/admin/api/progress/links", json={"email": "x@example.com", "name": "bad name"}, headers=HEAD).status_code == 400
    r = c.post("/admin/api/progress/test-reward", json={"items": [{"item": "minecraft:dirt", "count": 2}]}, headers=HEAD)
    assert r.status_code == 200 and "give Tamair minecraft:dirt 2" in sent
    assert c.post("/admin/api/progress/links/remove", json={"email": "boss@example.com"}, headers=HEAD).status_code == 200


def test_give_again(tmp_path):
    c, engine, data, sent = client(tmp_path)
    iid = c.get("/admin/api/progress", headers=HEAD).json()["daily"][0]["iid"]
    r = c.post("/admin/api/progress/give-again", json={"key": f"{iid}:reward", "name": "Tamair"}, headers=HEAD)
    assert r.status_code == 200 and any(s.startswith("give Tamair ") for s in sent)
    assert c.post("/admin/api/progress/give-again", json={"key": "999:reward", "name": "Tamair"}, headers=HEAD).status_code == 400


def test_writes_are_audited(tmp_path):
    c, engine, data, _ = client(tmp_path)
    c.post("/admin/api/progress/reroll", json={"kind": "daily"}, headers=HEAD)
    c.post("/admin/api/progress/reset", json={"confirm": "reset"}, headers=HEAD)
    actions = [json.loads(l)["action"] for l in (data / "audit.jsonl").read_text().splitlines()]
    assert actions == ["challenges reroll", "stats reset"]


def test_other_sites_cant_post(tmp_path):
    c, *_ = client(tmp_path)
    bad = {**HEAD, "Origin": "https://evil.example"}
    assert c.post("/admin/api/progress/reset", json={"confirm": "reset"}, headers=bad).status_code == 403
