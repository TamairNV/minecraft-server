"""Network tab: the connection between players, the friend's proxy and this server.

The numbers come from the netmon containers (../netmon/netmon.py), which write live.json every
5 s and history.json every minute. This module only reads those files and the server log (to put
names on connections), so the tab never sends anything to the game server.
"""
import ipaddress
import json
import re
import time
from pathlib import Path

RANGES = {"15m": 15 * 60, "1h": 3600, "6h": 6 * 3600, "24h": 86400, "7d": 7 * 86400}
MAX_POINTS = 360
STALE = 30  # s without a new live.json before the monitor counts as stopped
LOGIN_RE = re.compile(r"\]: ([A-Za-z0-9_]{3,16})\[/(.+?)\] logged in with entity id")
LOG_TAIL = 4 * 1024 * 1024


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def peer_key(text: str) -> str | None:
    """'10.100.0.1:25421', '[2a02:c7c::9]:5', '2a02:c7c:0:0:0:0:0:9:5' -> one normal form."""
    text = text.strip()
    if text.startswith("["):
        host, _, port = text[1:].partition("]:")
    else:
        host, _, port = text.rpartition(":")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return f"{ip.compressed}:{port}" if ip.version == 4 else f"[{ip.compressed}]:{port}"


def route(peer: str, tunnel_peer: str = "10.100.0.1") -> str:
    host = peer.rsplit(":", 1)[0].strip("[]")
    if host == tunnel_peer:
        return "proxy"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "unknown"
    if ip.is_loopback:
        return "local"
    if ip.is_private:
        return "LAN" if ip.version == 4 and host.startswith("192.168.") else "local"
    return "direct IPv6" if ip.version == 6 else "direct"


def logins(log_text: str) -> dict:
    """Connection address -> player name, from this log's join lines (newest wins)."""
    out = {}
    for m in LOGIN_RE.finditer(log_text):
        key = peer_key(m.group(2))
        if key:
            out[key] = m.group(1)
    return out


def tcp_retrans(a: dict, b: dict):
    if not a or not b or b["out"] <= a["out"]:
        return None
    return round(100 * max(0, b["retrans"] - a["retrans"]) / (b["out"] - a["out"]), 2)


def recent_points(recent: list) -> list[dict]:
    """The live 5 s samples in the same shape as history points."""
    out, prev_tcp = [], None
    for s in recent:
        tun = s.get("tun") or {}
        sent = tun.get("sent") or 0
        pub = s.get("pub")
        out.append({
            "t": int(s["t"]), "rtt": tun.get("avg"), "jitter": tun.get("jitter"),
            "loss": round(100 * (sent - tun.get("got", 0)) / sent, 2) if sent else None,
            "rx": s.get("wg_rx"), "tx": s.get("wg_tx"),
            "retrans": tcp_retrans(prev_tcp, s.get("tcp")) if prev_tcp else None,
            "pub_ms": pub.get("connect") if pub and pub.get("ok") else None,
            "pub_ok": pub.get("ok") if pub else None,
            "isp": (s.get("isp") or {}).get("avg"),
        })
        prev_tcp = s.get("tcp") or prev_tcp
    return out


def downsample(points: list, n: int = MAX_POINTS) -> list:
    """Average buckets so a week of minutes still draws as a few hundred points. Peaks keep their max."""
    if len(points) <= n:
        return points
    size = len(points) / n
    out = []
    for i in range(n):
        chunk = points[int(i * size):int((i + 1) * size)] or points[-1:]
        p = {"t": chunk[-1]["t"]}
        for k in chunk[0]:
            if k == "t":
                continue
            vals = [c.get(k) for c in chunk if isinstance(c.get(k), (int, float)) and not isinstance(c.get(k), bool)]
            if k == "pub_ok":
                flags = [c.get(k) for c in chunk if c.get(k) is not None]
                p[k] = all(flags) if flags else None
            elif not vals:
                p[k] = None
            elif k.endswith("_max") or k.endswith("_peak") or k in ("players", "loss"):
                p[k] = max(vals)
            else:
                p[k] = round(sum(vals) / len(vals), 2)
        out.append(p)
    return out


class Network:
    def __init__(self, live: Path, history: Path, log_file: Path, clock=time.time):
        self.live, self.history, self.log_file, self.clock = live, history, log_file, clock

    def _names(self) -> dict:
        try:
            with open(self.log_file, "rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - LOG_TAIL))
                return logins(f.read().decode(errors="replace"))
        except OSError:
            return {}

    def view(self, span: str = "1h") -> dict:
        now = self.clock()
        live = read_json(self.live)
        seconds = RANGES.get(span, 3600)
        if not live:
            return {"running": False, "stale": None, "alerts": [self._stale_alert(None)], "series": [], "players": [],
                    "events": [], "summary": None, "range": span}
        age = now - live.get("t", 0)
        alerts = list(live.get("alerts") or [])
        if age > STALE:
            alerts.insert(0, self._stale_alert(age))
        recent = live.get("recent") or []
        if seconds <= 15 * 60:
            series = recent_points(recent)
        else:
            # Minute history up to where the live 5 s samples start, so a fresh monitor still draws.
            pts = (read_json(self.history, {}) or {}).get("points", [])
            live_pts = recent_points(recent)
            start = live_pts[0]["t"] if live_pts else now
            series = downsample([p for p in pts if now - seconds <= p["t"] < start] + live_pts)
        summary = self.summary(live, now)
        players = self.players(live)
        for p in players:
            # Proxy players' ping includes the tunnel; what's left is the proxy <-> player leg.
            if p["route"] == "proxy" and p["rtt"] is not None and summary["rtt"] is not None:
                p["leg"] = round(max(1.0, p["rtt"] - summary["rtt"]), 1)
            else:
                p["leg"] = p["rtt"]
        return {
            "running": age <= STALE, "stale": round(age), "range": span, "tick": live.get("tick"),
            "alerts": alerts, "events": list(reversed(live.get("events") or []))[:30],
            "summary": summary, "series": series,
            "players": players, "public_host": live.get("public_host"), "peer": live.get("peer"),
            "uplink_mbit": live.get("uplink_mbit") or None,
        }

    @staticmethod
    def _stale_alert(age):
        when = "hasn't written anything yet" if age is None else f"last wrote {int(age)} s ago"
        return {"key": "monitor", "level": "crit", "title": "Network monitor isn't running",
                "detail": f"The netmon container {when}. Check `docker compose ps netmon netmon-sockets` in ~/minecraft."}

    def summary(self, live: dict, now: float) -> dict:
        recent = live.get("recent") or []
        last = recent[-1] if recent else {}
        five = [s for s in recent if s["t"] >= now - 300]
        wg = last.get("wg") or {}
        tun = [s["tun"] for s in five if s.get("tun")]
        sent, got = sum(p["sent"] for p in tun), sum(p["got"] for p in tun)
        rtts = [p["avg"] for p in tun if p.get("avg") is not None]
        jit = [p["jitter"] for p in tun if p.get("jitter") is not None]
        pub = next((s for s in reversed(recent) if s.get("pub")), None)
        isp = next((s["isp"] for s in reversed(recent) if s.get("isp")), None)
        dns = next((s["dns"] for s in reversed(recent) if s.get("dns")), None)
        tcp = [s["tcp"] for s in five if s.get("tcp")]
        ifs = last.get("ifs") or {}
        hist = (read_json(self.history, {}) or {}).get("points", [])
        day = [p for p in hist if p["t"] >= now - 86400]
        peaks = [p.get("tx_peak") for p in day if p.get("tx_peak") is not None]
        return {
            "handshake_age": round(now - wg["handshake"]) if wg.get("handshake") else None,
            "endpoint": wg.get("endpoint"), "dns": dns,
            "rtt": round(sum(rtts) / len(rtts), 1) if rtts else None,
            "jitter": round(sum(jit) / len(jit), 1) if jit else None,
            "loss": round(100 * (sent - got) / sent, 2) if sent else None,
            "rx": last.get("wg_rx"), "tx": last.get("wg_tx"),
            "tx_peak_24h": max(peaks) if peaks else None,
            "wg_total": {"rx": wg.get("rx"), "tx": wg.get("tx")},
            "if_errors": sum(ifs.get(k, 0) for k in ("rx_errors", "tx_errors", "rx_dropped", "tx_dropped")) if ifs else None,
            "retrans": tcp_retrans(tcp[0], tcp[-1]) if len(tcp) >= 2 else None,
            "public": {"ok": pub["pub"]["ok"], "connect": pub["pub"].get("connect"), "reply": pub["pub"].get("reply"),
                       "error": pub["pub"].get("error"), "age": round(now - pub["t"])} if pub else None,
            "local": (pub or {}).get("local"),
            "isp": isp.get("avg") if isp else None,
            "voice_drops": last.get("voice_drops"),
        }

    def players(self, live: dict) -> list[dict]:
        socks = live.get("players")
        if socks is None:
            return []
        names = self._names()
        uuids = {}
        for e in read_json(self.log_file.parent.parent / "whitelist.json", []) or []:
            if isinstance(e, dict) and e.get("name"):
                uuids[e["name"].lower()] = e.get("uuid")
        out = []
        for s in socks:
            key = peer_key(s["peer"]) or s["peer"]
            sent, re_ = s.get("bytes_sent") or 0, s.get("bytes_retrans") or 0
            name = names.get(key)
            out.append({
                "name": name, "uuid": uuids.get((name or "").lower()), "route": route(key, live.get("peer", "10.100.0.1")),
                "rtt": s.get("rtt"), "jitter": s.get("rtt_var"), "send_q": s.get("send_q"),
                "retrans_pct": round(100 * re_ / sent, 2) if sent else None,
                "delivery": s.get("delivery"), "sent": sent,
                "tx_rate": s.get("tx_rate"), "rx_rate": s.get("rx_rate"),
            })
        out.sort(key=lambda p: (p["name"] is None, (p["name"] or "").lower()))
        return out
