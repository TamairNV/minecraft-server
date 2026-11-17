import json
import os
import random

import pytest

os.environ.update(ACCESS_SITE="mc", CF_TEAM_DOMAIN="t.example", CF_AUD="aud", MC_SERVER_DIR="", MC_BACKUP_DIR="", MC_DATA_DIR="")

from app import challenges_default as cd  # noqa: E402
from app import progress as pg  # noqa: E402


def entry(i, **kw):
    e = {"id": f"c{i}", "text": "Jump {n} times", "metric": {"cat": "custom", "keys": ["minecraft:jump"]},
         "target": 10, "reward": [{"item": "minecraft:dirt", "count": 1}], "enabled": True}
    e.update(kw)
    return e


def test_pick_avoids_previous_when_possible():
    pool = [entry(i) for i in range(6)]
    for seed in range(20):
        got = pg.pick(pool, 3, {"c0", "c1", "c2"}, random.Random(seed))
        assert sorted(e["id"] for e in got) == ["c3", "c4", "c5"]


def test_pick_skips_disabled_and_falls_back_when_pool_small():
    pool = [entry(0), entry(1), entry(2, enabled=False)]
    got = pg.pick(pool, 2, {"c0"}, random.Random(1))
    assert sorted(e["id"] for e in got) == ["c0", "c1"]
    assert len(pg.pick(pool, 5, set(), random.Random(1))) == 2


def test_instance_is_frozen_copy(tmp_path):
    s = pg.Store(tmp_path / "p.db")
    e = entry(0)
    iid = s.add_instance("daily", "2026-11-11", e)
    e["target"] = 999
    cur = s.current("daily", "2026-11-11")
    assert [(c["iid"], c["target"]) for c in cur] == [(iid, 10)]
    s.replace_instance(iid, entry(1, target=5))
    assert s.current("daily", "2026-11-11")[0]["id"] == "c1"
    assert s.current("weekly", "2026-11-09") == []


def test_first_finisher_and_ties(tmp_path):
    s = pg.Store(tmp_path / "p.db")
    iid = s.add_instance("weekly", "2026-11-09", entry(0))
    names = {"a": "Alex", "b": "Bea", "c": "Cal"}
    new = pg.record_completions(s, iid, {"a": 12, "b": 15, "c": 3}, 10, 100.0, names)
    assert new == ["b", "a"]  # same check: more progress first
    assert pg.first_finisher(s, iid) == "b"
    assert pg.record_completions(s, iid, {"a": 20, "b": 30, "c": 11}, 10, 200.0, names) == ["c"]
    assert pg.first_finisher(s, iid) == "b"
    assert pg.completed(s, iid) == {"a", "b", "c"}


def test_defaults_are_valid(tmp_path):
    cd.save_pools(tmp_path, cd.DEFAULT_POOLS)
    pools = cd.load_pools(tmp_path)
    assert len(pools["daily"]) >= 20 and len(pools["weekly"]) >= 30
    assert all(e.get("bonus") for e in pools["weekly"])
    assert len({e["id"] for e in pools["daily"] + pools["weekly"]}) == len(pools["daily"]) + len(pools["weekly"])
    cd.save_goals(tmp_path, cd.DEFAULT_GOALS)
    assert cd.load_goals(tmp_path)[0]["target"] > 0


def test_load_pools_uses_defaults_when_missing(tmp_path):
    assert cd.load_pools(tmp_path) == cd.DEFAULT_POOLS


@pytest.mark.parametrize("bad", [
    {"text": "no placeholder"}, {"target": 0}, {"target": 10 ** 10}, {"metric": {"cat": "nope", "keys": ["x"]}},
    {"metric": {"cat": "custom", "keys": []}}, {"id": "bad id!"}, {"reward": []},
])
def test_save_pools_rejects_bad_entries(tmp_path, bad):
    pools = {"daily": [entry(0, **bad)], "weekly": []}
    with pytest.raises(cd.PoolError):
        cd.save_pools(tmp_path, pools)
    assert not (tmp_path / "challenges.json").exists()


def test_weekly_needs_bonus(tmp_path):
    with pytest.raises(cd.PoolError):
        cd.save_pools(tmp_path, {"daily": [], "weekly": [entry(0)]})
    cd.save_pools(tmp_path, {"daily": [], "weekly": [entry(0, bonus=[{"item": "minecraft:diamond", "count": 1}])]})
    assert json.loads((tmp_path / "challenges.json").read_text())["weekly"][0]["bonus"][0]["count"] == 1
