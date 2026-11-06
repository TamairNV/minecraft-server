#!/usr/bin/env python3
"""Server-list ping a host: ping.py <host> [port]"""
import asyncio, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "web"))
from app.slp import ping
host, port = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 25565
r = asyncio.run(asyncio.wait_for(ping(host, port), 5))
print(host, r["version"]["name"], r["players"]["online"], "/", r["players"]["max"], "|", r.get("description"))
