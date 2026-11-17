import json
import os

os.environ.update(ACCESS_SITE="mc", CF_TEAM_DOMAIN="t.example", CF_AUD="aud", MC_SERVER_DIR="", MC_BACKUP_DIR="", MC_DATA_DIR="")

from app import network as nw  # noqa: E402

LOG = """[20:01:02] [Server thread/INFO]: Alice[/10.100.0.1:25421] logged in with entity id 1 at ([minecraft:overworld]1, 2, 3)
[20:02:02] [Server thread/INFO]: Bob_2[/2a02:c7c:0:0:0:0:0:9:50000] logged in with entity id 2 at ([minecraft:overworld]1, 2, 3)
[20:03:02] [Server thread/INFO]: Carol[/[2a02:c7c::10]:50001] logged in with entity id 3 at ([minecraft:overworld]1, 2, 3)
[20:04:02] [Server thread/INFO]: <Alice> Eve[/1.2.3.4:5] logged in with entity id 9
"""


def test_peer_keys_match_between_log_and_ss():
    assert nw.peer_key("10.100.0.1:25421") == "10.100.0.1:25421"
    assert nw.peer_key("[::ffff:10.100.0.1]:25421") == "10.100.0.1:25421"
    assert nw.peer_key("2a02:c7c:0:0:0:0:0:9:50000") == "[2a02:c7c::9]:50000"
    assert nw.peer_key("[2a02:c7c::9]:50000") == "[2a02:c7c::9]:50000"
    assert nw.peer_key("nonsense") is None


def test_logins_ignore_chat():
    names = nw.logins(LOG)
    assert names == {"10.100.0.1:25421": "Alice", "[2a02:c7c::9]:50000": "Bob_2", "[2a02:c7c::10]:50001": "Carol"}


def test_routes():
    assert nw.route("10.100.0.1:1") == "proxy"
    assert nw.route("[2a02:c7c::9]:5") == "direct IPv6"
    assert nw.route("192.168.1.20:5") == "LAN"
    assert nw.route("127.0.0.1:5") == "local"
    assert nw.route("172.18.0.1:5") == "local"


def test_downsample_keeps_peaks_and_failures():
    pts = [{"t": i, "rtt": 10.0, "loss": 0.0, "tx_peak": 1.0, "pub_ok": True} for i in range(720)]
    pts[100].update(loss=50.0, tx_peak=9.0, pub_ok=False)
    out = nw.downsample(pts, 360)
    assert len(out) == 360 and out[-1]["t"] == 719
    assert max(p["loss"] for p in out) == 50.0 and max(p["tx_peak"] for p in out) == 9.0
    assert [p["pub_ok"] for p in out].count(False) == 1
    assert nw.downsample(pts[:10]) == pts[:10]


def live(t, **kw):
    s = {"t": t, "wg": {"endpoint": "77.97.177.156:51820", "handshake": t - 20, "rx": 1, "tx": 2},
         "tun": {"sent": 5, "got": 5, "avg": 25.0, "max": 30.0, "jitter": 2.0}, "wg_rx": 100.0, "wg_tx": 1000.0,
         "tcp": {"out": t * 10, "retrans": t // 5}}
    s.update(kw)
    return s


def setup(tmp_path, now, recent, players=None, hist=None):
    (tmp_path / "live.json").write_text(json.dumps({
        "t": now, "tick": 5, "peer": "10.100.0.1", "public_host": "server.example", "uplink_mbit": 0,
        "recent": recent, "players": players, "alerts": [{"key": "loss", "level": "warn", "title": "x", "detail": ""}],
        "events": [{"t": 1}, {"t": 2}]}))
    (tmp_path / "history.json").write_text(json.dumps({"points": hist or []}))
    (tmp_path / "logs").mkdir(exist_ok=True)
    (tmp_path / "logs" / "latest.log").write_text(LOG)
    (tmp_path / "whitelist.json").write_text(json.dumps([{"uuid": "u-1", "name": "alice"}]))
    return nw.Network(tmp_path / "live.json", tmp_path / "history.json", tmp_path / "logs" / "latest.log", clock=lambda: now)


def test_view_live(tmp_path):
    recent = [live(1000 + i * 5) for i in range(10)]
    recent[-1]["pub"] = {"ok": True, "connect": 40.0, "reply": 30.0}
    recent[-1]["local"] = {"ok": True, "connect": 0.3}
    socks = [{"peer": "10.100.0.1:25421", "rtt": 50.0, "rtt_var": 4.0, "send_q": 0, "bytes_sent": 1000, "bytes_retrans": 20},
             {"peer": "10.100.0.1:9", "rtt": 60.0, "send_q": 10}]
    v = setup(tmp_path, 1046, recent, socks).view("15m")
    assert v["running"] and v["stale"] == 0 and len(v["series"]) == 10
    assert v["series"][1]["retrans"] == 2.0 and v["series"][0]["retrans"] is None
    assert v["events"][0] == {"t": 2}
    s = v["summary"]
    assert s["handshake_age"] == 21 and s["rtt"] == 25.0 and s["loss"] == 0.0 and s["public"]["ok"]
    assert s["local"]["ok"] and s["endpoint"].startswith("77.")
    assert [p["name"] for p in v["players"]] == ["Alice", None]
    assert v["players"][0] == {"name": "Alice", "uuid": "u-1", "route": "proxy", "rtt": 50.0, "jitter": 4.0, "send_q": 0,
                               "retrans_pct": 2.0, "delivery": None, "sent": 1000,
                               "tx_rate": None, "rx_rate": None, "leg": 25.0}


def test_view_history_and_stale(tmp_path):
    hist = [{"t": 100_000 - 60 * i, "rtt": 20.0, "tx_peak": 5.0} for i in range(2000)]
    n = setup(tmp_path, 100_000, [live(99_900)], hist=hist)
    v = n.view("1h")
    assert len(v["series"]) == 60 and v["series"][-1]["t"] == 99_900 and v["summary"]["tx_peak_24h"] == 5.0
    assert len(n.view("7d")["series"]) == 360
    n.clock = lambda: 100_100
    v = n.view("1h")
    assert not v["running"] and v["alerts"][0]["key"] == "monitor"


def test_no_monitor_yet(tmp_path):
    v = nw.Network(tmp_path / "none.json", tmp_path / "h.json", tmp_path / "l.log").view("1h")
    assert not v["running"] and v["alerts"][0]["key"] == "monitor" and v["players"] == []


def test_admin_route(tmp_path):
    from test_admin import HEAD, client
    c, sent = client(tmp_path, "admin")
    r = c.get("/admin/api/network?range=6h", headers=HEAD)
    assert r.status_code == 200 and r.json()["range"] == "6h" and sent == []
    assert c.get("/admin/api/network?range=1y", headers=HEAD).status_code == 400
    c, _ = client(tmp_path, "user")
    assert c.get("/admin/api/network", headers=HEAD).status_code == 403
