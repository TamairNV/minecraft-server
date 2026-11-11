import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

os.environ.update(ACCESS_SITE="mc", CF_TEAM_DOMAIN="t.example", CF_AUD="aud", MC_SERVER_DIR="", MC_BACKUP_DIR="", MC_DATA_DIR="")

from app import progress as pg  # noqa: E402

LONDON = ZoneInfo("Europe/London")


def at(*args):
    return datetime(*args, tzinfo=LONDON).timestamp()


def test_flatten():
    raw = {"stats": {"minecraft:mined": {"minecraft:stone": 5}, "minecraft:custom": {"minecraft:jump": 2}}}
    assert pg.flatten(raw) == {"mined:minecraft:stone": 5, "custom:minecraft:jump": 2}


def test_metric_patterns_sum():
    flat = {"mined:minecraft:oak_log": 3, "mined:minecraft:birch_log": 2, "mined:minecraft:stone": 9,
            "crafted:minecraft:oak_log": 100}
    assert pg.metric_value(flat, {"cat": "mined", "keys": ["minecraft:*_log"]}) == 5
    assert pg.metric_value(flat, {"cat": "mined", "keys": ["*"]}) == 14
    assert pg.metric_value(flat, {"cat": "custom", "keys": ["minecraft:jump"]}) == 0


def test_delta_clamps_and_counts_new_players():
    assert pg.delta({"a": 5}, None) == {"a": 5}
    assert pg.delta({"a": 5, "b": 1}, {"a": 2}) == {"a": 3, "b": 1}
    assert pg.delta({"a": 1}, {"a": 4}) == {"a": 0}  # files went backwards (world swap)


def test_day_and_week_start_across_clock_change():
    # Clocks go back 2026-10-25 02:00 BST -> 01:00 GMT.
    assert pg.day_start(at(2026, 10, 25, 12)) == at(2026, 10, 25, 0)
    assert pg.week_start(at(2026, 10, 25, 12)) == at(2026, 10, 19, 0)
    assert pg.week_start(at(2026, 10, 26, 0, 30)) == at(2026, 10, 26, 0)
    assert pg.next_day(at(2026, 10, 25, 12)) == at(2026, 10, 26, 0)
    assert pg.next_day(at(2026, 10, 25, 12)) - pg.day_start(at(2026, 10, 25, 12)) == 25 * 3600
    # Clocks go forward 2027-03-28.
    assert pg.next_day(at(2027, 3, 28, 12)) - pg.day_start(at(2027, 3, 28, 12)) == 23 * 3600
    assert pg.next_week(at(2027, 3, 28, 12)) == at(2027, 3, 29, 0)
    assert pg.day_name(at(2026, 10, 25, 23, 59)) == "2026-10-25"


def test_store_marks_round_trip(tmp_path):
    s = pg.Store(tmp_path / "p.db")
    s.save_mark("season", {"u1": {"a": 1}, "u2": {"b": 2}})
    assert s.mark("season") == {"u1": {"a": 1}, "u2": {"b": 2}}
    assert s.mark("day:2026-11-11") is None
    s.save_mark("season", {"u1": {"a": 9}})  # replaces the whole mark
    assert s.mark("season") == {"u1": {"a": 9}}
    s.put("x", {"k": [1, 2]})
    assert s.get("x") == {"k": [1, 2]} and s.get("nope", 3) == 3


def test_read_totals_skips_bad_files(tmp_path):
    d = tmp_path / "world" / "players" / "stats"
    d.mkdir(parents=True)
    (d / "u1.json").write_text(json.dumps({"stats": {"minecraft:custom": {"minecraft:jump": 4}}}))
    (d / "u2.json").write_text("{not json")
    assert pg.read_totals(tmp_path) == {"u1": {"custom:minecraft:jump": 4}}


def test_progress_against_mark(tmp_path):
    s = pg.Store(tmp_path / "p.db")
    s.save_mark("week:2026-11-09", {"u1": {"custom:minecraft:jump": 10}})
    totals = {"u1": {"custom:minecraft:jump": 15}, "u2": {"custom:minecraft:jump": 3}}
    metric = {"cat": "custom", "keys": ["minecraft:jump"]}
    assert pg.progress(totals, s, "week:2026-11-09", metric) == {"u1": 5, "u2": 3}
