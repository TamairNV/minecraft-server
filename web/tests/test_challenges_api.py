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
    client.clock = clock
    return TestClient(app), engine, server, sent


HEAD = {"Cf-Access-Jwt-Assertion": "x", "Origin": "http://testserver"}


def test_challenges_payload_shape(tmp_path):
    c, engine, server, sent = client(tmp_path)
    d = c.get("/api/challenges", headers=HEAD).json()
    assert len(d["daily"]) == 2 and len(d["weekly"]) == 3
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
    assert d["me"] == {"linked": False, "name": None, "claimable": []}
