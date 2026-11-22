import json
import os
import random
from datetime import datetime
from zoneinfo import ZoneInfo

os.environ.update(ACCESS_SITE="mc", CF_TEAM_DOMAIN="t.example", CF_AUD="aud", MC_SERVER_DIR="", MC_BACKUP_DIR="", MC_DATA_DIR="")

import pytest  # noqa: E402

from app import challenges_default as cd  # noqa: E402
from app import progress as pg  # noqa: E402
from app.engine import Engine  # noqa: E402

LONDON = ZoneInfo("Europe/London")


def at(*a):
    return datetime(*a, tzinfo=LONDON).timestamp()


class World:
    def __init__(self, tmp_path):
        self.server = tmp_path / "server"
        self.stats = self.server / "world" / "players" / "stats"
        self.stats.mkdir(parents=True)
        (self.server / "whitelist.json").write_text(json.dumps([{"uuid": "u-a", "name": "Alex"}, {"uuid": "u-b", "name": "Bea"}]))
        self.sent, self.up, self.pending = [], True, {}

    def set(self, uuid, **custom):
        stats = {"minecraft:custom": {f"minecraft:{k}": v for k, v in custom.items()}}
        (self.stats / f"{uuid}.json").write_text(json.dumps({"stats": stats}))

    def query(self, command, pattern, timeout=3.0):
        self.sent.append(command)
        if self.up and command == "save-all":
            for uuid, custom in self.pending.items():  # the game flushes what it had in memory
                self.set(uuid, **custom)
            self.pending = {}
            return pattern.search("Saved the game")
        return None

    def command(self, text):
        if not self.up:
            from app.crafty import CraftyError
            raise CraftyError("down")
        self.sent.append(text)


def pools():
    def e(i, kind):
        x = {"id": f"{kind[0]}{i}", "text": "Jump {n} times", "metric": {"cat": "custom", "keys": ["minecraft:jump"]},
             "target": 10, "reward": [{"item": "minecraft:dirt", "count": 1}], "enabled": True}
        if kind == "weekly":
            x["bonus"] = [{"item": "minecraft:diamond", "count": 1}]
        else:
            x["tier"] = "hard" if i < 6 else "easy"
        return x
    return {"daily": [e(i, "daily") for i in range(16)], "weekly": [e(i, "weekly") for i in range(6)]}


def engine(tmp_path, world, clock):
    data = tmp_path / "data"
    data.mkdir()
    (data / "challenges.json").write_text(json.dumps(pools()))
    (data / "goals.json").write_text(json.dumps([
        {"id": "g1", "text": "Jump {n} times together", "metric": {"cat": "custom", "keys": ["minecraft:jump"]}, "target": 30},
        {"id": "g2", "text": "Jump {n} times together", "metric": {"cat": "custom", "keys": ["minecraft:jump"]}, "target": 1000}]))
    store = pg.Store(data / "progress.db")
    return Engine(world.server, data, world, world.query, store, clock=clock, rng=random.Random(3), sleep=lambda s: None), store


def test_first_tick_takes_season_day_week_and_picks(tmp_path):
    w = World(tmp_path)
    w.set("u-a", jump=100)  # test data from before: must not count
    now = [at(2026, 11, 11, 15)]
    e, s = engine(tmp_path, w, lambda: now[0])
    e.tick()
    assert s.mark("season") == s.mark("day:2026-11-11") == s.mark("week:2026-11-09") == {"u-a": {"custom:minecraft:jump": 100}}
    assert len(s.current("daily", "2026-11-11")) == 8 and len(s.current("weekly", "2026-11-09")) == 3
    assert "save-all" in w.sent
    e.tick()  # nothing new
    assert len(s.current("daily", "2026-11-11")) == 8


def test_completion_and_first_finisher_announced(tmp_path):
    w = World(tmp_path)
    now = [at(2026, 11, 11, 15)]
    e, s = engine(tmp_path, w, lambda: now[0])
    e.tick()
    w.set("u-b", jump=12)
    now[0] += 60
    e.tick()
    weekly = s.current("weekly", "2026-11-09")
    assert all(pg.first_finisher(s, i["iid"]) == "u-b" for i in weekly)
    assert any(t.startswith("tellraw @a") and "Bea" in t for t in w.sent)
    assert any(t.startswith("discord broadcast") and "Bea" in t for t in w.sent)


def test_day_rollover_picks_new_dailies(tmp_path):
    w = World(tmp_path)
    now = [at(2026, 11, 11, 23, 59)]
    e, s = engine(tmp_path, w, lambda: now[0])
    e.tick()
    first = {i["id"] for i in s.current("daily", "2026-11-11")}
    now[0] = at(2026, 11, 12, 0, 1)
    e.tick()
    second = {i["id"] for i in s.current("daily", "2026-11-12")}
    assert len(second) == 8 and not (first & second)
    assert s.mark("day:2026-11-12") is not None


def test_week_rollover_writes_hall_and_announces(tmp_path):
    w = World(tmp_path)
    now = [at(2026, 11, 15, 12)]
    e, s = engine(tmp_path, w, lambda: now[0])
    e.tick()
    w.set("u-a", jump=5, play_time=72000 * 3, mob_kills=4)
    w.set("u-b", jump=50, play_time=72000, mob_kills=9)
    now[0] = at(2026, 11, 16, 0, 1)
    e.sent_before = len(w.sent)
    e.tick()
    hall = e.hall()
    week = hall[0]
    assert week["week"] == "2026-11-09"
    assert week["boards"]["hours"][0] == {"name": "Alex", "uuid": "u-a", "value": 3.0}
    assert week["boards"]["mob_kills"][0]["name"] == "Bea"
    assert any("winners" in t.lower() for t in w.sent if t.startswith("discord broadcast"))
    assert len(s.current("weekly", "2026-11-16")) == 3


def test_rollover_survives_server_down(tmp_path):
    w = World(tmp_path)
    w.up = False
    now = [at(2026, 11, 11, 15)]
    e, s = engine(tmp_path, w, lambda: now[0])
    e.tick()
    assert s.mark("season") == {} and len(s.current("daily", "2026-11-11")) == 8


def test_goal_reached_announces_once_and_advances(tmp_path):
    w = World(tmp_path)
    now = [at(2026, 11, 11, 15)]
    e, s = engine(tmp_path, w, lambda: now[0])
    e.tick()
    assert e.goals_state()["current"]["id"] == "g1"
    w.set("u-a", jump=20)
    w.set("u-b", jump=15)
    e.tick()
    e.tick()
    assert [t for t in w.sent if t.startswith("discord broadcast") and "together" in t.lower()].__len__() == 1
    st = e.goals_state()
    assert st["current"]["id"] == "g2" and st["current"]["value"] == 35 and st["done"] == ["g1"]


def test_reset_backs_up_and_zeroes(tmp_path):
    w = World(tmp_path)
    now = [at(2026, 11, 11, 15)]
    e, s = engine(tmp_path, w, lambda: now[0])
    e.tick()
    w.set("u-a", jump=40)
    e.tick()
    assert e.goals_state()["done"] == ["g1"]
    e.reset("admin@example.com")
    assert list((tmp_path / "data" / "progress-backups").glob("progress-*.db"))
    assert e.goals_state()["done"] == [] and e.goals_state()["current"]["value"] == 0
    assert s.mark("season") == {"u-a": {"custom:minecraft:jump": 40}}
    assert len(s.current("daily", "2026-11-11")) == 8
    assert all(not pg.completed(s, i["iid"]) for i in s.current("weekly", "2026-11-09"))


def test_week_boards(tmp_path):
    w = World(tmp_path)
    now = [at(2026, 11, 11, 15)]
    e, s = engine(tmp_path, w, lambda: now[0])
    e.tick()
    w.set("u-a", walk_one_cm=250_000)
    b = e.week_boards()
    assert b["km"][0] == {"name": "Alex", "uuid": "u-a", "value": 2.5}
    assert b["hours"] == []


def test_announce_text_is_json_safe(tmp_path):
    w = World(tmp_path)
    e, s = engine(tmp_path, w, lambda: at(2026, 11, 11, 15))
    e.announce('A "quoted"\nthing')
    tell = [t for t in w.sent if t.startswith("tellraw")][0]
    assert json.loads(tell[len("tellraw @a "):])["text"] == 'A "quoted" thing'
    assert "\n" not in [t for t in w.sent if t.startswith("discord")][0]


def test_unreadable_stats_file_at_mark_keeps_previous_totals(tmp_path):
    w = World(tmp_path)
    w.set("u-a", jump=1000, mob_kills=500)
    now = [at(2026, 11, 11, 15)]
    e, s = engine(tmp_path, w, lambda: now[0])
    e.tick()
    (w.stats / "u-a.json").write_text('{"stats": {"minecraft:cus')  # half written at midnight
    now[0] = at(2026, 11, 12, 0, 1)
    e.tick()
    assert s.mark("day:2026-11-12")["u-a"] == {"custom:minecraft:jump": 1000, "custom:minecraft:mob_kills": 500}
    w.set("u-a", jump=1001, mob_kills=500)
    e.tick()
    assert not any(pg.completed(s, i["iid"]) for i in s.current("daily", "2026-11-12"))


def test_last_minutes_before_rollover_count_for_old_day(tmp_path):
    w = World(tmp_path)
    now = [at(2026, 11, 11, 23, 50)]
    e, s = engine(tmp_path, w, lambda: now[0])
    e.tick()
    old = s.current("daily", "2026-11-11")
    w.pending = {"u-a": {"jump": 50}}  # played at 23:58, not saved to disk yet
    now[0] = at(2026, 11, 12, 0, 0, 30)
    e.tick()
    assert all(pg.completed(s, i["iid"]) == {"u-a"} for i in old)
    assert s.mark("day:2026-11-12") == {"u-a": {"custom:minecraft:jump": 50}}


def test_hall_records_weekly_first_finishers(tmp_path):
    w = World(tmp_path)
    now = [at(2026, 11, 15, 12)]
    e, s = engine(tmp_path, w, lambda: now[0])
    e.tick()
    w.set("u-b", jump=20)
    e.tick()
    now[0] = at(2026, 11, 16, 0, 1)
    e.tick()
    week = e.hall()[0]
    assert len(week["firsts"]) == 3 and all(f["name"] == "Bea" and f["text"] == "Jump 10 times" for f in week["firsts"])
    assert e.hall_counts() == {"u-b": 3}


def tiers(insts):
    return sorted(cd.tier(i) for i in insts)


def test_day_picks_three_hard_and_five_easy(tmp_path):
    w = World(tmp_path)
    e, s = engine(tmp_path, w, lambda: at(2026, 11, 11, 15))
    e.tick()
    assert tiers(s.current("daily", "2026-11-11")) == ["easy"] * 5 + ["hard"] * 3
    e.reroll("daily")
    assert tiers(s.current("daily", "2026-11-11")) == ["easy"] * 5 + ["hard"] * 3


def test_day_from_before_tiers_is_topped_up_not_wiped(tmp_path):
    w = World(tmp_path)
    now = [at(2026, 11, 11, 15)]
    e, s = engine(tmp_path, w, lambda: now[0])
    e.tick()
    with s.conn() as c:
        c.execute("UPDATE instances SET active=0 WHERE kind='daily'")
    old = []
    for i in (6, 7):  # two picks saved without a tier, like the live server before this change
        entry = {k: v for k, v in pools()["daily"][i].items() if k != "tier"}
        old.append(s.add_instance("daily", "2026-11-11", entry))
    pg.record_completions(s, old[0], {"u-a": 10}, 10, now[0], {})
    now[0] += 60
    e.tick()
    cur = s.current("daily", "2026-11-11")
    assert [i["iid"] for i in cur][:2] == old and pg.completed(s, old[0]) == {"u-a"}
    assert tiers(cur) == ["easy"] * 5 + ["hard"] * 3 and len({i["id"] for i in cur}) == 8
    e.tick()
    assert len(s.current("daily", "2026-11-11")) == 8


def test_finished_daily_is_whispered_with_its_tier(tmp_path):
    w = World(tmp_path)
    now = [at(2026, 11, 11, 15)]
    e, s = engine(tmp_path, w, lambda: now[0])
    e.tick()
    w.set("u-b", jump=12)
    now[0] += 60
    e.tick()
    notes = [json.loads(t[len("tellraw Bea "):]) for t in w.sent if t.startswith("tellraw Bea ")]
    assert len(notes) == 8
    assert sorted(n[0]["text"] for n in notes) == ["[Daily] "] * 5 + ["[Hard] "] * 3
    assert {n[0]["color"] for n in notes if n[0]["text"] == "[Hard] "} == {"red"}
    assert not any(t.startswith("discord") and "Challenge done" in t for t in w.sent)


def test_swap_stays_in_tier(tmp_path):
    w = World(tmp_path)
    e, s = engine(tmp_path, w, lambda: at(2026, 11, 11, 15))
    e.tick()
    hard = next(i for i in s.current("daily", "2026-11-11") if cd.tier(i) == "hard")
    taken = {i["id"] for i in s.current("daily", "2026-11-11")}
    easy_id = next(x["id"] for x in pools()["daily"] if x["tier"] == "easy" and x["id"] not in taken)
    hard_id = next(x["id"] for x in pools()["daily"] if x["tier"] == "hard" and x["id"] not in taken)
    with pytest.raises(cd.PoolError):
        e.swap(hard["iid"], easy_id)
    e.swap(hard["iid"], hard_id)
    assert s.instance(hard["iid"])["id"] == hard_id and cd.tier(s.instance(hard["iid"])) == "hard"
