"""Minecraft server list ping: who is online, without RCON or plugin access."""
import asyncio
import json
import struct


def varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def packet(pid: int, body: bytes) -> bytes:
    data = varint(pid) + body
    return varint(len(data)) + data


async def read_varint(reader) -> int:
    n = shift = 0
    while True:
        b = (await reader.readexactly(1))[0]
        n |= (b & 0x7F) << shift
        if not b & 0x80:
            return n
        shift += 7
        if shift > 35:
            raise ValueError("varint too long")


async def ping(host: str, port: int) -> dict:
    reader, writer = await asyncio.open_connection(host, port)
    try:
        h = host.encode()
        handshake = varint(-1 & 0xFFFFFFFF) + varint(len(h)) + h + struct.pack(">H", port) + varint(1)
        writer.write(packet(0, handshake) + packet(0, b""))
        await writer.drain()
        await read_varint(reader)  # packet length
        await read_varint(reader)  # packet id
        size = await read_varint(reader)
        return json.loads(await reader.readexactly(size))
    finally:
        writer.close()
