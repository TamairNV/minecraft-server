#!/usr/bin/env python3
"""Tiny client for the local Crafty API. Reads ~/minecraft/.env. Prints no secrets."""
import json, os, ssl, sys, urllib.request, urllib.error
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "https://127.0.0.1:8444/api/v2"
CTX = ssl._create_unverified_context()  # Crafty's own self-signed cert on loopback

def env():
    out = {}
    p = os.path.join(HERE, ".env")
    if os.path.exists(p):
        for line in open(p):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k] = v
    return out

_token = None

def login():
    global _token
    if _token is None:
        e = env()
        _token = call("POST", "/auth/login", {"username": e["CRAFTY_ADMIN_USER"], "password": e["CRAFTY_ADMIN_PASSWORD"]}, token="")["data"]["token"]
    return _token

def call(method, path, body=None, token=None):
    token = login() if token is None else token
    req = urllib.request.Request(BASE + path, method=method,
                                 data=None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode()))
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=120) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
    try:
        return json.loads(raw)
    except ValueError:
        return {"status": "raw", "data": raw.decode(errors="replace")[:500]}
