"""Check who someone is and what role they have on this site. Copy this file into your app. See README.md.

Needs PyJWT[crypto]. Env: ACCESS_SITE, CF_TEAM_DOMAIN, CF_AUD, ACCESS_LOWEST_ROLE (default "user"),
ACCESS_PANEL_URL (default http://access-panel:8001). The app must join Docker network access-net.
"""
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

import jwt

log = logging.getLogger("tnv_access")
FRESH_SECONDS = 60
STALE_SECONDS = 600
NOT_LISTED_SECONDS = 15  # short, so someone just added gets in quickly


class AccessDenied(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class AccessUser:
    email: str
    role: str


def _http_fetch(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, {}


class AccessGuard:
    def __init__(self, site, team_domain, aud, lowest_role="user", panel_url="http://access-panel:8001",
                 fetch=_http_fetch, clock=time.time, key_lookup=None):
        self.site, self.team, self.aud, self.lowest = site, team_domain, aud, lowest_role
        self.panel_url, self.fetch, self.clock = panel_url.rstrip("/"), fetch, clock
        self._cache: dict[str, tuple[str, list[str], float]] = {}
        self._not_listed: dict[str, float] = {}
        self._roles: list[str] | None = None  # the site's list as last reported, fresher than ACCESS_LOWEST_ROLE
        self._lock = threading.Lock()
        if key_lookup is None:
            client = jwt.PyJWKClient(f"https://{team_domain}/cdn-cgi/access/certs", cache_keys=True)
            key_lookup = lambda token: client.get_signing_key_from_jwt(token).key
        self._key_lookup = key_lookup

    def _email(self, token) -> str:
        if not token:
            raise AccessDenied(401, "Not logged in through Cloudflare Access.")
        try:
            claims = jwt.decode(token, self._key_lookup(token), algorithms=["RS256"], audience=self.aud,
                                issuer=f"https://{self.team}")
        except Exception:
            raise AccessDenied(401, "Invalid Cloudflare Access token.") from None
        email = (claims.get("email") or "").strip().lower()
        if not email:
            raise AccessDenied(401, "Invalid Cloudflare Access token.")
        return email

    def _lookup(self, email: str) -> tuple[str, list[str]]:
        now = self.clock()
        with self._lock:
            cached = self._cache.get(email)
            refused = self._not_listed.get(email)
        if refused is not None and now - refused < NOT_LISTED_SECONDS:
            raise AccessDenied(403, "You don't have access to this site.")
        if cached and now - cached[2] < FRESH_SECONDS:
            return cached[0], cached[1]
        url = f"{self.panel_url}/roles/{urllib.parse.quote(self.site, safe='')}/{urllib.parse.quote(email, safe='')}"
        try:
            status, data = self.fetch(url)
            if status == 200:
                role, roles = data["role"], list(data["roles"])
                if role not in roles:
                    raise ValueError("role isn't in the role list")
        except Exception:  # any trouble reaching or reading the panel counts as the panel being away
            status = 0
        if status == 200:
            with self._lock:
                self._cache[email] = (role, roles, now)
                self._not_listed.pop(email, None)
                self._roles = roles
            return role, roles
        if status == 404:  # the panel answered: they aren't on this site
            with self._lock:
                self._cache.pop(email, None)
                self._not_listed[email] = now
            raise AccessDenied(403, "You don't have access to this site.")
        log.warning("access panel unavailable (status %s)", status)
        if cached and now - cached[2] <= STALE_SECONDS:
            return cached[0], cached[1]
        lowest = self._roles[0] if self._roles else self.lowest
        return lowest, [lowest]  # never higher than the lowest role while the panel is away

    def check(self, token, required: str | None = None) -> AccessUser:
        email = self._email(token)
        role, roles = self._lookup(email)
        if required is not None and (required not in roles or roles.index(role) < roles.index(required)):
            raise AccessDenied(403, "You don't have the role needed for this.")
        return AccessUser(email, role)


_guard: AccessGuard | None = None


def configure(guard: AccessGuard | None) -> None:
    global _guard
    _guard = guard


def _from_env() -> AccessGuard:
    global _guard
    if _guard is None:
        env = os.environ
        site, team, aud = env.get("ACCESS_SITE"), env.get("CF_TEAM_DOMAIN"), env.get("CF_AUD")
        if not (site and team and aud):
            raise AccessDenied(503, "Login isn't configured (ACCESS_SITE, CF_TEAM_DOMAIN, CF_AUD).")
        _guard = AccessGuard(site, team, aud, env.get("ACCESS_LOWEST_ROLE", "user"),
                             env.get("ACCESS_PANEL_URL", "http://access-panel:8001"))
    return _guard


def _dependency(required: str | None):
    from fastapi import Header, HTTPException

    def dep(cf_access_jwt_assertion: str | None = Header(None)) -> AccessUser:
        try:
            return _from_env().check(cf_access_jwt_assertion, required)
        except AccessDenied as e:
            raise HTTPException(e.status, str(e)) from None
    return dep


def require_role(name: str):
    """FastAPI dependency: `user = Depends(require_role("admin"))`."""
    return _dependency(name)



# FastAPI dependency for anyone listed on this site: `user = Depends(current_user)`.
current_user = _dependency(None)
