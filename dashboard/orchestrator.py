"""In-process orchestration for the browser dashboard."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Dict, Optional

from blockchain.chain import Chain
from blockchain.tx import Transaction
from blockchain.wallet import Wallet
from net.peer import Peer
from net.tracker import Tracker


DEFAULT_HOST = "127.0.0.1"
DEFAULT_TRACKER_PORT = 9000
DEFAULT_FIRST_PEER_PORT = 9201
DEFAULT_DIFFICULTY_BITS = 4


@dataclass
class ManagedWallet:
    id: int
    name: str
    wallet: Wallet
    initial_balance: int = 0

    @property
    def address(self) -> str:
        return self.wallet.address()


@dataclass
class ManagedPeer:
    id: int
    peer: Peer
    task: Optional[asyncio.Task[None]]


class DashboardOrchestrator:
    def __init__(
        self,
        *,
        host: str = DEFAULT_HOST,
        tracker_port: int = DEFAULT_TRACKER_PORT,
        first_peer_port: int = DEFAULT_FIRST_PEER_PORT,
        difficulty_bits: int = DEFAULT_DIFFICULTY_BITS,
    ) -> None:
        self.host = host
        self.tracker_port = tracker_port
        self.first_peer_port = first_peer_port
        self.difficulty_bits = difficulty_bits
        self.tracker: Tracker | None = None
        self.tracker_task: asyncio.Task[None] | None = None
        self.peers: Dict[int, ManagedPeer] = {}
        self.wallets: Dict[int, ManagedWallet] = {}
        self._next_peer_id = 1
        self._next_wallet_id = 1
        self._lock = asyncio.Lock()

    async def start_tracker(self) -> dict:
        async with self._lock:
            if self.tracker_task is None or self.tracker_task.done():
                self.tracker = Tracker(self.host, self.tracker_port)
                self.tracker_task = asyncio.create_task(self.tracker.serve())
        await asyncio.sleep(0.05)
        return self.snapshot()["tracker"]

    async def stop(self) -> None:
        tasks: list[asyncio.Task[None]] = []
        if self.tracker_task is not None:
            tasks.append(self.tracker_task)
        tasks.extend(mp.task for mp in self.peers.values() if mp.task is not None)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def create_wallet(self, name: str | None = None, initial_balance: int = 0) -> dict:
        if initial_balance < 0:
            raise ValueError("initial_balance must be non-negative")
        wallet_id = self._next_wallet_id
        self._next_wallet_id += 1
        managed = ManagedWallet(
            id=wallet_id,
            name=name or f"Wallet {wallet_id}",
            wallet=Wallet.generate(),
            initial_balance=initial_balance,
        )
        self.wallets[wallet_id] = managed
        if initial_balance:
            self._seed_existing_peers(managed.address, initial_balance)
        return self._wallet_snapshot(managed)

    async def create_peer(
        self,
        *,
        miner_wallet_id: int | None = None,
        start_mining: bool = False,
    ) -> dict:
        await self.start_tracker()
        miner_address = None
        if start_mining:
            if miner_wallet_id is None:
                raise ValueError("miner_wallet_id is required")
            miner_address = self._wallet_for(miner_wallet_id).address

        peer_id = self._next_peer_id
        self._next_peer_id += 1
        port = self.first_peer_port + peer_id - 1
        peer = Peer(
            listen_host=self.host,
            listen_port=port,
            tracker_host=self.host,
            tracker_port=self.tracker_port,
            chain=Chain(
                difficulty_bits=self.difficulty_bits,
                balances=self._seed_balances(),
            ),
            miner_address=miner_address,
        )
        task = asyncio.create_task(peer.run())
        self.peers[peer_id] = ManagedPeer(id=peer_id, peer=peer, task=task)
        await asyncio.sleep(0.05)
        return self._peer_snapshot(self.peers[peer_id])

    async def submit_transaction(
        self,
        *,
        peer_id: int,
        sender_wallet_id: int,
        recipient_wallet_id: int,
        amount: int,
    ) -> dict:
        sender = self._wallet_for(sender_wallet_id)
        recipient = self._wallet_for(recipient_wallet_id)
        peer = self._peer_for(peer_id).peer
        nonce = self._next_nonce(peer, sender.address)
        tx = Transaction(sender.address, recipient.address, amount, nonce)
        tx.sign(sender.wallet)
        tx_hash = await peer.submit_tx(tx)
        return {"hash": tx_hash, "tx": tx.to_dict()}

    def snapshot(self) -> dict:
        return {
            "tracker": self._tracker_snapshot(),
            "wallets": [
                self._wallet_snapshot(wallet)
                for wallet in sorted(self.wallets.values(), key=lambda w: w.id)
            ],
            "peers": [
                self._peer_snapshot(peer)
                for peer in sorted(self.peers.values(), key=lambda p: p.id)
            ],
        }

    def _seed_balances(self) -> dict[str, int]:
        return {
            wallet.address: wallet.initial_balance
            for wallet in self.wallets.values()
            if wallet.initial_balance
        }

    def _seed_existing_peers(self, address: str, amount: int) -> None:
        for managed in self.peers.values():
            managed.peer.chain.balances[address] = amount
            managed.peer.chain._seed_balances[address] = amount

    def _next_nonce(self, peer: Peer, sender: str) -> int:
        pending = [
            tx.nonce
            for tx in peer.mempool
            if tx.sender == sender and tx.nonce > peer.chain.nonces.get(sender, 0)
        ]
        return max(pending, default=peer.chain.nonces.get(sender, 0)) + 1

    def _wallet_for(self, wallet_id: int) -> ManagedWallet:
        wallet = self.wallets.get(wallet_id)
        if wallet is None:
            raise ValueError(f"unknown wallet_id: {wallet_id}")
        return wallet

    def _peer_for(self, peer_id: int) -> ManagedPeer:
        peer = self.peers.get(peer_id)
        if peer is None:
            raise ValueError(f"unknown peer_id: {peer_id}")
        return peer

    def _wallet_snapshot(self, managed: ManagedWallet) -> dict:
        return {
            "id": managed.id,
            "name": managed.name,
            "address": managed.address,
            "initial_balance": managed.initial_balance,
        }

    def _tracker_snapshot(self) -> dict:
        running = self.tracker_task is not None and not self.tracker_task.done()
        return {
            "host": self.host,
            "port": self.tracker_port,
            "running": running,
            "peers": sorted(self.tracker.peers.keys()) if self.tracker else [],
        }

    def _peer_snapshot(self, managed: ManagedPeer) -> dict:
        peer = managed.peer
        tip = peer.chain.tip
        return {
            "id": managed.id,
            "addr": peer.addr,
            "running": managed.task is not None and not managed.task.done(),
            "is_mining": peer.miner_address is not None,
            "height": peer.chain.height,
            "known_peers": sorted(peer.peers),
            "mempool_size": len(peer.mempool),
            "tip_hash": tip.hash().hex(),
            "mempool": [self._tx_snapshot(tx) for tx in peer.mempool],
            "balances": self._balances_snapshot(peer),
            "blocks": [self._block_snapshot(block) for block in peer.chain.blocks],
        }

    def _balances_snapshot(self, peer: Peer) -> list[dict]:
        return [
            {
                "wallet_id": wallet.id,
                "name": wallet.name,
                "address": wallet.address,
                "balance": peer.chain.balances.get(wallet.address, 0),
                "nonce": peer.chain.nonces.get(wallet.address, 0),
            }
            for wallet in sorted(self.wallets.values(), key=lambda w: w.id)
        ]

    def _block_snapshot(self, block) -> dict:
        return {
            "index": block.index,
            "timestamp": block.timestamp,
            "nonce": block.nonce,
            "hash": block.hash().hex(),
            "prev_hash": block.prev_hash.hex(),
            "tx_count": len(block.txs),
            "txs": [self._tx_snapshot(tx) for tx in block.txs],
        }

    def _tx_snapshot(self, tx: Transaction) -> dict:
        return {
            "hash": tx.hash().hex(),
            "sender": tx.sender,
            "recipient": tx.recipient,
            "amount": tx.amount,
            "nonce": tx.nonce,
            "signature": tx.signature,
        }
