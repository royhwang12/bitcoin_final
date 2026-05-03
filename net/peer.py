"""Peer node: speaks to the tracker and floods blocks/txs to other peers.

Two surfaces:
  1. Outbound connection to the tracker (REGISTER, periodic HEARTBEAT, listens
     for pushed PEER_LIST messages).
  2. Inbound TCP listener for peer<->peer traffic (NEW_TX, NEW_BLOCK,
     GET_CHAIN, CHAIN).

Wire framing and tracker bookkeeping are unchanged. Validation, mempool
management, fork resolution, and the mining loop are wired into
`blockchain.chain.Chain` per Architecture.md §6.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from blockchain.block import Block
from blockchain.chain import Chain, ValidationError
from blockchain.pow import meets_difficulty
from blockchain.tx import Transaction

from . import messages as M


HEARTBEAT_INTERVAL_S = 5.0
SEEN_CACHE_LIMIT = 4096

# Mining tunables. We mine in chunks so the asyncio loop stays responsive
# (Architecture.md §5: CPU-bound mining must yield).
MINE_CHUNK_NONCES = 5_000
MINE_IDLE_SLEEP_S = 0.5
# How long the mining loop waits for the first PEER_LIST before producing
# block 1. Without this, a miner can race the tracker session and produce
# block 1 with peers={}, leaving the rest of the network permanently behind.
MINING_BOOTSTRAP_S = 1.0
# Read timeout for a CHAIN response after we send GET_CHAIN.
CHAIN_REQUEST_TIMEOUT_S = 5.0


log = logging.getLogger("peer")


@dataclass
class Peer:
    listen_host: str
    listen_port: int
    tracker_host: str
    tracker_port: int

    chain: Chain = field(default_factory=Chain)
    peers: Set[str] = field(default_factory=set)         # other peers' "host:port"
    seen_hashes: Set[str] = field(default_factory=set)   # dedupe by hash hex
    # Pending transactions awaiting inclusion in a block.
    mempool: List[Transaction] = field(default_factory=list)
    # If set, this peer mines on top of self.chain.tip. Address is a hex DER
    # SPKI pubkey (see Wallet.address). Mining is disabled when None.
    miner_address: Optional[str] = None

    _tracker_writer: Optional[asyncio.StreamWriter] = None
    # Guards _sync_chain_from_peers so a flooded NEW_BLOCK that triggers a
    # GET_CHAIN doesn't fan out into N concurrent re-syncs.
    _chain_sync_in_flight: bool = False

    @property
    def addr(self) -> str:
        return f"{self.listen_host}:{self.listen_port}"

    # ---- lifecycle -----------------------------------------------------

    async def run(self) -> None:
        server = await asyncio.start_server(
            self._handle_peer_conn, self.listen_host, self.listen_port,
        )
        log.info("peer %s listening", self.addr)
        async with server:
            tasks = [server.serve_forever(), self._tracker_session()]
            if self.miner_address is not None:
                tasks.append(self._mining_loop())
            await asyncio.gather(*tasks)

    # ---- tracker session ----------------------------------------------

    async def _tracker_session(self) -> None:
        """Connect to tracker, REGISTER, send HEARTBEATs, consume PEER_LISTs."""
        reader, writer = await asyncio.open_connection(self.tracker_host,
                                                        self.tracker_port)
        self._tracker_writer = writer
        await M.send(writer, M.Message(M.REGISTER, {"addr": self.addr}))

        async def heartbeat_loop():
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL_S)
                await M.send(writer, M.Message(M.HEARTBEAT, {"addr": self.addr}))

        async def reader_loop():
            while True:
                msg = await M.recv(reader)
                if msg.type == M.PEER_LIST:
                    await self._on_peer_list(msg.payload.get("peers", []))
                else:
                    log.warning("unexpected msg from tracker: %s", msg.type)

        try:
            await asyncio.gather(heartbeat_loop(), reader_loop())
        except (asyncio.IncompleteReadError, ConnectionResetError) as e:
            log.warning("tracker connection lost: %s", e)
        finally:
            try:
                await M.send(writer, M.Message(M.UNREGISTER, {"addr": self.addr}))
            except Exception:
                pass
            writer.close()

    async def _on_peer_list(self, peer_addrs) -> None:
        new = {a for a in peer_addrs if a != self.addr}
        added = new - self.peers
        removed = self.peers - new
        self.peers = new
        if added or removed:
            log.info("peer set updated: +%s -%s (now=%d)",
                     sorted(added), sorted(removed), len(self.peers))

    # ---- inbound peer<->peer ------------------------------------------

    async def _handle_peer_conn(self, reader: asyncio.StreamReader,
                                  writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                msg = await M.recv(reader)
                await self._dispatch_peer_msg(msg, writer)
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _dispatch_peer_msg(self, msg: M.Message,
                                   writer: asyncio.StreamWriter) -> None:
        if msg.type == M.NEW_TX:
            tx = Transaction.from_dict(msg.payload["tx"])
            await self._on_new_tx(tx)
        elif msg.type == M.NEW_BLOCK:
            block = Block.from_dict(msg.payload["block"])
            await self._on_new_block(block)
        elif msg.type == M.GET_CHAIN:
            await self._on_get_chain(writer)
        elif msg.type == M.CHAIN:
            blocks = [Block.from_dict(b) for b in msg.payload["blocks"]]
            await self._on_chain(blocks)
        else:
            log.warning("unknown peer message: %s", msg.type)

    # ---- handlers ------------------------------------------------------

    async def submit_tx(self, tx: Transaction) -> str:
        """Validate, remember, and flood a locally-created transaction."""
        return await self._accept_tx(tx, skip_seen=False)

    async def _on_new_tx(self, tx: Transaction) -> None:
        try:
            await self._accept_tx(tx, skip_seen=True)
        except ValidationError as e:
            h = tx.hash().hex()
            log.info("dropping invalid tx %s: %s", h[:12], e)

    async def _accept_tx(self, tx: Transaction, *, skip_seen: bool) -> str:
        h = tx.hash().hex()
        if skip_seen and h in self.seen_hashes:
            return h
        self._validate_tx_for_mempool(tx)
        if h in self.seen_hashes and self._has_pending_tx(h):
            return h
        self._mark_seen(h)
        # Replace any pre-existing pending tx from the same sender at this
        # nonce so the latest signature wins; otherwise append.
        replaced = False
        for i, pending in enumerate(self.mempool):
            if pending.sender == tx.sender and pending.nonce == tx.nonce:
                self.mempool[i] = tx
                replaced = True
                break
        if not replaced:
            self.mempool.append(tx)
        log.info("accepted tx %s into mempool (size=%d)", h[:12], len(self.mempool))
        await self._flood(M.Message(M.NEW_TX, {"tx": tx.to_dict()}))
        return h

    def _has_pending_tx(self, h: str) -> bool:
        return any(tx.hash().hex() == h for tx in self.mempool)

    def _validate_tx_for_mempool(self, tx: Transaction) -> None:
        sim_balances: Dict[str, int] = dict(self.chain.balances)
        sim_nonces: Dict[str, int] = dict(self.chain.nonces)
        ordered = sorted(self.mempool, key=lambda t: (t.sender, t.nonce))
        for pending in ordered:
            if pending.sender != tx.sender:
                continue
            if pending.hash() == tx.hash():
                continue
            if pending.amount <= 0 or not pending.verify_signature():
                continue
            sender_balance = sim_balances.get(pending.sender, 0)
            if sender_balance < pending.amount:
                continue
            if pending.nonce != sim_nonces.get(pending.sender, 0) + 1:
                continue
            sim_balances[pending.sender] = sender_balance - pending.amount
            sim_balances[pending.recipient] = (
                sim_balances.get(pending.recipient, 0) + pending.amount
            )
            sim_nonces[pending.sender] = pending.nonce

        if not tx.verify_signature():
            raise ValidationError("bad signature")
        if tx.amount <= 0:
            raise ValidationError("amount must be positive")
        sender_balance = sim_balances.get(tx.sender, 0)
        if sender_balance < tx.amount:
            raise ValidationError("insufficient balance")
        expected_nonce = sim_nonces.get(tx.sender, 0) + 1
        if tx.nonce != expected_nonce:
            raise ValidationError(
                f"bad nonce: expected {expected_nonce}, got {tx.nonce}"
            )

    async def _on_new_block(self, block: Block) -> None:
        h = block.hash().hex()
        if not self._mark_seen(h):
            return
        try:
            self.chain.append(block)
        except ValidationError as e:
            msg = str(e)
            if "prev_hash" in msg or "bad block index" in msg:
                # Possible fork or we're behind: pull chains from peers.
                # We don't know which peer originally floodeded this block
                # (their socket closed), so we ask everyone; longest wins.
                log.info("block %s does not extend tip (%s); requesting chains",
                         h[:12], msg)
                asyncio.create_task(self._sync_chain_from_peers())
            else:
                log.info("dropping invalid block %s: %s", h[:12], msg)
            return

        log.info("appended block %d %s (chain height=%d)",
                 block.index, h[:12], self.chain.height)
        self._purge_mempool_against_chain()
        await self._flood(M.Message(M.NEW_BLOCK, {"block": block.to_dict()}))

    async def _on_get_chain(self, writer: asyncio.StreamWriter) -> None:
        payload = {"blocks": [b.to_dict() for b in self.chain.blocks]}
        await M.send(writer, M.Message(M.CHAIN, payload))

    async def _on_chain(self, blocks) -> None:
        if self.chain.replace_if_longer(blocks):
            log.info("switched to longer chain (height=%d)", self.chain.height)
            self._purge_mempool_against_chain()

    # ---- chain sync (request/response) --------------------------------

    async def _request_chain(self, addr: str) -> None:
        """Open a connection to `addr`, send GET_CHAIN, await one CHAIN reply.

        Unlike `_flood`, this is a real request/response: we keep the reader
        half open long enough to consume the response. Any other failure
        (timeout, connect error, malformed reply) is logged and ignored.
        """
        try:
            host, port_s = addr.split(":")
            reader, writer = await asyncio.open_connection(host, int(port_s))
        except Exception as e:
            log.debug("GET_CHAIN connect to %s failed: %s", addr, e)
            return
        try:
            await M.send(writer, M.Message(M.GET_CHAIN, {}))
            try:
                reply = await asyncio.wait_for(M.recv(reader),
                                               timeout=CHAIN_REQUEST_TIMEOUT_S)
            except (asyncio.TimeoutError, asyncio.IncompleteReadError):
                log.debug("GET_CHAIN to %s timed out / closed", addr)
                return
            if reply.type != M.CHAIN:
                log.debug("unexpected reply to GET_CHAIN from %s: %s",
                          addr, reply.type)
                return
            blocks = [Block.from_dict(b) for b in reply.payload["blocks"]]
            await self._on_chain(blocks)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _sync_chain_from_peers(self) -> None:
        """Ask every known peer for their chain in parallel, swap to the
        longest valid one. Guarded so a burst of NEW_BLOCK floods can't
        kick off N concurrent re-syncs."""
        if self._chain_sync_in_flight:
            return
        self._chain_sync_in_flight = True
        try:
            await asyncio.gather(
                *(self._request_chain(a) for a in list(self.peers)),
                return_exceptions=True,
            )
        finally:
            self._chain_sync_in_flight = False

    # ---- dedup cache ---------------------------------------------------

    def _mark_seen(self, h: str) -> bool:
        """Record `h` in the dedup cache. Returns False if it was already seen.

        Enforces SEEN_CACHE_LIMIT by dropping the cache when it would overflow.
        We use bulk eviction rather than FIFO to keep the set surface from
        Architecture.md §4.4 unchanged.
        """
        if h in self.seen_hashes:
            return False
        if len(self.seen_hashes) >= SEEN_CACHE_LIMIT:
            self.seen_hashes.clear()
        self.seen_hashes.add(h)
        return True

    # ---- mempool helpers ----------------------------------------------

    def _purge_mempool_against_chain(self) -> None:
        """Drop any pending tx whose nonce is now stale or whose sender no
        longer has the funds for it."""
        kept: List[Transaction] = []
        for tx in self.mempool:
            if tx.nonce <= self.chain.nonces.get(tx.sender, 0):
                continue
            if self.chain.balances.get(tx.sender, 0) < tx.amount:
                continue
            kept.append(tx)
        self.mempool = kept

    def _build_block_template(self) -> Block:
        """Build the next block template, including as many mempool txs as
        will validate sequentially against the current chain state."""
        sim_balances: Dict[str, int] = dict(self.chain.balances)
        sim_nonces: Dict[str, int] = dict(self.chain.nonces)
        included: List[Transaction] = []
        # Sort by (sender, nonce) so per-sender ordering is monotonic.
        ordered = sorted(self.mempool, key=lambda t: (t.sender, t.nonce))
        for tx in ordered:
            if tx.amount <= 0 or not tx.verify_signature():
                continue
            bal = sim_balances.get(tx.sender, 0)
            if bal < tx.amount:
                continue
            if tx.nonce != sim_nonces.get(tx.sender, 0) + 1:
                continue
            sim_balances[tx.sender] = bal - tx.amount
            sim_balances[tx.recipient] = sim_balances.get(tx.recipient, 0) + tx.amount
            sim_nonces[tx.sender] = tx.nonce
            included.append(tx)
        return self.chain.next_block_template(self.miner_address, pending=included)

    # ---- mining --------------------------------------------------------

    async def _mining_loop(self) -> None:
        """Continuously try to extend the local chain.

        Per Architecture.md §5 we mine in chunks and `await asyncio.sleep(0)`
        between chunks so the server task and tracker session stay live.
        """
        assert self.miner_address is not None
        log.info("mining enabled; miner_address=%s", self.miner_address[:16])
        # Bootstrap pause: wait for the first PEER_LIST so block 1 isn't
        # produced alone (and then orphaned because nobody received it).
        # Time-bounded so a solo node still mines.
        boot_deadline = time.monotonic() + MINING_BOOTSTRAP_S
        while not self.peers and time.monotonic() < boot_deadline:
            await asyncio.sleep(0.05)
        if self.peers:
            log.info("mining bootstrap done; %d peer(s) known", len(self.peers))
        else:
            log.info("mining bootstrap timeout; mining solo")

        while True:
            tip_at_start = self.chain.tip.hash()
            template = self._build_block_template()
            template.timestamp = time.time()
            template.nonce = 0

            mined: Optional[Block] = None
            while True:
                # If somebody else extended the chain under us, restart with
                # a fresh template on the new tip.
                if self.chain.tip.hash() != tip_at_start:
                    break
                for _ in range(MINE_CHUNK_NONCES):
                    if meets_difficulty(template.hash(), self.chain.difficulty_bits):
                        mined = template
                        break
                    template.nonce += 1
                if mined is not None:
                    break
                await asyncio.sleep(0)

            if mined is None:
                # Tip moved; loop and rebuild.
                continue

            try:
                self.chain.append(mined)
            except ValidationError as e:
                # Race: somebody else's block already extended the tip
                # between our last check and now. Drop and retry.
                log.debug("mined block invalid post-race: %s", e)
                continue

            self._mark_seen(mined.hash().hex())
            self._purge_mempool_against_chain()
            log.info("mined block %d (height=%d, nonce=%d)",
                     mined.index, self.chain.height, mined.nonce)
            await self._flood(M.Message(M.NEW_BLOCK, {"block": mined.to_dict()}))
            # Tiny pause so we don't pin a CPU when the mempool is empty.
            if not self.mempool:
                await asyncio.sleep(MINE_IDLE_SLEEP_S)

    # ---- outbound flooding --------------------------------------------

    async def _flood(self, msg: M.Message) -> None:
        """Send `msg` to every peer in self.peers. Best-effort, no retries."""
        for addr in list(self.peers):
            try:
                host, port_s = addr.split(":")
                _, w = await asyncio.open_connection(host, int(port_s))
                await M.send(w, msg)
                w.close()
            except Exception as e:
                log.debug("flood to %s failed: %s", addr, e)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a blockchain peer node.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--tracker-host", default="127.0.0.1")
    parser.add_argument("--tracker-port", type=int, default=9000)
    parser.add_argument(
        "--miner-address",
        default=None,
        help="Hex DER-SPKI public key to mine for. Omit to disable mining.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    peer = Peer(
        listen_host=args.host,
        listen_port=args.port,
        tracker_host=args.tracker_host,
        tracker_port=args.tracker_port,
        miner_address=args.miner_address,
    )
    asyncio.run(peer.run())


if __name__ == "__main__":
    main()
