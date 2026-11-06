import zipfile

import pytest

from app.worlds import WorldError, Worlds


class FakeCrafty:
    def __init__(self, on_start=None):
        self.up, self.on_start, self.backups, self.sent = True, on_start, None, []

    def running(self):
        return self.up

    def stats(self):
        return {}

    def command(self, text):
        self.sent.append(text)

    def action(self, name):
        if name == "stop_server":
            self.up = False
        if name == "start_server":
            self.up = True
            if self.on_start:
                self.on_start()

    def backup(self):
        with zipfile.ZipFile(self.backups / f"b{len(list(self.backups.iterdir()))}.zip", "w") as z:
            z.writestr("x", "y")


def setup(tmp_path, ping=None, on_start=None):
    server, backups = tmp_path / "server", tmp_path / "backups"
    (server / "world").mkdir(parents=True)
    backups.mkdir()
    (server / "world" / "marker").write_text("old")
    (server / "server.properties").write_text("motd=x\nlevel-seed=111\n")
    crafty = FakeCrafty(on_start)
    crafty.backups = backups
    w = Worlds(server, backups, crafty, ping or (lambda: {"players": {"online": 0}}), sleep=lambda s: None, start_timeout=4)
    w.setup()
    return w, server


def run(w, fn):
    fn()
    w._lock.acquire()  # waits for the job's thread to finish
    w._lock.release()
    return w.job()


def test_swap_to_new_world_and_back(tmp_path):
    w, server = setup(tmp_path)
    slug = w.create_new("Second", "222")
    job = run(w, lambda: w.start_swap(slug, "me"))
    assert job["status"] == "done", job
    assert w.active() == slug and not (server / "world").exists()
    assert "level-seed=222" in (server / "server.properties").read_text()
    assert any("worldborder set 10000" in c for c in w.crafty.sent)
    assert len(w.backup_files()) == 1

    (server / "world").mkdir()
    job = run(w, lambda: w.start_swap("first-world", "me"))
    assert job["status"] == "done", job
    assert (server / "world" / "marker").read_text() == "old"
    assert "level-seed=111" in (server / "server.properties").read_text()
    assert (w._slot(slug) / "payload" / "world").exists()


def test_failed_start_restores_old_world(tmp_path):
    state = {"fail": True}

    def ping():
        if state["fail"]:
            raise OSError("down")
        return {"players": {"online": 0}}

    w, server = setup(tmp_path, ping)
    slug = w.create_new("Broken", "222")

    def starts():
        # Paper makes files, then the old world comes back healthy after rollback.
        if not (server / "world").exists():
            (server / "world").mkdir()
            (server / "world" / "partial").write_text("x")
        if (server / "world" / "marker").exists():
            state["fail"] = False

    w.crafty.on_start = starts
    w.crafty.action("noop")
    job = run(w, lambda: w.start_swap(slug, "me"))
    assert job["status"] == "failed" and "previous one is back" in job["error"]
    assert w.active() == "first-world"
    assert (server / "world" / "marker").read_text() == "old"
    assert (w._slot(slug) / "payload" / "world" / "partial").exists()
    assert "level-seed=111" in (server / "server.properties").read_text()
    assert not w.journal_file.exists()


def test_restore_refuses_unsafe_backup(tmp_path):
    w, _ = setup(tmp_path)
    with zipfile.ZipFile(tmp_path / "backups" / "evil.zip", "w") as z:
        z.writestr("world/level.dat", "x")
        z.writestr("world/../../escaped", "x")
    job = run(w, lambda: w.start_restore("Evil", "evil.zip", "me"))
    assert job["status"] == "failed" and "unsafe" in job["error"]
    assert not (tmp_path / "server" / "escaped").exists() and not w._slot("evil").exists()


def test_bad_names_refused(tmp_path):
    w, _ = setup(tmp_path)
    for name in ("../../etc", "", "a"):
        with pytest.raises(WorldError):
            w.create_new(name)
    with pytest.raises(WorldError):
        w.create_new("Fine name", "bad\nseed=1")
