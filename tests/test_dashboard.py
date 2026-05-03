import unittest

from blockchain.chain import Chain, ValidationError
from blockchain.tx import Transaction
from blockchain.wallet import Wallet
from net.peer import Peer


class PeerSubmitTxTests(unittest.IsolatedAsyncioTestCase):
    async def test_submit_tx_accepts_valid_transaction(self) -> None:
        alice = Wallet.generate()
        bob = Wallet.generate()
        tx = Transaction(alice.address(), bob.address(), amount=25, nonce=1)
        tx.sign(alice)
        peer = Peer(
            listen_host="127.0.0.1",
            listen_port=9201,
            tracker_host="127.0.0.1",
            tracker_port=9200,
            chain=Chain(difficulty_bits=0, balances={alice.address(): 100}),
        )

        tx_hash = await peer.submit_tx(tx)

        self.assertEqual(tx_hash, tx.hash().hex())
        self.assertEqual(peer.mempool, [tx])
        self.assertIn(tx_hash, peer.seen_hashes)

    async def test_submit_tx_accepts_next_nonce_after_pending_transaction(self) -> None:
        alice = Wallet.generate()
        bob = Wallet.generate()
        tx1 = Transaction(alice.address(), bob.address(), amount=25, nonce=1)
        tx2 = Transaction(alice.address(), bob.address(), amount=30, nonce=2)
        tx1.sign(alice)
        tx2.sign(alice)
        peer = Peer(
            listen_host="127.0.0.1",
            listen_port=9201,
            tracker_host="127.0.0.1",
            tracker_port=9200,
            chain=Chain(difficulty_bits=0, balances={alice.address(): 100}),
        )

        await peer.submit_tx(tx1)
        await peer.submit_tx(tx2)

        self.assertEqual([tx.nonce for tx in peer.mempool], [1, 2])

    async def test_submit_tx_rejects_duplicate_after_transaction_is_mined(self) -> None:
        alice = Wallet.generate()
        bob = Wallet.generate()
        tx = Transaction(alice.address(), bob.address(), amount=25, nonce=1)
        tx.sign(alice)
        peer = Peer(
            listen_host="127.0.0.1",
            listen_port=9201,
            tracker_host="127.0.0.1",
            tracker_port=9200,
            chain=Chain(difficulty_bits=0, balances={alice.address(): 100}),
        )
        await peer.submit_tx(tx)
        block = peer.chain.next_block_template(alice.address(), pending=[tx])
        peer.chain.append(block)
        peer._purge_mempool_against_chain()

        with self.assertRaises(ValidationError):
            await peer.submit_tx(tx)

    async def test_submit_tx_rejects_invalid_transaction_without_seen_marker(self) -> None:
        alice = Wallet.generate()
        bob = Wallet.generate()
        tx = Transaction(alice.address(), bob.address(), amount=125, nonce=1)
        tx.sign(alice)
        peer = Peer(
            listen_host="127.0.0.1",
            listen_port=9201,
            tracker_host="127.0.0.1",
            tracker_port=9200,
            chain=Chain(difficulty_bits=0, balances={alice.address(): 100}),
        )

        with self.assertRaises(ValidationError):
            await peer.submit_tx(tx)

        self.assertEqual(peer.mempool, [])
        self.assertNotIn(tx.hash().hex(), peer.seen_hashes)


class DashboardOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    def test_snapshot_reports_wallet_balances_and_peer_progress(self) -> None:
        from dashboard.orchestrator import DashboardOrchestrator, ManagedPeer

        orchestrator = DashboardOrchestrator(first_peer_port=9301)
        wallet = orchestrator.create_wallet("miner", initial_balance=250)
        peer = Peer(
            listen_host="127.0.0.1",
            listen_port=9301,
            tracker_host="127.0.0.1",
            tracker_port=9000,
            chain=Chain(difficulty_bits=0, balances={wallet["address"]: 250}),
            miner_address=wallet["address"],
        )
        orchestrator.peers[1] = ManagedPeer(id=1, peer=peer, task=None)

        state = orchestrator.snapshot()

        self.assertFalse(state["tracker"]["running"])
        self.assertEqual(state["wallets"][0]["name"], "miner")
        self.assertEqual(state["wallets"][0]["address"], wallet["address"])
        self.assertEqual(state["peers"][0]["addr"], "127.0.0.1:9301")
        self.assertEqual(state["peers"][0]["height"], 0)
        self.assertTrue(state["peers"][0]["is_mining"])
        self.assertEqual(state["peers"][0]["balances"][0]["balance"], 250)

    async def test_submit_transaction_uses_next_pending_nonce(self) -> None:
        from dashboard.orchestrator import DashboardOrchestrator, ManagedPeer

        orchestrator = DashboardOrchestrator(first_peer_port=9301)
        alice = orchestrator.create_wallet("Alice", initial_balance=100)
        bob = orchestrator.create_wallet("Bob")
        peer = Peer(
            listen_host="127.0.0.1",
            listen_port=9301,
            tracker_host="127.0.0.1",
            tracker_port=9000,
            chain=Chain(difficulty_bits=0, balances={alice["address"]: 100}),
        )
        orchestrator.peers[1] = ManagedPeer(id=1, peer=peer, task=None)

        await orchestrator.submit_transaction(
            peer_id=1,
            sender_wallet_id=alice["id"],
            recipient_wallet_id=bob["id"],
            amount=10,
        )
        await orchestrator.submit_transaction(
            peer_id=1,
            sender_wallet_id=alice["id"],
            recipient_wallet_id=bob["id"],
            amount=15,
        )

        self.assertEqual([tx.nonce for tx in peer.mempool], [1, 2])


if __name__ == "__main__":
    unittest.main()
