import json
import os
import sqlite3
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

os.environ.update(ACCESS_SITE="mc", CF_TEAM_DOMAIN="t.example", CF_AUD="aud", MC_SERVER_DIR="", MC_BACKUP_DIR="", MC_DATA_DIR="")

from app import progress as pg  # noqa: E402
from app import rewards as rw  # noqa: E402
from app.links import Links  # noqa: E402

KNOWN = {"minecraft:dirt", "minecraft:diamond", "minecraft:enchanted_book", "minecraft:oak_log"}
ONBOARD_SCHEMA = """CREATE TABLE requests (id INTEGER PRIMARY KEY AUTOINCREMENT, discord_id INTEGER NOT NULL,
discord_name TEXT NOT NULL, email TEXT NOT NULL, mc_name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
login TEXT NOT NULL DEFAULT '', whitelist TEXT NOT NULL DEFAULT '', role TEXT NOT NULL DEFAULT '', dm TEXT NOT NULL DEFAULT '',
approvals_message_id INTEGER, created_at REAL NOT NULL, decided_at REAL, decided_by TEXT NOT NULL DEFAULT '')"""


@pytest.mark.parametrize("items", [
    [{"item": "minecraft:dirt\nop x", "count": 1}], [{"item": "minecraft:dirt; op x", "count": 1}],
    [{"item": "dirt", "count": 1}], [{"item": "minecraft:dirt", "count": 0}], [{"item": "minecraft:dirt", "count": 257}],
    [{"item": "minecraft:dirt", "count": 1}] * 6, [{"item": "minecraft:not_a_thing", "count": 1}], [],
    [{"item": "minecraft:dirt", "count": True}], [{"item": "minecraft:dirt[a=b]\n", "count": 1}],
])
def test_reward_rejects_bad_items(items):
    with pytest.raises(rw.RewardError):
        rw.validate_reward(items, KNOWN)


def test_reward_accepts_components_and_unknown_registry():
    book = 'minecraft:enchanted_book[stored_enchantments={"minecraft:mending":1}]'
    assert rw.validate_reward([{"item": book, "count": 1}], KNOWN) == [{"item": book, "count": 1}]
    assert rw.validate_reward([{"item": "minecraft:whatever", "count": 2}], None)[0]["count"] == 2


def setup_server(tmp_path, onboard_rows=()):
    server = tmp_path / "server"
    server.mkdir()
    (server / "whitelist.json").write_text(json.dumps([{"uuid": "u-alex", "name": "Alex"}, {"uuid": "u-bea", "name": "Bea"}]))
    db = tmp_path / "onboard.db"
    con = sqlite3.connect(db)
    con.execute(ONBOARD_SCHEMA)
    for email, name, status in onboard_rows:
        con.execute("INSERT INTO requests (discord_id, discord_name, email, mc_name, status, created_at) VALUES (1,'d',?,?,?,0)",
                    (email, name, status))
    con.commit()
    con.close()
    return server, db


def test_links_manual_wins_and_only_approved(tmp_path):
    server, db = setup_server(tmp_path, [("Alex@Example.com", "Alex", "approved"), ("bea@example.com", "Bea", "pending")])
    links = Links(db, tmp_path / "data", server)
    assert links.player_for("alex@example.com") == {"name": "Alex", "uuid": "u-alex"}
    assert links.player_for("bea@example.com") is None
    links.set_manual("bea@example.com", "Bea")
    links.set_manual("alex@example.com", "Bea")
    assert links.player_for("ALEX@example.com") == {"name": "Bea", "uuid": "u-bea"}
    links.remove_manual("alex@example.com")
    assert links.player_for("alex@example.com")["name"] == "Alex"
    with pytest.raises(rw.LinkError):
        links.set_manual("x@example.com", "Not A Name!")


def test_links_missing_onboard_db(tmp_path):
    server, _ = setup_server(tmp_path)
    links = Links(tmp_path / "nope.db", tmp_path / "data", server)
    assert links.player_for("a@example.com") is None
    assert links.list()["onboard"] == []


class Game:
    """Answers console commands like the server would."""

    def __init__(self, online=("Alex",), fail_give=False):
        self.online, self.fail_give, self.sent = list(online), fail_give, []
        self.lock = threading.Lock()

    def query(self, command, pattern, timeout=3.0):
        with self.lock:
            self.sent.append(command)
        if command == "list":
            text = f"There are {len(self.online)} of a max of 25 players online: {', '.join(self.online)}"
        elif command.startswith("give "):
            _, name, item, count = command.split(" ", 3)
            text = "Unknown item" if self.fail_give else f"Gave {count} [Thing] to {name}"
        else:
            return None
        return pattern.search(text)

    def gives(self):
        return [c for c in self.sent if c.startswith("give ")]


DAY = datetime(2026, 11, 11, tzinfo=ZoneInfo("Europe/London")).timestamp()


def setup_claims(tmp_path, game, bonus_for=None):
    server, db = setup_server(tmp_path, [("alex@example.com", "Alex", "approved"), ("bea@example.com", "Bea", "approved")])
    store = pg.Store(tmp_path / "p.db")
    iid = store.add_instance("daily", "2026-11-11", {"id": "d1", "text": "Jump {n} times", "target": 1,
                                                    "metric": {"cat": "custom", "keys": ["minecraft:jump"]},
                                                    "reward": [{"item": "minecraft:oak_log", "count": 32}]})
    pg.record_completions(store, iid, {"u-alex": 5, "u-bea": 2}, 1, DAY + 10, {})
    claims = rw.Claims(store, game.query, Links(db, tmp_path / "data", server), sleep=lambda s: None)
    return claims, iid


def test_claim_gives_and_marks_done(tmp_path):
    game = Game()
    claims, iid = setup_claims(tmp_path, game)
    assert [c["key"] for c in claims.claimable("u-alex", DAY + 60)] == [f"{iid}:reward"]
    r = claims.claim("alex@example.com", f"{iid}:reward", DAY + 60)
    assert r["given"] == [{"item": "minecraft:oak_log", "count": 32}]
    assert game.gives() == ["give Alex minecraft:oak_log 32"]
    assert claims.claimable("u-alex", DAY + 120) == []
    with pytest.raises(rw.ClaimError):
        claims.claim("alex@example.com", f"{iid}:reward", DAY + 200)
    assert len(game.gives()) == 1


def test_claim_offline_refused_and_retryable(tmp_path):
    game = Game(online=())
    claims, iid = setup_claims(tmp_path, game)
    with pytest.raises(rw.ClaimError, match="Join the server"):
        claims.claim("bea@example.com", f"{iid}:reward", DAY + 60)
    game.online = ["Bea"]
    claims.claim("bea@example.com", f"{iid}:reward", DAY + 70)
    assert game.gives() == ["give Bea minecraft:oak_log 32"]


def test_claim_twice_gives_once(tmp_path):
    game = Game()
    claims, iid = setup_claims(tmp_path, game)
    results = []

    def go():
        try:
            claims.claim("alex@example.com", f"{iid}:reward", DAY + 60)
            results.append("ok")
        except rw.ClaimError:
            results.append("no")

    ts = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert results.count("ok") == 1 and len(game.gives()) == 1


def test_claim_expired_refused(tmp_path):
    game = Game()
    claims, iid = setup_claims(tmp_path, game)
    late = DAY + 8 * 86400 + 3600
    assert claims.claimable("u-alex", late) == []
    with pytest.raises(rw.ClaimError):
        claims.claim("alex@example.com", f"{iid}:reward", late)


def test_claim_not_completed_or_unlinked(tmp_path):
    game = Game(online=("Alex", "Bea"))
    claims, iid = setup_claims(tmp_path, game)
    with pytest.raises(rw.ClaimError, match="linked"):
        claims.claim("stranger@example.com", f"{iid}:reward", DAY + 60)
    with pytest.raises(rw.ClaimError):
        claims.claim("alex@example.com", "999:reward", DAY + 60)
    with pytest.raises(rw.ClaimError):
        claims.claim("alex@example.com", f"{iid}:bonus", DAY + 60)  # daily has no bonus


def test_claim_rate_limited(tmp_path):
    game = Game(online=())
    claims, iid = setup_claims(tmp_path, game)
    with pytest.raises(rw.ClaimError, match="Join"):
        claims.claim("alex@example.com", f"{iid}:reward", DAY + 60)
    with pytest.raises(rw.ClaimError, match="moment"):
        claims.claim("alex@example.com", f"{iid}:reward", DAY + 60)


def test_give_failure_not_marked(tmp_path):
    game = Game(fail_give=True)
    claims, iid = setup_claims(tmp_path, game)
    with pytest.raises(rw.ClaimError):
        claims.claim("alex@example.com", f"{iid}:reward", DAY + 60)
    assert [c["key"] for c in claims.claimable("u-alex", DAY + 60)] == [f"{iid}:reward"]


def test_weekly_bonus_only_for_first(tmp_path):
    game = Game(online=("Alex", "Bea"))
    server, db = setup_server(tmp_path, [("alex@example.com", "Alex", "approved"), ("bea@example.com", "Bea", "approved")])
    store = pg.Store(tmp_path / "p.db")
    iid = store.add_instance("weekly", "2026-11-09", {"id": "w1", "text": "x {n}", "target": 1, "metric": {"cat": "custom", "keys": ["a"]},
                                                     "reward": [{"item": "minecraft:dirt", "count": 1}],
                                                     "bonus": [{"item": "minecraft:diamond", "count": 3}]})
    pg.record_completions(store, iid, {"u-bea": 9}, 1, DAY, {})
    pg.record_completions(store, iid, {"u-alex": 9}, 1, DAY + 60, {})
    claims = rw.Claims(store, game.query, Links(db, tmp_path / "data", server), sleep=lambda s: None)
    assert sorted(c["key"] for c in claims.claimable("u-bea", DAY + 100)) == [f"{iid}:bonus", f"{iid}:reward"]
    assert [c["key"] for c in claims.claimable("u-alex", DAY + 100)] == [f"{iid}:reward"]
    # Weekly rewards last 14 days after the week (week of 2026-11-09 ends 11-16, so until 11-30).
    assert claims.claimable("u-bea", DAY + 18 * 86400) != []
    assert claims.claimable("u-bea", DAY + 20 * 86400) == []
