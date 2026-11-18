import asyncio
import json
import os
import socket
import struct
import threading
import time

os.environ.update(ACCESS_SITE="mc", CF_TEAM_DOMAIN="t.example", CF_AUD="aud", MC_SERVER_DIR="", MC_BACKUP_DIR="", MC_DATA_DIR="")

import pytest  # noqa: E402

from app import stress as st  # noqa: E402
from app.slp import packet, read_varint  # noqa: E402


def fake_server():
    """A tiny Minecraft status server on a free port: status reply + pong, like Paper."""
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    box = {}

    async def handle(reader, writer):
        try:
            for _ in range(2):  # handshake, status request
                n = await read_varint(reader)
                await reader.readexactly(n)
            body = json.dumps({"version": {"name": "Fake", "protocol": 1}, "players": {"max": 1, "online": 0}}).encode()
            from app.slp import varint
            writer.write(packet(0, varint(len(body)) + body))
            n = await read_varint(reader)
            ping = await reader.readexactly(n)
            writer.write(varint(len(ping)) + ping)
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    async def main():
        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        box["port"] = server.sockets[0].getsockname()[1]
        box["server"] = server
        ready.set()
        await server.serve_forever()

    threading.Thread(target=lambda: loop.run_until_complete(main()), daemon=True).start()
    ready.wait(5)
    return box["port"]


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wait_done(t, limit=20):
    end = time.time() + limit
    while t.status()["running"] and time.time() < end:
        time.sleep(0.1)
    return t.status()


def test_exchange_with_fake_server():
    port = fake_server()
    c, total, sent, got = asyncio.run(st.exchange("127.0.0.1", port))
    assert 0 <= c <= total and sent > 0 and got > 0


def test_stress_run_counts_and_summary():
    port = fake_server()
    t = st.StressTest({"proxy": ("127.0.0.1", port)})
    t.start("proxy", 3, 5)
    assert t.status()["running"]
    with pytest.raises(RuntimeError):
        t.start("proxy", 1, 5)
    d = wait_done(t)
    r = d["last"]
    assert not d["running"] and r["done"]
    assert r["ok"] > 10 and r["failed"] == 0 and r["fail_pct"] == 0
    assert r["total_ms"]["p50"] is not None and r["connect_ms"]["max"] >= r["connect_ms"]["p50"]
    assert r["timeline"] and sum(p["ok"] for p in r["timeline"]) == r["ok"]


def test_refused_connections_are_counted():
    t = st.StressTest({"direct": ("127.0.0.1", free_port())})
    t.start("direct", 2, 5)
    r = wait_done(t)["last"]
    assert r["ok"] == 0 and r["failed"] > 0 and r["fail_kinds"].get("refused") == r["failed"] and r["fail_pct"] == 100


@pytest.mark.parametrize("args", [("nope", 1, 10), ("proxy", 0, 10), ("proxy", 51, 10), ("proxy", 5, 4), ("proxy", 5, 61)])
def test_limits(args):
    t = st.StressTest({"proxy": ("127.0.0.1", 1)})
    with pytest.raises(ValueError):
        t.start(*args)
    assert not t.status()["running"]


def test_failure_kinds():
    assert st.kind(asyncio.TimeoutError()) == "timeout"
    assert st.kind(ConnectionRefusedError()) == "refused"
    assert st.kind(ConnectionResetError()) == "reset"
    assert st.kind(asyncio.IncompleteReadError(b"", 3)) == "reset"
    assert st.kind(OSError("x")) == "other"


def test_api_admin_only_and_audited(tmp_path, monkeypatch):
    from app import admin
    from tests.test_admin import HEAD, client

    port = fake_server()
    monkeypatch.setattr(admin, "StressTest", lambda: st.StressTest({"proxy": ("127.0.0.1", port), "direct": ("127.0.0.1", port)}))
    c, _ = client(tmp_path, "user")
    assert c.post("/admin/api/network/stress", json={"target": "proxy", "connections": 2, "seconds": 5}, headers=HEAD).status_code == 403

    c, _ = client(tmp_path, "admin")
    bad = {**HEAD, "Origin": "https://evil.example"}
    assert c.post("/admin/api/network/stress", json={"target": "proxy", "connections": 2, "seconds": 5}, headers=bad).status_code == 403
    assert c.post("/admin/api/network/stress", json={"target": "proxy", "connections": 99, "seconds": 5}, headers=HEAD).status_code == 400
    r = c.post("/admin/api/network/stress", json={"target": "proxy", "connections": 2, "seconds": 5}, headers=HEAD)
    assert r.status_code == 200 and r.json()["running"]
    assert c.post("/admin/api/network/stress", json={"target": "direct", "connections": 2, "seconds": 5}, headers=HEAD).status_code == 409
    end = time.time() + 15
    while c.get("/admin/api/network/stress", headers=HEAD).json()["running"] and time.time() < end:
        time.sleep(0.2)
    last = c.get("/admin/api/network/stress", headers=HEAD).json()["last"]
    assert last["ok"] > 0 and "who" not in last
    audit = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert audit[-1]["action"] == "network stress test" and "proxy, 2 connections" in audit[-1]["detail"]
