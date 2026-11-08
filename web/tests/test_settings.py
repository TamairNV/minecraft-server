import gzip
import io
import json
import os
import struct

import pytest

os.environ.update(ACCESS_SITE="mc", CF_TEAM_DOMAIN="t.example", CF_AUD="aud", MC_SERVER_DIR="", MC_BACKUP_DIR="", MC_DATA_DIR="")

from app import settings as st  # noqa: E402
from app.pregen import Pregen  # noqa: E402

PROPS = """#Minecraft server properties
#Fri Nov 06 22:09:25 GMT 2026
difficulty=normal
# a note someone left
level-name=world
level-type=minecraft\\:normal
max-players=15
motd=Friends SMP - Latest Drop
online-mode=true
rcon.password=hunter2
some-mod-key=keep me
view-distance=14
white-list=true
"""


class Crafty:
    def __init__(self, log=None, answer=None):
        self.sent, self.log, self.answer = [], log, answer

    def command(self, text):
        self.sent.append(text)
        if self.log and self.answer:
            with open(self.log, "a") as f:
                f.write(f"[18:00:00] [Server thread/INFO]: System chat: {self.answer}\n")


def server(tmp_path, crafty=None):
    (tmp_path / "server.properties").write_text(PROPS)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "latest.log").write_text("")
    return st.ServerSettings(tmp_path, tmp_path / "data", crafty or Crafty(), sleep=lambda s: None)


def test_escape_round_trip():
    for value in ("minecraft:normal", "a=b", r"back\slash", " lead", "TNV SMP §6gold"):
        assert st.unescape(st.escape(value)) == value
    assert st.unescape(r"§a") == "§a"


def test_save_keeps_comments_order_and_unknown_keys(tmp_path):
    s = server(tmp_path)
    r = s.save({"view-distance": 12, "motd": "TNV SMP", "difficulty": "normal"}, running=False, by="a@example.com")
    assert r == {"changed": ["motd", "view-distance"], "live": [], "restart": ["motd", "view-distance"]}
    text = (tmp_path / "server.properties").read_text()
    assert text == PROPS.replace("view-distance=14", "view-distance=12").replace("motd=Friends SMP - Latest Drop", "motd=TNV SMP")
    backups = list((tmp_path / "data" / "settings-backups").iterdir())
    assert len(backups) == 1 and backups[0].read_text() == PROPS
    assert json.loads((tmp_path / "data" / "settings-pending.json").read_text())["keys"] == ["motd", "view-distance"]


def test_nothing_changed_writes_nothing(tmp_path):
    s = server(tmp_path)
    assert s.save({"view-distance": "14"}, running=False, by="x")["changed"] == []
    assert not (tmp_path / "data" / "settings-backups").exists()


@pytest.mark.parametrize("values", [
    {"view-distance": 40}, {"view-distance": 1}, {"max-players": 0}, {"difficulty": "nightmare"},
    {"white-list": "yes"}, {"motd": "two\nlines"}, {"online-mode": "false"}, {"level-name": "other"},
    {"rcon.password": "x"}, {"not-a-key": "1"}, {"some-mod-key": "x" * 1001},
])
def test_rejects_bad_values(tmp_path, values):
    s = server(tmp_path)
    with pytest.raises(st.SettingsError):
        s.save(values, running=True, by="x")
    assert (tmp_path / "server.properties").read_text() == PROPS


def test_live_commands_when_running(tmp_path):
    c = Crafty()
    s = server(tmp_path, c)
    r = s.save({"white-list": False, "difficulty": "hard", "view-distance": 10, "pvp": False}, running=True, by="x")
    assert sorted(r["live"]) == ["difficulty", "pvp", "white-list"]
    assert sorted(c.sent) == ["difficulty hard", "gamerule pvp false", "whitelist off"]
    assert r["restart"] == ["view-distance"]


def test_view_hides_secrets_and_reads_game_rule(tmp_path):
    c = Crafty(tmp_path / "logs" / "latest.log", "Game rule pvp is currently set to false")
    v = server(tmp_path, c).view(running=True, started="2026-11-06 22:09:16")
    dump = json.dumps(v)
    assert "hunter2" not in dump and "rcon.password" not in dump
    fields = {f["key"]: f for g in v["groups"] for f in g["fields"]}
    assert fields["pvp"]["value"] == "false" and fields["online-mode"]["locked"]
    adv = {a["key"]: a for a in v["advanced"]}
    assert adv["some-mod-key"]["value"] == "keep me" and adv["level-type"]["value"] == "minecraft:normal"
    assert adv["level-name"]["locked"]


def test_pending_restart_clears_after_a_restart(tmp_path):
    s = server(tmp_path)
    s.save({"view-distance": 12}, running=False, by="x")
    assert s.view(False, "2000-01-01 00:00:00")["pending"] == ["view-distance"]
    assert s.view(False, "2999-01-01 00:00:00")["pending"] == []


def nbt_border(size, x, z):
    def tag(t, name, payload):
        return bytes([t]) + struct.pack(">H", len(name)) + name.encode() + payload
    inner = (tag(6, "center_x", struct.pack(">d", x)) + tag(4, "lerp_time", struct.pack(">q", 0)) +
             tag(6, "lerp_target", struct.pack(">d", size)) + tag(6, "size", struct.pack(">d", size)) +
             tag(6, "center_z", struct.pack(">d", z)) + b"\x00")
    root = tag(3, "DataVersion", struct.pack(">i", 5023)) + tag(10, "data", inner) + b"\x00"
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as f:
        f.write(tag(10, "", root))
    return buf.getvalue()


def test_borders_read_files_and_live_size(tmp_path):
    c = Crafty(tmp_path / "logs" / "latest.log", "The world border is currently 2,000 block(s) wide")
    s = server(tmp_path, c)
    for dim, _ in st.DIMENSIONS:
        d = tmp_path / "world" / "dimensions" / "minecraft" / dim / "data" / "minecraft"
        d.mkdir(parents=True)
        (d / "world_border.dat").write_bytes(nbt_border(10000, 0.5, 0.5))
    rows = s.borders(running=True)
    assert [r["size"] for r in rows] == [10000, 10000, 10000]
    assert rows[0]["center_x"] == 0.5 and rows[0]["live"] == 2000
    assert c.sent[0] == "execute in minecraft:overworld run worldborder get"
    assert rows[1]["chunky"][3] == "chunky radius 1000"


def test_set_border_commands_and_limits(tmp_path):
    c = Crafty()
    s = server(tmp_path, c)
    assert s.set_border("the_nether", 5000, 0, -12.5) == [
        "execute in minecraft:the_nether run worldborder center 0 -12.5",
        "execute in minecraft:the_nether run worldborder set 5000"]
    rows = s.borders(running=False)
    assert rows[1]["size"] == 5000 and rows[1]["center_z"] == -12.5
    for bad in (("the_moon", 10, 0, 0), ("overworld", 0, 0, 0), ("overworld", 10, 4e7, 0)):
        with pytest.raises(st.SettingsError):
            s.set_border(*bad)


CHUNKY_LOG = """[14:47:00] [Server thread/INFO]: [Chunky] Task started in minecraft:overworld for the square region centered at 0, 0 with radius 500.
[14:47:04] [Server thread/INFO]: [Chunky] Task running for minecraft:overworld. Processed: 10 chunks (0.24%), ETA: 0:02:19, Rate: 30.1 cps, Current: 1, -31
[14:47:05] [Server thread/INFO]: [Chunky] Task running for minecraft:overworld. Processed: 1,118 chunks (26.79%), ETA: 0:00:47, Rate: 85.6 cps, Current: 23, -30
[14:48:00] [Server thread/INFO]: [Chunky] Task started in minecraft:the_end for the circle region centered at 0, 0 with radius 100.
[14:48:10] [Server thread/INFO]: [Chunky] Task finished for minecraft:the_end. Processed: 157 chunks (100.00%), Total time: 0:00:10
"""


def test_pregen_progress_from_log(tmp_path):
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "latest.log").write_text(CHUNKY_LOG)
    tasks = tmp_path / "plugins" / "Chunky" / "tasks" / "minecraft"
    tasks.mkdir(parents=True)
    (tasks / "the_nether.properties").write_text("world=minecraft:the_nether\ncancelled=false\ncenter-x=0.0\ncenter-z=0.0\nradius=800.0\nshape=square\nchunks=50\n")
    rows = {r["dim"]: r for r in Pregen(tmp_path, tmp_path, Crafty(), sleep=lambda s: None).state(running=True)}
    assert rows["overworld"]["status"] == "running" and rows["overworld"]["chunks"] == 1118 and rows["overworld"]["eta"] == "0:00:47"
    assert rows["the_end"]["status"] == "finished" and rows["the_end"]["took"] == "0:00:10"
    assert rows["the_nether"]["status"] == "paused" and rows["the_nether"]["can_continue"] and rows["the_nether"]["radius"] == 800
    stopped = {r["dim"]: r for r in Pregen(tmp_path, tmp_path, Crafty(), sleep=lambda s: None).state(running=False)}
    assert stopped["overworld"]["status"] == "paused"


def test_pregen_start_and_control(tmp_path):
    c = Crafty()
    p = Pregen(tmp_path, tmp_path, c, sleep=lambda s: None)
    p.start("the_nether", 2500, 0, 0, "circle", "a@example.com")
    assert c.sent == ["chunky world minecraft:the_nether", "chunky shape circle", "chunky center 0 0",
                      "chunky radius 2500", "chunky start", "chunky confirm"]
    assert json.loads((tmp_path / "pregen.json").read_text())["the_nether"]["radius"] == 2500
    c.sent.clear()
    p.control("overworld", "cancel")
    assert c.sent == ["chunky cancel minecraft:overworld", "chunky confirm"]
    for bad in (("overworld", 10, 0, 0, "square"), ("overworld", 1000, 0, 0, "hexagon"), ("mars", 1000, 0, 0, "square")):
        with pytest.raises(st.SettingsError):
            p.start(*bad, "x")
    with pytest.raises(st.SettingsError):
        p.control("overworld", "trim")
