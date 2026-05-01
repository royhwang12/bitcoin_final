"""Wire message types and framing.

All messages are JSON objects with a `type` field plus a payload. We use
length-prefixed framing on the TCP stream: a 4-byte big-endian unsigned
length, then that many UTF-8 JSON bytes.

Message catalog (Design.md §3):
    Tracker <-> Peer:
        REGISTER     {addr: "host:port"}             peer -> tracker
        UNREGISTER   {addr: "host:port"}             peer -> tracker
        HEARTBEAT    {addr: "host:port"}             peer -> tracker
        PEER_LIST    {peers: ["host:port", ...]}     tracker -> peer (push)

    Peer <-> Peer:
        NEW_TX       {tx: {...}}
        NEW_BLOCK    {block: {...}}
        GET_CHAIN    {}                              request full chain
        CHAIN        {blocks: [{...}, ...]}          response
"""

from __future__ import annotations

import asyncio
import json
import struct
from dataclasses import dataclass
from typing import Any, Dict


LENGTH_PREFIX = struct.Struct(">I")  # 4-byte unsigned big-endian
MAX_FRAME_BYTES = 8 * 1024 * 1024     # 8 MiB hard cap


# --- message type constants ---------------------------------------------

REGISTER   = "REGISTER"
UNREGISTER = "UNREGISTER"
HEARTBEAT  = "HEARTBEAT"
PEER_LIST  = "PEER_LIST"

NEW_TX     = "NEW_TX"
NEW_BLOCK  = "NEW_BLOCK"
GET_CHAIN  = "GET_CHAIN"
CHAIN      = "CHAIN"


@dataclass
class Message:
    type: str
    payload: Dict[str, Any]

    def to_bytes(self) -> bytes:
        body = json.dumps({"type": self.type, "payload": self.payload},
                          sort_keys=True).encode("utf-8")
        return LENGTH_PREFIX.pack(len(body)) + body

    @classmethod
    def from_json_bytes(cls, body: bytes) -> "Message":
        d = json.loads(body.decode("utf-8"))
        return cls(type=d["type"], payload=d.get("payload", {}))


async def send(writer: asyncio.StreamWriter, msg: Message) -> None:
    writer.write(msg.to_bytes())
    await writer.drain()


async def recv(reader: asyncio.StreamReader) -> Message:
    """Read exactly one length-prefixed frame. Raises EOFError on clean close."""
    header = await reader.readexactly(LENGTH_PREFIX.size)
    (length,) = LENGTH_PREFIX.unpack(header)
    if length > MAX_FRAME_BYTES:
        raise ValueError(f"frame too large: {length} bytes")
    body = await reader.readexactly(length)
    return Message.from_json_bytes(body)
