"""Network monitor for the admin site's Network tab.

Players reach the server through the friend's reverse proxy:
player -> server.tamairnv.co.uk (proxy's router) -> proxy -> WireGuard wg0 -> this host:25565.

Two copies of this script run (see docker-compose.yml):

  sockets  shares the Minecraft container's network, so `ss` sees each player's TCP
           connection (RTT, retransmits, send queue). Writes sockets.json. No privileges.
  host     host network with NET_ADMIN (to read `wg show`) and NET_RAW (ping). Probes the
           tunnel and the public route, reads sockets.json, keeps the history, raises alerts.
           Writes live.json (every tick) and history.json (every minute).

The site only reads these files. Nothing here changes any setting.
"""
import json
import os
import re
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

TICK = float(os.environ.get("NETMON_TICK", "5"))
OUT = Path(os.environ.get("NETMON_OUT", "/out"))
HISTORY_DIR = Path(os.environ.get("NETMON_HISTORY", "/history"))
WG_IF = os.environ.get("NETMON_WG", "wg0")
TUNNEL_PEER = os.environ.get("NETMON_PEER", "10.100.0.1")
PUBLIC_HOST = os.environ.get("NETMON_PUBLIC_HOST", "server.tamairnv.co.uk")
MC_PORT = int(os.environ.get("NETMON_MC_PORT", "25565"))
VOICE_PORT = int(os.environ.get("NETMON_VOICE_PORT", "24454"))
UPLINK_MBIT = float(os.environ.get("NETMON_UPLINK_MBIT", "0") or 0)  # proxy's upload, 0 = unknown
RECENT = int(15 * 60 / TICK)       # 5 s samples kept for the last 15 minutes
HISTORY = 7 * 24 * 60              # 1 minute points kept for a week

# Alert thresholds. Each is (warn, critical).
LOSS = (2.0, 10.0)            # % of tunnel pings lost over 5 minutes
RETRANS = (2.0, 5.0)          # % of the server's TCP segments resent over 5 minutes
HANDSHAKE_DEAD = 180          # s; WireGuard re-handshakes every 2 minutes while in use
PLAYER_RTT = 250              # ms
PLAYER_BACKLOG = 512 * 1024   # bytes waiting to be sent to one player


def write_json(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")))
    os.chmod(tmp, 0o644)
    tmp.replace(path)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def run(cmd: list[str], timeout: float = 10) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


# ---- parsing (pure, tested in test_netmon.py)

def parse_wg_dump(text: str) -> dict | None:
    """First peer of `wg show <if> dump`. The first line is the interface itself."""
    lines = [line.split("\t") for line in text.strip().splitlines()]
    for f in lines[1:]:
        if len(f) >= 8:
            endpoint = None if f[2] == "(none)" else f[2]
            return {"endpoint": endpoint, "handshake": int(f[4]), "rx": int(f[5]), "tx": int(f[6]),
                    "keepalive": None if f[7] == "off" else int(f[7])}
    return None


PING_LOSS = re.compile(r"(\d+) packets transmitted, (\d+) (?:packets )?received")
PING_RTT = re.compile(r"= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms")


def parse_ping(text: str) -> dict:
    m = PING_LOSS.search(text)
    sent, got = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    r = PING_RTT.search(text)
    return {"sent": sent, "got": got,
            "avg": float(r.group(2)) if r else None, "max": float(r.group(3)) if r else None,
            "jitter": float(r.group(4)) if r else None}


def addr(text: str) -> str:
    """'[::ffff:10.100.0.1]:25421' -> '10.100.0.1:25421'."""
    host, _, port = text.rpartition(":")
    host = host.strip("[]")
    if host.startswith("::ffff:") and "." in host:
        host = host[7:]
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def _num(pattern: str, text: str, kind=float):
    m = re.search(pattern, text)
    return kind(m.group(1)) if m else None


def parse_ss(text: str, port: int) -> list[dict]:
    """`ss -tinH` output: a socket line, then its details on an indented line."""
    out, cur = [], None
    for line in text.splitlines():
        if not line.strip():
            continue
        if not line[0].isspace():
            f = line.split()
            cur = None
            if len(f) >= 5 and f[0] == "ESTAB" and f[3].endswith(f":{port}"):
                cur = {"peer": addr(f[4]), "send_q": int(f[2]), "recv_q": int(f[1])}
                out.append(cur)
        elif cur is not None:
            rtt = re.search(r"\brtt:([\d.]+)/([\d.]+)", line)
            retrans = re.search(r"\bretrans:\d+/(\d+)", line)
            cur.update(
                rtt=float(rtt.group(1)) if rtt else None,
                rtt_var=float(rtt.group(2)) if rtt else None,
                retrans=int(retrans.group(1)) if retrans else 0,
                bytes_sent=_num(r"\bbytes_sent:(\d+)", line, int),
                bytes_retrans=_num(r"\bbytes_retrans:(\d+)", line, int) or 0,
                cwnd=_num(r"\bcwnd:(\d+)", line, int),
                delivery=_num(r"\bdelivery_rate ([\d.]+)bps", line) or _mbps(line, "delivery_rate"),
                lastrcv=_num(r"\blastrcv:(\d+)", line, int),
            )
    return out


def _mbps(line: str, key: str):
    m = re.search(rf"\b{key} ([\d.]+)([KMG])bps", line)
    if not m:
        return None
    return float(m.group(1)) * {"K": 1e3, "M": 1e6, "G": 1e9}[m.group(2)]


def parse_snmp_tcp(text: str) -> dict | None:
    rows = [line.split() for line in text.splitlines() if line.startswith("Tcp:")]
    if len(rows) < 2:
        return None
    t = dict(zip(rows[0][1:], rows[1][1:]))
    return {"out": int(t["OutSegs"]), "retrans": int(t["RetransSegs"])}


def parse_udp_drops(text: str, port: int) -> int:
    """Packets the kernel dropped on the voice chat socket (/proc/net/udp, last column)."""
    total = 0
    for line in text.splitlines()[1:]:
        f = line.split()
        if len(f) >= 13 and f[1].rsplit(":", 1)[-1] == f"{port:04X}":
            total += int(f[-1])
    return total


def read_ifstats(name: str, base: str = "/sys/class/net") -> dict | None:
    d = Path(base) / name / "statistics"
    try:
        return {k: int((d / k).read_text()) for k in
                ("rx_bytes", "tx_bytes", "rx_packets", "tx_packets", "rx_errors", "tx_errors", "rx_dropped", "tx_dropped")}
    except (OSError, ValueError):
        return None


# ---- probes

def varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b, n = n & 0x7F, n >> 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def slp(host: str, port: int, timeout: float = 4.0) -> dict:
    """Minecraft status ping: proves the whole path reaches the game, not just an open port."""
    t0 = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            connect = (time.monotonic() - t0) * 1000
            s.settimeout(timeout)
            h = host.encode()
            body = varint(0) + varint(767) + varint(len(h)) + h + struct.pack(">H", port) + varint(1)
            s.sendall(varint(len(body)) + body + b"\x01\x00")
            t1 = time.monotonic()
            first = s.recv(1)
            if not first:
                raise OSError("closed")
            return {"ok": True, "connect": round(connect, 1), "reply": round((time.monotonic() - t1) * 1000, 1)}
    except OSError as e:
        return {"ok": False, "error": type(e).__name__ if not str(e) else str(e)[:80]}


def resolve(host: str) -> str | None:
    try:
        return socket.gethostbyname(host)
    except OSError:
        return None


# ---- alerts (pure)

def rate(new, old, key, seconds):
    if not new or not old or seconds <= 0 or new.get(key) is None or old.get(key) is None:
        return None
    d = new[key] - old[key]
    return d / seconds if d >= 0 else None


def window(recent: list, seconds: float) -> list:
    if not recent:
        return []
    cut = recent[-1]["t"] - seconds
    return [s for s in recent if s["t"] >= cut]


def alerts(recent: list, now: float, uplink_mbit: float = 0) -> list[dict]:
    """Active problems, worst first. Each has key, level (warn/crit), title and detail."""
    out = []
    if not recent:
        return out
    last = recent[-1]
    add = lambda key, level, title, detail: out.append({"key": key, "level": level, "title": title, "detail": detail})

    wg = last.get("wg")
    hs_age = now - wg["handshake"] if wg and wg["handshake"] else None
    if wg is None:
        add("tunnel", "crit", "WireGuard tunnel is down", "wg0 has no peer. Is wg-quick@wg0 running?")
    elif hs_age is None or hs_age > HANDSHAKE_DEAD:
        add("tunnel", "crit", "WireGuard tunnel is down",
            f"No handshake with the proxy for {fmt_age(hs_age)}. The proxy box, its router forward for UDP 51820, "
            "or its internet is down.")

    five = window(recent, 300)
    pings = [s["tun"] for s in five if s.get("tun")]
    sent = sum(p["sent"] for p in pings)
    got = sum(p["got"] for p in pings)
    last3 = [s["tun"] for s in recent[-3:] if s.get("tun")]
    if len(last3) == 3 and all(p["sent"] and not p["got"] for p in last3):
        add("proxy", "crit", "Proxy isn't answering", f"No ping replies from {TUNNEL_PEER} for the last {len(last3) * int(TICK)} s.")
    elif sent >= 60:  # at least a minute of pings
        loss = 100 * (sent - got) / sent
        if loss >= LOSS[0]:
            add("loss", "crit" if loss >= LOSS[1] else "warn", f"Packet loss {loss:.1f}% on the tunnel",
                "Over the last 5 minutes. Loss throttles chunk loading hardest, because TCP backs off on every lost packet.")

    pub = [s["pub"] for s in recent if s.get("pub")][-2:]
    local = [s["local"] for s in recent if s.get("local")][-1:]
    if len(pub) == 2 and not any(p["ok"] for p in pub) and local and local[0]["ok"]:
        hint = ("The tunnel is fine, so it's most likely the proxy's router forward for 25565 (its UPnP lease) or the "
                "proxy's firewall." if not out else "See the tunnel alert above.")
        add("public", "crit", f"Players can't join via {PUBLIC_HOST}", f"The game answers locally but not through the proxy. {hint}")

    if last.get("dns") and wg and wg.get("endpoint"):
        ep_ip = wg["endpoint"].rsplit(":", 1)[0].strip("[]")
        if last["dns"] != ep_ip:
            add("endpoint", "warn", "Proxy's public IP changed",
                f"{PUBLIC_HOST} now points at {last['dns']}, but this server's wg0.conf still sends to {ep_ip}. "
                "Put the new address (or the duckdns name) in Endpoint and restart wg-quick@wg0.")

    tcp = [s for s in five if s.get("tcp")]
    if len(tcp) >= 2:
        d_out = tcp[-1]["tcp"]["out"] - tcp[0]["tcp"]["out"]
        d_re = tcp[-1]["tcp"]["retrans"] - tcp[0]["tcp"]["retrans"]
        if d_out >= 2000 and d_re >= 0:
            pct = 100 * d_re / d_out
            if pct >= RETRANS[0]:
                add("retrans", "crit" if pct >= RETRANS[1] else "warn", f"{pct:.1f}% of game traffic is being resent",
                    "Over the last 5 minutes. Players see slow chunk loading and rubber-banding.")

    if uplink_mbit and len(five) >= 2:
        mbit = [s["wg_tx"] * 8 / 1e6 for s in window(recent, 60) if s.get("wg_tx") is not None]
        if mbit and sum(mbit) / len(mbit) >= 0.8 * uplink_mbit:
            add("bandwidth", "warn", "Tunnel close to the proxy's upload limit",
                f"{sum(mbit) / len(mbit):.1f} of {uplink_mbit:g} Mbit/s over the last minute.")

    voice = [s["voice_drops"] for s in five if s.get("voice_drops") is not None]
    if len(voice) >= 2 and voice[-1] > voice[0]:
        add("voice", "warn", "Voice chat packets dropped", f"{voice[-1] - voice[0]} dropped by this server in the last 5 minutes.")

    slow = {}
    for s in recent[-3:]:
        for p in s.get("players") or []:
            slow.setdefault(p["peer"], []).append(p)
    for peer, seen in slow.items():
        if len(seen) == 3 and all((p.get("send_q") or 0) >= PLAYER_BACKLOG for p in seen):
            add(f"backlog:{peer}", "warn", "A player's connection can't keep up",
                f"{seen[-1]['send_q'] // 1024} KB waiting to be sent to {peer}.")
        elif len(seen) == 3 and all((p.get("rtt") or 0) >= PLAYER_RTT for p in seen):
            add(f"rtt:{peer}", "warn", "A player has very high ping", f"{seen[-1]['rtt']:.0f} ms to {peer}.")

    out.sort(key=lambda a: a["level"] != "crit")
    return out


def fmt_age(s) -> str:
    if s is None:
        return "ever"
    s = int(s)
    return f"{s} s" if s < 120 else f"{s // 60} min" if s < 7200 else f"{s // 3600} h"


def minute_point(samples: list) -> dict:
    """One history point from a minute of 5 s samples."""
    def avg(vals):
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    def peak(vals):
        vals = [v for v in vals if v is not None]
        return round(max(vals), 2) if vals else None

    tun = [s["tun"] for s in samples if s.get("tun")]
    sent, got = sum(p["sent"] for p in tun), sum(p["got"] for p in tun)
    tcp = [s["tcp"] for s in samples if s.get("tcp")]
    retrans = None
    if len(tcp) >= 2 and tcp[-1]["out"] > tcp[0]["out"]:
        retrans = round(100 * max(0, tcp[-1]["retrans"] - tcp[0]["retrans"]) / (tcp[-1]["out"] - tcp[0]["out"]), 2)
    players = [len(s["players"]) for s in samples if s.get("players") is not None]
    pub = [s["pub"] for s in samples if s.get("pub")]
    return {
        "t": int(samples[-1]["t"]),
        "rtt": avg(p["avg"] for p in tun), "rtt_max": peak(p["max"] for p in tun),
        "jitter": avg(p["jitter"] for p in tun),
        "loss": round(100 * (sent - got) / sent, 2) if sent else None,
        "rx": avg(s.get("wg_rx") for s in samples), "tx": avg(s.get("wg_tx") for s in samples),
        "tx_peak": peak(s.get("wg_tx") for s in samples),
        "retrans": retrans,
        "players": max(players) if players else None,
        "pub_ok": all(p["ok"] for p in pub) if pub else None,
        "pub_ms": avg(p.get("connect") for p in pub),
        "isp": avg(s["isp"]["avg"] for s in samples if s.get("isp")),
    }


# ---- loops

def sockets_loop() -> None:
    out = OUT / "sockets.json"
    while True:
        players = parse_ss(run(["ss", "-tinH", f"( sport = :{MC_PORT} )"]), MC_PORT)
        try:
            tcp = parse_snmp_tcp(Path("/proc/net/snmp").read_text())
        except OSError:
            tcp = None
        try:
            voice = parse_udp_drops(Path("/proc/net/udp").read_text() + "\n" +
                                    "\n".join(Path("/proc/net/udp6").read_text().splitlines()[1:]), VOICE_PORT)
        except OSError:
            voice = None
        write_json(out, {"t": time.time(), "players": players, "tcp": tcp, "voice_drops": voice})
        time.sleep(TICK)


def host_loop() -> None:
    hist_file = HISTORY_DIR / "history.json"
    history = read_json(hist_file, {}).get("points", []) if hist_file.exists() else []
    events = read_json(HISTORY_DIR / "events.json", []) or []
    recent: list = []
    minute: list = []
    last_ifs = last_t = None
    active: dict = {}
    n = 0
    while True:
        t = time.time()
        s = {"t": t}
        s["wg"] = parse_wg_dump(run(["wg", "show", WG_IF, "dump"]))
        ifs = read_ifstats(WG_IF)
        if ifs:
            s["ifs"] = ifs
            if last_ifs and last_t:
                s["wg_rx"] = rate(ifs, last_ifs, "rx_bytes", t - last_t)
                s["wg_tx"] = rate(ifs, last_ifs, "tx_bytes", t - last_t)
            last_ifs, last_t = ifs, t
        s["tun"] = parse_ping(run(["ping", "-n", "-q", "-c", "5", "-i", "0.2", "-W", "1", TUNNEL_PEER], timeout=8))
        if n % 6 == 0:  # every 30 s
            s["dns"] = resolve(PUBLIC_HOST)
            s["pub"] = slp(PUBLIC_HOST, MC_PORT)
            s["local"] = slp("127.0.0.1", MC_PORT)
            if s["dns"]:
                isp = parse_ping(run(["ping", "-n", "-q", "-c", "3", "-i", "0.3", "-W", "1", s["dns"]], timeout=6))
                s["isp"] = isp if isp["got"] else None
        sock = read_json(OUT / "sockets.json")
        if sock and t - sock.get("t", 0) < 3 * TICK:
            s["players"], s["tcp"], s["voice_drops"] = sock["players"], sock["tcp"], sock["voice_drops"]

        recent = (recent + [s])[-RECENT:]
        minute.append(s)
        now_alerts = alerts(recent, t, UPLINK_MBIT)
        keys = {a["key"]: a for a in now_alerts}
        for k, a in keys.items():
            if k not in active:
                events.append({"t": int(t), "key": k, "level": a["level"], "title": a["title"], "state": "start"})
        for k, a in active.items():
            if k not in keys:
                events.append({"t": int(t), "key": k, "level": a["level"], "title": a["title"], "state": "clear"})
        if keys.keys() != active.keys():
            events = events[-200:]
            write_json(HISTORY_DIR / "events.json", events)
        active = keys

        if len(minute) >= int(60 / TICK):
            history = (history + [minute_point(minute)])[-HISTORY:]
            minute = []
            write_json(hist_file, {"points": history})

        write_json(OUT / "live.json", {
            "t": t, "tick": TICK, "peer": TUNNEL_PEER, "public_host": PUBLIC_HOST, "uplink_mbit": UPLINK_MBIT,
            "recent": [{k: v for k, v in x.items() if k != "players"} for x in recent],
            "players": s.get("players"), "alerts": now_alerts, "events": events[-50:],
        })
        n += 1
        time.sleep(max(0.5, TICK - (time.time() - t)))


if __name__ == "__main__":
    {"sockets": sockets_loop, "host": host_loop}[sys.argv[1] if len(sys.argv) > 1 else "host"]()
