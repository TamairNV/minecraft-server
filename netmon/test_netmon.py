import netmon as nm

WG = ("PRIVATE\tPUBLIC\t51820\toff\n"
      "PEERKEY\t(none)\t77.97.177.156:51820\t10.100.0.1/32\t1763400000\t1000\t2000\t25\n")

PING = """--- 10.100.0.1 ping statistics ---
5 packets transmitted, 4 received, 20% packet loss, time 803ms
rtt min/avg/max/mdev = 24.383/26.704/29.722/2.234 ms
"""

SS = """ESTAB 0 1048576 [::ffff:172.18.0.3]:25565 [::ffff:10.100.0.1]:25421
\t cubic wscale:7,7 rto:228 rtt:27.5/3.1 mss:1368 cwnd:10 bytes_sent:500000 bytes_retrans:1200 retrans:0/3 lastrcv:40 delivery_rate 3.2Mbps
ESTAB 0 0 [2a00:23c7::5]:25565 [2a02:c7c::9]:50000
\t cubic rtt:15/2 cwnd:20 bytes_sent:10 lastrcv:10
LISTEN 0 50 *:25565 *:*
ESTAB 0 0 [::ffff:172.18.0.3]:8443 [::ffff:172.18.0.4]:40000
\t cubic rtt:1/1
"""


def test_wg_dump():
    assert nm.parse_wg_dump(WG) == {"endpoint": "77.97.177.156:51820", "handshake": 1763400000,
                                    "rx": 1000, "tx": 2000, "keepalive": 25}
    assert nm.parse_wg_dump("PRIVATE\tPUBLIC\t51820\toff\n") is None


def test_ping():
    assert nm.parse_ping(PING) == {"sent": 5, "got": 4, "avg": 26.704, "max": 29.722, "jitter": 2.234}
    lost = nm.parse_ping("3 packets transmitted, 0 received, 100% packet loss, time 2003ms\n")
    assert lost == {"sent": 3, "got": 0, "avg": None, "max": None, "jitter": None}


def test_ss_only_game_connections():
    socks = nm.parse_ss(SS, 25565)
    assert [s["peer"] for s in socks] == ["10.100.0.1:25421", "[2a02:c7c::9]:50000"]
    a = socks[0]
    assert (a["send_q"], a["rtt"], a["retrans"], a["bytes_retrans"], a["cwnd"]) == (1048576, 27.5, 3, 1200, 10)
    assert a["delivery"] == 3.2e6
    assert socks[1]["retrans"] == 0 and socks[1]["delivery"] is None


def test_snmp_and_udp_drops():
    snmp = "Tcp: RtoAlgorithm OutSegs RetransSegs\nTcp: 1 2000 30\n"
    assert nm.parse_snmp_tcp(snmp) == {"out": 2000, "retrans": 30}
    udp = ("  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode ref pointer drops\n"
           "  1: 00000000:5F86 00000000:0000 07 00000000:00000000 00:00000000 00000000  1000 0 1 2 0 7\n"
           "  2: 00000000:0035 00000000:0000 07 00000000:00000000 00:00000000 00000000  1000 0 1 2 0 9\n")
    assert nm.parse_udp_drops(udp, 24454) == 7


def sample(t, **kw):
    s = {"t": t, "wg": {"endpoint": "1.2.3.4:51820", "handshake": t - 30, "rx": 0, "tx": 0, "keepalive": 25},
         "tun": {"sent": 5, "got": 5, "avg": 26, "max": 30, "jitter": 2}}
    s.update(kw)
    return s


def keys(recent, now=None, **kw):
    return [a["key"] for a in nm.alerts(recent, now or recent[-1]["t"], **kw)]


def test_healthy_has_no_alerts():
    assert keys([sample(1000 + i * 5) for i in range(60)]) == []


def test_tunnel_down_on_old_handshake():
    r = [sample(1000)]
    r[0]["wg"]["handshake"] = 700
    assert keys(r) == ["tunnel"]
    r[0]["wg"]["handshake"] = 0
    assert keys(r) == ["tunnel"]


def test_one_bad_sample_is_not_an_alert():
    r = [sample(1000, tun={"sent": 5, "got": 0, "avg": None, "max": None, "jitter": None}), sample(1005)]
    assert keys(r) == []


def test_loss_warn_and_proxy_dead():
    lossy = [sample(1000 + i * 5, tun={"sent": 5, "got": 4 if i % 4 == 0 else 5, "avg": 26, "max": 30, "jitter": 2})
             for i in range(60)]
    a = nm.alerts(lossy, lossy[-1]["t"])
    assert [x["key"] for x in a] == ["loss"] and a[0]["level"] == "warn"
    dead = lossy[:-3] + [sample(1300 + i, tun={"sent": 5, "got": 0, "avg": None, "max": None, "jitter": None}) for i in range(3)]
    assert keys(dead) == ["proxy"]


def test_public_route_down_while_local_works():
    ok = {"ok": True, "connect": 30}
    bad = {"ok": False, "error": "timed out"}
    r = [sample(1000, pub=bad, local=ok), sample(1030, pub=bad, local=ok)]
    a = nm.alerts(r, 1030)
    assert [x["key"] for x in a] == ["public"] and "UPnP" in a[0]["detail"]
    r[0]["pub"] = ok
    assert keys(r) == []


def test_endpoint_changed():
    assert keys([sample(1000, dns="5.6.7.8")]) == ["endpoint"]
    assert keys([sample(1000, dns="1.2.3.4")]) == []


def test_retransmits_and_bandwidth():
    r = [sample(1000, tcp={"out": 0, "retrans": 0}), sample(1100, tcp={"out": 10000, "retrans": 600})]
    a = nm.alerts(r, 1100)
    assert a[0]["key"] == "retrans" and a[0]["level"] == "crit"
    bw = [sample(1000 + i * 5, wg_tx=1.1e6) for i in range(12)]  # 8.8 Mbit/s
    assert keys(bw, uplink_mbit=10) == ["bandwidth"]
    assert keys(bw) == []


def test_player_backlog_needs_three_samples():
    p = lambda q: {"peer": "10.100.0.1:1", "send_q": q, "rtt": 30}
    r = [sample(1000 + i * 5, players=[p(600_000)]) for i in range(3)]
    assert keys(r) == ["backlog:10.100.0.1:1"]
    r[0]["players"] = [p(0)]
    assert keys(r) == []


def test_crit_sorted_first():
    r = [sample(1000, dns="5.6.7.8")]
    r[0]["wg"]["handshake"] = 1
    assert keys(r) == ["tunnel", "endpoint"]


def test_minute_point():
    r = [sample(1000 + i * 5, wg_tx=1000, wg_rx=500, tcp={"out": i * 100, "retrans": i}, players=[{}, {}])
         for i in range(12)]
    r[3]["tun"] = {"sent": 5, "got": 0, "avg": None, "max": None, "jitter": None}
    p = nm.minute_point(r)
    assert p["t"] == 1055 and p["rtt"] == 26 and p["tx"] == 1000 and p["players"] == 2
    assert p["loss"] == round(100 * 5 / 60, 2) and p["retrans"] == 1.0 and p["pub_ok"] is None


def test_slp_against_fake_server():
    import socket
    import threading
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def serve():
        c, _ = srv.accept()
        c.recv(100)
        c.sendall(b"\x05\x00\x03{}")
        c.close()

    threading.Thread(target=serve, daemon=True).start()
    r = nm.slp("127.0.0.1", srv.getsockname()[1])
    assert r["ok"] and r["connect"] >= 0
    srv.close()
    assert nm.slp("127.0.0.1", 1, timeout=1)["ok"] is False


def test_player_rates():
    a = [{"peer": "x", "bytes_sent": 1000, "bytes_received": 100}]
    prev = nm.add_rates(a, {}, 0)
    assert a[0]["tx_rate"] is None
    b = [{"peer": "x", "bytes_sent": 6000, "bytes_received": 600}, {"peer": "y", "bytes_sent": 5}]
    nm.add_rates(b, prev, 5)
    assert (b[0]["tx_rate"], b[0]["rx_rate"], b[1]["tx_rate"]) == (1000.0, 100.0, None)
