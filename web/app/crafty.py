"""Crafty API client, using the site's restricted account: it can send commands
and start, stop or back up the server, but can't change Crafty's settings."""
import os
import threading

import httpx


class CraftyError(Exception):
    pass


class Crafty:
    def __init__(self, base=None, user=None, password=None, server_id=None, backup_id=None):
        self.base = base or os.environ.get("CRAFTY_URL", "https://crafty:8443/api/v2")
        self.user = user or os.environ.get("CRAFTY_WEB_USER", "")
        self.password = password or os.environ.get("CRAFTY_WEB_PASSWORD", "")
        self.sid = server_id or os.environ.get("CRAFTY_SERVER_ID", "")
        self.backup_id = backup_id or os.environ.get("CRAFTY_BACKUP_ID", "")
        self._token = None
        self._lock = threading.Lock()
        # Crafty's self-signed certificate, reached only on the private compose network.
        self._http = httpx.Client(verify=False, timeout=30)

    def _login(self):
        r = self._http.post(f"{self.base}/auth/login", json={"username": self.user, "password": self.password})
        if r.status_code != 200:
            raise CraftyError("Crafty login failed")
        self._token = r.json()["data"]["token"]

    def _call(self, method, path, content=None):
        try:
            with self._lock:
                if self._token is None:
                    self._login()
                token = self._token
            headers = {"Authorization": f"Bearer {token}"}
            r = self._http.request(method, self.base + path, content=content, headers=headers)
            if r.status_code in (401, 403):
                with self._lock:
                    self._login()
                    headers["Authorization"] = f"Bearer {self._token}"
                r = self._http.request(method, self.base + path, content=content, headers=headers)
        except httpx.HTTPError as e:
            raise CraftyError(f"Can't reach Crafty: {type(e).__name__}") from None
        if r.status_code != 200:
            raise CraftyError(f"Crafty refused {path.rsplit('/', 1)[-1]} ({r.status_code})")
        return r.json()

    def stats(self) -> dict:
        data = self._call("GET", f"/servers/{self.sid}/stats")["data"]
        data.pop("server_id", None)  # holds the startup command, which the site doesn't need
        return data

    def running(self) -> bool:
        return bool(self.stats().get("running"))

    def command(self, text: str) -> None:
        self._call("POST", f"/servers/{self.sid}/stdin", content=text.encode())

    def action(self, name: str) -> None:
        self._call("POST", f"/servers/{self.sid}/action/{name}")

    def backup(self) -> None:
        self.action(f"backup_server/{self.backup_id}")
