"""Tracker: maintains the live peer set and pushes updates.

Responsibilities (Design.md §3):
  - Accept REGISTER / UNREGISTER from peers.
  - Receive HEARTBEATs and evict peers that go silent for too long.
  - Push the updated PEER_LIST to every live peer after any membership change.

This file is a structured stub: the asyncio scaffolding is here, but most
methods are TODOs so the team can flesh out the policy.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Set

from . import messages as M


HEARTBEAT_TIMEOUT_S = 15.0
SWEEP_INTERVAL_S = 5.0


log = logging.getLogger("tracker")


@dataclass
class PeerInfo:
    addr: str               # "host:port" the peer listens on for peer<->peer traffic
    last_seen: float = field(default_factory=time.time)


class Tracker:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.peers: Dict[str, PeerInfo] = {}
        # Open writers we use to push PEER_LIST back to each peer.
        self._writers: Dict[str, asyncio.StreamWriter] = {}
        self._lock = asyncio.Lock()

    # ---- server entry points -------------------------------------------

    async def serve(self) -> None:
        server = await asyncio.start_server(self._handle_client, self.host, self.port)
        log.info("tracker listening on %s:%d", self.host, self.port)
        async with server:
            await asyncio.gather(server.serve_forever(), self._sweep_loop())

    async def _handle_client(self, reader: asyncio.StreamReader,
                              writer: asyncio.StreamWriter) -> None:
        """One TCP connection per peer; we read messages until it closes."""
        addr = None
        try:
            while True:
                msg = await M.recv(reader)
                addr = await self._dispatch(msg, writer) or addr
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            if addr:
                await self._drop(addr)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    # ---- message dispatch ---------------------------------------------

    async def _dispatch(self, msg: M.Message,
                         writer: asyncio.StreamWriter) -> str | None:
        """Returns the peer's addr if learned from this message, else None."""
        if msg.type == M.REGISTER:
            addr = msg.payload["addr"]
            await self._register(addr, writer)
            return addr
        if msg.type == M.HEARTBEAT:
            addr = msg.payload["addr"]
            await self._touch(addr)
            return addr
        if msg.type == M.UNREGISTER:
            addr = msg.payload["addr"]
            await self._drop(addr)
            return addr
        log.warning("tracker: unknown message type %r", msg.type)
        return None

    # ---- membership ops (TODO: flesh out) ------------------------------

    async def _register(self, addr: str, writer: asyncio.StreamWriter) -> None:
        """Add peer, remember its writer, broadcast PEER_LIST."""
        async with self._lock:
            self.peers[addr] = PeerInfo(addr=addr)
            self._writers[addr] = writer
        log.info("registered %s (peers=%d)", addr, len(self.peers))
        await self._broadcast_peer_list()

    async def _drop(self, addr: str) -> None:
        async with self._lock:
            self.peers.pop(addr, None)
            self._writers.pop(addr, None)
        log.info("dropped %s (peers=%d)", addr, len(self.peers))
        await self._broadcast_peer_list()

    async def _touch(self, addr: str) -> None:
        async with self._lock:
            info = self.peers.get(addr)
            if info:
                info.last_seen = time.time()

    async def _broadcast_peer_list(self) -> None:
        """Push current peer list to every connected peer.

        On send failure we evict the peer (their tracker connection is
        considered dead) so the membership view converges.
        """
        msg = M.Message(type=M.PEER_LIST,
                        payload={"peers": sorted(self.peers.keys())})
        async with self._lock:
            writers = list(self._writers.items())
        broken: list[str] = []
        for addr, w in writers:
            try:
                await M.send(w, msg)
            except Exception as e:
                log.warning("failed to push PEER_LIST to %s: %s", addr, e)
                broken.append(addr)
        for addr in broken:
            await self._drop(addr)

    async def _sweep_loop(self) -> None:
        """Periodically evict peers that haven't sent a heartbeat."""
        while True:
            await asyncio.sleep(SWEEP_INTERVAL_S)
            cutoff = time.time() - HEARTBEAT_TIMEOUT_S
            stale = [a for a, info in self.peers.items() if info.last_seen < cutoff]
            for addr in stale:
                log.info("evicting stale peer %s", addr)
                await self._drop(addr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a tracker node.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(Tracker(args.host, args.port).serve())


if __name__ == "__main__":
    main()
