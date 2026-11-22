import os

os.environ.update(ACCESS_SITE="mc", CF_TEAM_DOMAIN="t.example", CF_AUD="aud", MC_SERVER_DIR="", MC_BACKUP_DIR="", MC_DATA_DIR="")

from app import cpu  # noqa: E402

STAT = """cpu  100 0 100 800 0 0 0 0 0 0
cpu0 40 10 30 400 10 5 5 0 0 0
cpu1 50 0 50 400 0 0 0 0 0 0
intr 12345
"""


def test_read_stat(tmp_path):
    p = tmp_path / "stat"
    p.write_text(STAT)
    # user = user + nice, system = system + irq + softirq + steal, total includes idle and iowait
    assert cpu.read_stat(str(p)) == [(50, 40, 500), (50, 50, 500)]


def test_percents_split_user_and_system():
    before = [(0, 0, 0), (0, 0, 0), (5, 5, 10)]
    after = [(30, 20, 100), (0, 0, 100), (5, 5, 10)]
    assert cpu.percents(before, after) == [
        {"user": 30.0, "system": 20.0}, {"user": 0.0, "system": 0.0}, {"user": 0.0, "system": 0.0}]


def test_cgroup_usage(tmp_path):
    p = tmp_path / "cpu.stat"
    p.write_text("usage_usec 1234\nuser_usec 1000\nsystem_usec 234\n")
    assert cpu.read_cgroup_usec(str(p)) == 1234


class Fake:
    def __init__(self, stats, mc, times):
        self.stats, self.mc, self.times, self.waits = iter(stats), iter(mc), iter(times), []

    def meter(self):
        return cpu.CpuMeter(read=lambda: next(self.stats), read_mc=self._mc, clock=lambda: next(self.times),
                            sleep=self.waits.append)

    def _mc(self):
        v = next(self.mc)
        if v is None:
            raise OSError
        return v


def test_server_goes_past_100_when_several_threads_are_busy():
    # 3 seconds of CPU time in 1.5 s of wall time = two threads flat out = 200%, like htop.
    f = Fake(stats=[[(0, 0, 0)], [(10, 0, 100)]], mc=[0, 3_000_000], times=[10.0, 11.5])
    r = f.meter().sample()
    assert r == {"threads": [{"user": 10.0, "system": 0.0}], "server": 200.0}
    assert f.waits == [0.5]


def test_sampler_uses_previous_reading_after_the_first():
    f = Fake(stats=[[(0, 0, 0)], [(10, 0, 100)], [(10, 50, 200)]], mc=[0, 500_000, 1_000_000], times=[0.0, 1.0, 2.0])
    m = f.meter()
    m.sample()
    assert m.sample() == {"threads": [{"user": 0.0, "system": 50.0}], "server": 50.0}
    assert f.waits == [0.5]


def test_server_container_down_gives_none_but_threads_still_work():
    f = Fake(stats=[[(0, 0, 0)], [(10, 0, 100)]], mc=[None, None], times=[0.0, 1.0])
    assert f.meter().sample() == {"threads": [{"user": 10.0, "system": 0.0}], "server": None}
