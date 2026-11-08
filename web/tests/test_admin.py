import json
import os
import time

os.environ.update(ACCESS_SITE="mc", CF_TEAM_DOMAIN="t.example", CF_AUD="aud", MC_SERVER_DIR="", MC_BACKUP_DIR="", MC_DATA_DIR="")


def no_network(url):
    from app.modpack import PackError
    raise PackError("offline")


def client(tmp_path, role):
    os.environ["MC_SERVER_DIR"] = str(tmp_path)
    from fastapi.testclient import TestClient
    from app import admin, modpack, tnv_access
    from app.tnv_access import AccessDenied, AccessUser
    from fastapi import FastAPI

    class Guard:
        def check(self, token, required=None):
            if not token:
                raise AccessDenied(401, "no")
            order = ["user", "admin"]
            if required and order.index(role) < order.index(required):
                raise AccessDenied(403, "no")
            return AccessUser("a@example.com", role)

    tnv_access.configure(Guard())
    sent = []

    class Crafty:
        running = lambda self: True
        command = lambda self, text: sent.append(text)

    class Worlds:
        backups = tmp_path
        busy = lambda self: False

    app = FastAPI()
    app.include_router(admin.build(tmp_path, tmp_path, Crafty(), Worlds(), None, modpack.Packs(tmp_path, tmp_path / "modpack", fetch=no_network)))
    return TestClient(app), sent


HEAD = {"Cf-Access-Jwt-Assertion": "x", "Origin": "http://testserver"}


def test_needs_login_and_admin_role(tmp_path):
    c, sent = client(tmp_path, "user")
    assert c.get("/admin/api/whitelist").status_code == 401
    assert c.post("/admin/api/command", json={"command": "op me"}, headers=HEAD).status_code == 403
    assert sent == []


def test_other_sites_cant_post(tmp_path):
    c, sent = client(tmp_path, "admin")
    bad = {**HEAD, "Origin": "https://evil.example"}
    assert c.post("/admin/api/command", json={"command": "op me"}, headers=bad).status_code == 403
    assert sent == []


def test_whitelist_rejects_command_injection(tmp_path):
    c, sent = client(tmp_path, "admin")
    r = c.post("/admin/api/whitelist", json={"name": "x; op me", "action": "add"}, headers=HEAD)
    assert r.status_code == 400 and sent == []
    assert c.post("/admin/api/whitelist", json={"name": "Good_Name", "action": "add"}, headers=HEAD).status_code == 200
    assert sent == ["whitelist add Good_Name"]
    assert json.loads((tmp_path / "audit.jsonl").read_text())["who"] == "a@example.com"


def test_modpack_build_needs_admin_and_same_origin(tmp_path):
    c, _ = client(tmp_path, "user")
    assert c.post("/admin/api/modpack/build", json={}, headers=HEAD).status_code == 403
    c, _ = client(tmp_path, "admin")
    assert c.post("/admin/api/modpack/build", json={}, headers={**HEAD, "Origin": "https://evil.example"}).status_code == 403
    assert not (tmp_path / "audit.jsonl").exists()


def test_modpack_failure_is_reported_and_audited(tmp_path):
    c, _ = client(tmp_path, "admin")
    assert c.post("/admin/api/modpack/build", json={}, headers=HEAD).status_code == 200
    for _ in range(100):
        job = c.get("/admin/api/modpack", headers=HEAD).json()["job"]
        if job["status"] != "running":
            break
        time.sleep(0.02)
    assert job["status"] == "failed"
    for _ in range(100):
        actions = [json.loads(line)["action"] for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
        if "modpack failed" in actions:
            break
        time.sleep(0.02)
    assert actions == ["modpack build", "modpack failed"]


def test_settings_save_needs_admin_same_origin_and_is_audited(tmp_path):
    (tmp_path / "server.properties").write_text("view-distance=14\n")
    c, sent = client(tmp_path, "user")
    assert c.post("/admin/api/settings", json={"values": {"view-distance": 12}}, headers=HEAD).status_code == 403
    c, sent = client(tmp_path, "admin")
    bad = {**HEAD, "Origin": "https://evil.example"}
    assert c.post("/admin/api/settings", json={"values": {"view-distance": 12}}, headers=bad).status_code == 403
    assert c.post("/admin/api/settings", json={"values": {"view-distance": 99}}, headers=HEAD).status_code == 400
    r = c.post("/admin/api/settings", json={"values": {"view-distance": 12}}, headers=HEAD)
    assert r.status_code == 200 and r.json()["restart"] == ["view-distance"]
    assert (tmp_path / "server.properties").read_text() == "view-distance=12\n"
    assert json.loads((tmp_path / "audit.jsonl").read_text())["action"] == "settings"
    assert sent == []
