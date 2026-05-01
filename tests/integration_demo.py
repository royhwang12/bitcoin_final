"""End-to-end integration check for the P2P blockchain.

Run with:

    python tests/integration_demo.py

The script spins up an in-process tracker and three peers on localhost,
exercises the spec requirements (membership, mining, broadcasting,
validation, fork resolution), and prints [PASS] / [FAIL] checkpoints for
each one. It does NOT modify the codebase; it just probes it.

Some checkpoints are network-driven, others poke the pure-logic layer
directly so we can isolate where bugs actually live.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path

# Allow running as `python tests/integration_demo.py` from the repo root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from blockchain.block import Block, genesis_block
from blockchain.chain import Chain, ValidationError
from blockchain.pow import mine
from blockchain.tx import Transaction
from blockchain.wallet import Wallet
from net.peer import Peer
from net.tracker import Tracker


# ------------------------------------------------------------------ utils

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    tag = "[PASS]" if ok else "[FAIL]"
    line = f"{tag} {name}"
    if detail:
        line += f"  --  {detail}"
    print(line, flush=True)
    (PASSED if ok else FAILED).append(name)


def section(title: str) -> None:
    bar = "=" * 70
    print(f"\n{bar}\n{title}\n{bar}", flush=True)


# ---------------------------------------------------------- pure-logic tests

def test_chain_pure_logic() -> None:
    section("Pure-logic checks (no network)")

    alice = Wallet.generate()
    bob = Wallet.generate()
    carol = Wallet.generate()

    # 1. Genesis is deterministic across two fresh chains.
    c1 = Chain(difficulty_bits=0)
    c2 = Chain(difficulty_bits=0)
    check("genesis is deterministic across nodes",
          c1.tip.hash() == c2.tip.hash(),
          f"genesis_hash={c1.tip.hash().hex()[:12]}...")

    # 2. A signed tx round-trips and verifies.
    tx = Transaction(alice.address(), bob.address(), amount=10, nonce=1)
    tx.sign(alice)
    check("ECDSA signature verifies", tx.verify_signature())

    # 3. validate_tx rejects a bad signature.
    bad_sig_tx = Transaction(alice.address(), bob.address(), amount=10, nonce=1,
                             signature="00" * 70)
    chain = Chain(difficulty_bits=0, balances={alice.address(): 100})
    rejected = False
    try:
        chain.validate_tx(bad_sig_tx)
    except ValidationError:
        rejected = True
    check("validate_tx rejects bad signature", rejected)

    # 4. validate_tx rejects overspend.
    chain = Chain(difficulty_bits=0, balances={alice.address(): 5})
    big = Transaction(alice.address(), bob.address(), amount=999, nonce=1)
    big.sign(alice)
    rejected = False
    try:
        chain.validate_tx(big)
    except ValidationError as e:
        rejected = "balance" in str(e)
    check("validate_tx rejects overspend (insufficient balance)", rejected)

    # 5. validate_tx rejects out-of-order nonce.
    chain = Chain(difficulty_bits=0, balances={alice.address(): 100})
    skip = Transaction(alice.address(), bob.address(), amount=10, nonce=5)
    skip.sign(alice)
    rejected = False
    try:
        chain.validate_tx(skip)
    except ValidationError as e:
        rejected = "nonce" in str(e)
    check("validate_tx rejects skipped nonce", rejected)

    # 6. append a valid block, balances and nonces update.
    chain = Chain(difficulty_bits=0, balances={alice.address(): 100})
    tx1 = Transaction(alice.address(), bob.address(), amount=30, nonce=1); tx1.sign(alice)
    tx2 = Transaction(alice.address(), carol.address(), amount=20, nonce=2); tx2.sign(alice)
    block = chain.next_block_template(miner_address=alice.address(), pending=[tx1, tx2])
    chain.append(block)
    check("append updates balances",
          chain.balances[alice.address()] == 50
          and chain.balances[bob.address()] == 30
          and chain.balances[carol.address()] == 20,
          f"alice={chain.balances[alice.address()]} bob={chain.balances[bob.address()]} carol={chain.balances[carol.address()]}")
    check("append updates per-sender nonce",
          chain.nonces[alice.address()] == 2)

    # 7. validate_block rejects a tampered block.
    # We tamper the tx amount AFTER signing -- this breaks the signature, so
    # validate_block must reject the block regardless of PoW difficulty.
    chain = Chain(difficulty_bits=0, balances={alice.address(): 100})
    tx = Transaction(alice.address(), bob.address(), amount=10, nonce=1); tx.sign(alice)
    blk = chain.next_block_template(alice.address(), pending=[tx])
    tampered = Block.from_dict(blk.to_dict())
    tampered.txs[0].amount = 9999   # signature no longer matches payload
    rejected = False
    try:
        fresh = Chain(difficulty_bits=0, balances={alice.address(): 100})
        fresh.validate_block(tampered)
    except ValidationError as e:
        rejected = "signature" in str(e) or "balance" in str(e)
    check("validate_block rejects tampered tx amount", rejected)

    # 7b. At nonzero difficulty, tampering the nonce also breaks PoW.
    chain = Chain(difficulty_bits=8, balances={alice.address(): 100})
    tx = Transaction(alice.address(), bob.address(), amount=10, nonce=1); tx.sign(alice)
    blk = chain.next_block_template(alice.address(), pending=[tx])
    mine(blk, difficulty_bits=8, max_iters=200_000)   # find a valid nonce
    chain.append(blk)
    nonce_tampered = Block.from_dict(blk.to_dict())
    nonce_tampered.nonce += 1
    rejected = False
    try:
        fresh = Chain(difficulty_bits=8, balances={alice.address(): 100})
        fresh.validate_block(nonce_tampered)
    except ValidationError as e:
        rejected = "difficulty" in str(e)
    check("validate_block rejects tampered nonce (PoW broken)", rejected)

    # 8. Proof-of-work check: mine() at difficulty=8 produces a hash with
    # at least 1 leading zero byte.
    miner_blk = Block(index=1, prev_hash=genesis_block().hash(),
                       timestamp=0.0, nonce=0, txs=[])
    mine(miner_blk, difficulty_bits=8, max_iters=200_000)
    check("mine() produces a PoW-valid hash at diff=8",
          miner_blk.hash()[0] == 0,
          f"hash={miner_blk.hash().hex()[:16]}... nonce={miner_blk.nonce}")

    # 9. replace_if_longer accepts a strictly longer valid chain.
    seed = {alice.address(): 100}
    short = Chain(difficulty_bits=0, balances=dict(seed))
    long_ = Chain(difficulty_bits=0, balances=dict(seed))
    # Build a 2-block extension on `long_`.
    txA = Transaction(alice.address(), bob.address(), amount=10, nonce=1); txA.sign(alice)
    txB = Transaction(alice.address(), carol.address(), amount=20, nonce=2); txB.sign(alice)
    long_.append(long_.next_block_template(alice.address(), pending=[txA]))
    long_.append(long_.next_block_template(alice.address(), pending=[txB]))
    swapped = short.replace_if_longer(long_.blocks)
    check("replace_if_longer adopts longer valid chain",
          swapped and short.height == 2,
          f"new_height={short.height}")

    # 10. replace_if_longer refuses an equally-long chain (strict >).
    other = Chain(difficulty_bits=0, balances=dict(seed))
    txOther = Transaction(alice.address(), bob.address(), amount=5, nonce=1); txOther.sign(alice)
    other.append(other.next_block_template(alice.address(), pending=[txOther]))
    refused = not short.replace_if_longer(other.blocks)
    check("replace_if_longer refuses equal/shorter chain", refused,
          f"short.height={short.height} other.height={other.height}")


# ---------------------------------------------------------- network harness

async def wait_for(predicate, timeout_s: float, poll_s: float = 0.05) -> bool:
    """Poll `predicate()` until True or timeout. Returns the final value."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(poll_s)
    return predicate()


async def test_network() -> None:
    section("Network checks (live tracker + 3 peers on localhost)")

    # Quiet the production loggers so our checkpoints stand out. Bump to
    # logging.INFO if you want to see the full protocol chatter.
    for name in ("tracker", "peer", "asyncio"):
        logging.getLogger(name).setLevel(logging.WARNING)

    miner = Wallet.generate()
    bob = Wallet.generate()
    seed_balances = {miner.address(): 1_000}

    tracker = Tracker("127.0.0.1", 9100)
    tracker_task = asyncio.create_task(tracker.serve())
    await asyncio.sleep(0.2)
    print(f"[BOOT] tracker listening on 127.0.0.1:9100", flush=True)

    peer_a = Peer(
        listen_host="127.0.0.1", listen_port=9101,
        tracker_host="127.0.0.1", tracker_port=9100,
        chain=Chain(difficulty_bits=4, balances=dict(seed_balances)),
        miner_address=miner.address(),
    )
    peer_b = Peer(
        listen_host="127.0.0.1", listen_port=9102,
        tracker_host="127.0.0.1", tracker_port=9100,
        chain=Chain(difficulty_bits=4, balances=dict(seed_balances)),
    )
    peer_c = Peer(
        listen_host="127.0.0.1", listen_port=9103,
        tracker_host="127.0.0.1", tracker_port=9100,
        chain=Chain(difficulty_bits=4, balances=dict(seed_balances)),
    )

    a_task = asyncio.create_task(peer_a.run())
    b_task = asyncio.create_task(peer_b.run())
    c_task = asyncio.create_task(peer_c.run())
    print(f"[BOOT] peers up: A=9101 (mining)  B=9102  C=9103", flush=True)

    try:
        # Checkpoint 1: tracker registered all 3 peers.
        ok = await wait_for(lambda: len(tracker.peers) == 3, timeout_s=3.0)
        check("tracker registered 3 peers",
              ok, f"tracker.peers={sorted(tracker.peers.keys())}")

        # Checkpoint 2: peer list propagated to every peer (each peer sees
        # the other two).
        def peers_converged():
            return (peer_a.peers == {"127.0.0.1:9102", "127.0.0.1:9103"}
                    and peer_b.peers == {"127.0.0.1:9101", "127.0.0.1:9103"}
                    and peer_c.peers == {"127.0.0.1:9101", "127.0.0.1:9102"})
        ok = await wait_for(peers_converged, timeout_s=3.0)
        check("PEER_LIST propagated to all peers", ok,
              f"A={sorted(peer_a.peers)} B={sorted(peer_b.peers)} C={sorted(peer_c.peers)}")

        # Checkpoint 3: A mines blocks on its local chain.
        ok = await wait_for(lambda: peer_a.chain.height >= 1, timeout_s=3.0)
        check("miner produces blocks on local chain", ok,
              f"A.height={peer_a.chain.height}")

        # Checkpoint 4: a flooded NEW_BLOCK reaches B and C and they accept
        # it (only works for blocks where their chain is in sync; we test
        # this by injecting a hand-built block 1 directly into the network).
        # First, snapshot A's height so we have a known reference point.
        await asyncio.sleep(0.5)
        height_before = peer_b.chain.height
        # Send a fresh tx to A so we can watch a tx round-trip.
        # Use the next nonce given the chain's current state.
        next_nonce = peer_a.chain.nonces.get(miner.address(), 0) + 1
        tx = Transaction(miner.address(), bob.address(), amount=7, nonce=next_nonce)
        tx.sign(miner)
        # Inject via A's gossip handler so A floods to B and C.
        await peer_a._on_new_tx(tx)

        ok = await wait_for(
            lambda: any(t.hash() == tx.hash() for t in peer_b.mempool)
                    and any(t.hash() == tx.hash() for t in peer_c.mempool),
            timeout_s=3.0,
        )
        check("NEW_TX floods to B and C and lands in their mempools", ok,
              f"B.mempool={len(peer_b.mempool)} C.mempool={len(peer_c.mempool)}")

        # Checkpoint 5: invalid (overspend) tx is rejected by A.
        before_pool = len(peer_a.mempool)
        bad = Transaction(miner.address(), bob.address(),
                          amount=10_000_000,
                          nonce=peer_a.chain.nonces.get(miner.address(), 0) + 1)
        bad.sign(miner)
        await peer_a._on_new_tx(bad)
        check("overspend tx is rejected (mempool unchanged)",
              len(peer_a.mempool) == before_pool,
              f"mempool size before={before_pool} after={len(peer_a.mempool)}")

        # Checkpoint 6: tampered block is rejected by B (mutate nonce).
        good = peer_a.chain.tip
        tampered = Block.from_dict(good.to_dict())
        tampered.nonce += 1   # invalidates PoW + chain link
        height_b = peer_b.chain.height
        await peer_b._on_new_block(tampered)
        check("tampered block is rejected (B's height unchanged)",
              peer_b.chain.height == height_b,
              f"B.height={peer_b.chain.height}")

        # Checkpoint 7: chain convergence across the 3 peers.  This is the
        # one we know is broken (GET_CHAIN/CHAIN has no return path on a
        # one-shot flooded socket); we still report it honestly.
        await asyncio.sleep(2.0)   # give the network a generous window
        same_tip = (peer_a.chain.tip.hash()
                    == peer_b.chain.tip.hash()
                    == peer_c.chain.tip.hash())
        check("all 3 peers converge to the same tip", same_tip,
              f"A.height={peer_a.chain.height} "
              f"B.height={peer_b.chain.height} "
              f"C.height={peer_c.chain.height}")

        # Checkpoint 8: replace_if_longer is a no-op when chains are equal
        # length (Design.md §4 fork rule uses strict >, not >=).
        snap_b = Chain(blocks=list(peer_b.chain.blocks),
                       difficulty_bits=peer_b.chain.difficulty_bits,
                       balances=dict(seed_balances))
        swapped = snap_b.replace_if_longer(peer_a.chain.blocks)
        check("replace_if_longer refuses equal-length chain (strict >)",
              not swapped and snap_b.height == peer_a.chain.height,
              f"swapped={swapped} snap_b.height={snap_b.height} A.height={peer_a.chain.height}")

    finally:
        for t in (a_task, b_task, c_task, tracker_task):
            t.cancel()
        await asyncio.gather(a_task, b_task, c_task, tracker_task,
                             return_exceptions=True)
        print(f"[TEARDOWN] all tasks cancelled", flush=True)


# ---------------------------------------------------------------- entrypoint

def main() -> int:
    print("Running blockchain integration demo. Each [PASS]/[FAIL] line is a")
    print("checkpoint against the project spec / Architecture.md.\n")

    test_chain_pure_logic()
    asyncio.run(test_network())

    section("Summary")
    print(f"PASSED: {len(PASSED)}", flush=True)
    print(f"FAILED: {len(FAILED)}", flush=True)
    for name in FAILED:
        print(f"  - {name}", flush=True)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
