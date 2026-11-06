import json

from app import stats


def make(tmp_path):
    d = tmp_path / "world" / "players" / "stats"
    d.mkdir(parents=True)
    (tmp_path / "usercache.json").write_text(json.dumps([{"uuid": "aaa", "name": "Ant"}]))
    (d / "aaa.json").write_text(json.dumps({"stats": {
        "minecraft:custom": {"minecraft:play_time": 144000, "minecraft:deaths": 3, "minecraft:mob_kills": 7,
                             "minecraft:walk_one_cm": 100000, "minecraft:aviate_one_cm": 200000},
        "minecraft:mined": {"minecraft:stone": 10, "minecraft:dirt": 5}}}))
    (d / "bbb.json").write_text(json.dumps({"stats": {"minecraft:custom": {"minecraft:play_time": 72000}}}))
    (d / "broken.json").write_text("{not json")
    return tmp_path


def test_collect_aggregates_and_sorts(tmp_path):
    players = stats.collect(make(tmp_path))
    assert [p["name"] for p in players] == ["Ant", "bbb"]
    a = players[0]
    assert (a["hours"], a["deaths"], a["mob_kills"], a["mined"], a["km"]) == (2.0, 3, 7, 15, 3.0)


def test_no_stats_dir_is_empty(tmp_path):
    assert stats.collect(tmp_path) == []


def test_varint():
    from app.slp import varint
    assert varint(0) == b"\x00"
    assert varint(300) == b"\xac\x02"
    assert varint(-1 & 0xFFFFFFFF) == b"\xff\xff\xff\xff\x0f"
