import json
import os
import re

os.environ.update(ACCESS_SITE="mc", CF_TEAM_DOMAIN="t.example", CF_AUD="aud", MC_SERVER_DIR="", MC_BACKUP_DIR="", MC_DATA_DIR="")

import pytest  # noqa: E402

from app import autotune as at  # noqa: E402


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class Game:
    """Fake server: remembers what the console set, answers the status commands."""
    def __init__(self, view=14, conc=20, rc=50):
        self.view, self.conc, self.rc, self.up, self.sent = view, conc, rc, True, []

    def running(self):
        return self.up

    def command(self, text):
        self.sent.append(text)
        m = re.fullmatch(r"viewdist set (\d+)", text)
        if m:
            self.view = int(m.group(1))
        m = re.fullmatch(r"dhs config set (\w+) (\d+)", text)
        if m:
            setattr(self, "conc" if m.group(1) == "full_data_request_concurrency_limit" else "rc", int(m.group(2)))

    def query(self, command, pattern):
        self.sent.append(command)
        text = {"viewdist status": f"ViewDist: view {self.view} sim 10 (world {self.view})",
                "dhs config get full_data_request_concurrency_limit": f"Value of full_data_request_concurrency_limit is {self.conc}.",
                "dhs config get login_data_sync_rc_limit": f"Value of login_data_sync_rc_limit is {self.rc}."}[command]
        return pattern.search(text)

    def restart(self):
        self.view, self.conc, self.rc = 14, 20, 50


def setup(tmp_path, **cfg):
    server = tmp_path / "server"
    (server / "plugins" / "DHSupport").mkdir(parents=True)
    (server / "server.properties").write_text("motd=x\nview-distance=14\n")
    (server / "plugins" / "DHSupport" / "config.yml").write_text(
        "full_data_request_concurrency_limit: 20\nlogin_data_sync_rc_limit: 50\n")
    data = tmp_path / "data"
    data.mkdir()
    if cfg:
        (data / "autotune.json").write_text(json.dumps(cfg))
    clock, game = Clock(), Game()
    tune = at.AutoTune(server, data, tmp_path / "live.json", game, game.query, clock=clock)
    return tune, game, clock, tmp_path / "live.json"


def traffic(path, clock, mbit, seconds=150):
    """netmon live.json with one sample every 5 s at this rate."""
    t = clock()
    recent = [{"t": t - s, "wg_tx": mbit * 1e6 / 8} for s in range(seconds, -1, -5)]
    path.write_text(json.dumps({"t": t, "recent": recent}))


def run(tune, clock, path, mbit, seconds, step=5):
    for _ in range(int(seconds // step)):
        clock.t += step
        traffic(path, clock, mbit)
        tune.tick()


def test_base_values_come_from_the_files(tmp_path):
    tune, *_ = setup(tmp_path)
    assert at.base_values(tune.server) == {"view": 14, "dh_conc": 20, "dh_rc": 50}
    assert at.targets(0, {"view": 14, "dh_conc": 20, "dh_rc": 50}) == {"view": 14, "dh_conc": 20, "dh_rc": 50}
    assert at.targets(3, {"view": 14, "dh_conc": 20, "dh_rc": 50}) == {"view": 8, "dh_conc": 2, "dh_rc": 5}
    # never above the configured normal, never below the floor
    assert at.targets(1, {"view": 7, "dh_conc": 3, "dh_rc": 50}) == {"view": 6, "dh_conc": 3, "dh_rc": 20}


def test_busy_upload_steps_down_one_level_at_a_time(tmp_path):
    tune, game, clock, live = setup(tmp_path)
    run(tune, clock, live, 18, 5)        # 90% busy: first step straight away
    assert tune.level == 1 and game.view == 12 and game.conc == 8 and game.rc == 20
    run(tune, clock, live, 18, 15)       # 15 s later, under UP_HOLD (20 s): stays
    assert tune.level == 1
    run(tune, clock, live, 18, 5)        # 20 s since the last step: next one
    assert tune.level == 2 and game.view == 10
    run(tune, clock, live, 18, 200)
    assert tune.level == 3 and game.view == 8 and game.conc == 2 and game.rc == 5
    assert {k: tune.live[k] for k in ("view", "sim", "dh_conc", "dh_rc")} == {"view": 8, "sim": 10, "dh_conc": 2, "dh_rc": 5}
    texts = [e["text"] for e in tune.events]
    assert texts[0].startswith("Normal → Busy") and texts[-1].startswith("Heavy → Full")


def test_quiet_upload_steps_back_up_slowly(tmp_path):
    tune, game, clock, live = setup(tmp_path)
    run(tune, clock, live, 18, 60)
    assert tune.level >= 2
    lvl = tune.level
    run(tune, clock, live, 2, 60)        # quiet, but not for 2 minutes yet
    assert tune.level == lvl
    run(tune, clock, live, 2, 130)
    assert tune.level == lvl - 1
    run(tune, clock, live, 2, 600)
    assert tune.level == 0 and (game.view, game.conc, game.rc) == (14, 20, 50)
    assert tune.events[-1]["text"].startswith("Busy → Normal")


def test_middle_traffic_holds(tmp_path):
    tune, game, clock, live = setup(tmp_path)
    run(tune, clock, live, 18, 10)
    assert tune.level == 1
    run(tune, clock, live, 11, 600)      # 55%: below step-up, above step-back
    assert tune.level == 1


def test_stale_netmon_or_stopped_server_changes_nothing(tmp_path):
    tune, game, clock, live = setup(tmp_path)
    traffic(live, clock, 19)
    clock.t += 60                        # file is now a minute old
    tune.tick()
    assert tune.level == 0 and game.sent == []
    game.up = False
    run(tune, clock, live, 19, 60)
    assert tune.level == 0 and game.sent == []


def test_restart_is_noticed_and_level_reapplied(tmp_path):
    tune, game, clock, live = setup(tmp_path)
    run(tune, clock, live, 18, 10)
    assert tune.level == 1
    game.restart()
    run(tune, clock, live, 11, at.CHECK_EVERY + 10)
    assert game.view == 12 and game.conc == 8
    assert any("Re-applied Busy" in e["text"] for e in tune.events)


def test_turning_off_restores_normal_and_state_survives(tmp_path):
    tune, game, clock, live = setup(tmp_path)
    run(tune, clock, live, 18, 40)
    assert tune.level == 2
    tune.configure(False, 20, 75, 40)
    assert tune.level == 0 and (game.view, game.conc, game.rc) == (14, 20, 50)
    run(tune, clock, live, 19, 60)       # off: ignores traffic
    assert tune.level == 0
    again = at.AutoTune(tune.server, tune.file.parent, live, game, game.query, clock=clock)
    assert again.cfg["enabled"] is False and again.events[-1]["text"].startswith("Turned off")
    with pytest.raises(ValueError):
        tune.configure(True, 20, 40, 50)
    with pytest.raises(ValueError):
        tune.configure(True, 0, 75, 40)


def test_status_for_the_page(tmp_path):
    tune, game, clock, live = setup(tmp_path)
    run(tune, clock, live, 18, 10)
    d = tune.status()
    assert d["level_name"] == "Busy" and d["target"] == {"view": 12, "dh_conc": 8, "dh_rc": 20}
    assert d["base"]["view"] == 14 and len(d["levels"]) == 4 and d["levels"][3]["view"] == 8
    assert d["usage"]["fresh"] and d["usage"]["short_pct"] == 90.0
    assert d["events"][0]["after"]["view"] == 12 and d["live"]["view"] == 12


def test_api(tmp_path, monkeypatch):
    from tests.test_admin import HEAD, client
    from app import admin

    tune, game, clock, live = setup(tmp_path)
    real_build = admin.build
    monkeypatch.setattr(admin, "build", lambda *a, **k: real_build(*a, **{**k, "autotune": tune}))
    c, _ = client(tmp_path, "user")
    assert c.post("/admin/api/network/autotune", json={"enabled": False}, headers=HEAD).status_code == 403
    c, _ = client(tmp_path, "admin")
    assert c.get("/admin/api/network/autotune", headers=HEAD).json()["level_name"] == "Normal"
    assert c.post("/admin/api/network/autotune", json={"enabled": True, "uplink_mbit": 20, "up_pct": 30, "down_pct": 50},
                  headers=HEAD).status_code == 400
    r = c.post("/admin/api/network/autotune", json={"enabled": True, "uplink_mbit": 25, "up_pct": 80, "down_pct": 30}, headers=HEAD)
    assert r.status_code == 200 and r.json()["uplink_mbit"] == 25
    r = c.post("/admin/api/network/autotune/refresh", json={}, headers=HEAD)
    assert r.json()["live"]["view"] == 14
    audit = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert audit[-1]["action"] == "network smart tuning" and "upload 25 Mbit/s" in audit[-1]["detail"]
