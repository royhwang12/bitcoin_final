# Testing

We test blockchain resilience with unit tests for chain validation edge cases and a live integration demo with three nodes.

## How to run

```bash
python3 test_chain.py

# Integration demo (boots tracker + 3 peers on localhost)
python3 tests/integration_demo.py
```

## Results

- Test_chain: **4/4 passed**
- Integration demo: **20/20 checkpoints passed**

## Unit tests (`tests/test_chain.py`)

Tests core `Chain` validation rules with `difficulty_bits=0` (no real mining, pure logic).

| Test | What it proves |
|------|----------------|
| Append valid block updates balances and nonces | Blocks correctly credit/debit accounts |
| Rejects duplicate transaction hashes | No replaying the same tx in one block |
| Rejects bad block index | Blocks must follow sequentially |
| Rejects in-block overspend | Can't spend more than you have, even mid-block |

## Integration demo (`tests/integration_demo.py`)

Boots a real tracker and 3 peers on `127.0.0.1:9100-9103`. Peer A mines; B and C are passive.

### Pure-logic checkpoints (12)

| # | Checkpoint | What it proves |
|---|-----------|----------------|
| 1 | Genesis is deterministic | Two independent chains produce the same genesis block |
| 2 | ECDSA signature verifies | Sign-then-verify round-trips correctly |
| 3 | Rejects forged signature | Tampered signatures are caught |
| 4 | Rejects overspend | Can't send more than your balance |
| 5 | Rejects skipped nonce | Transactions must use the next sequential nonce |
| 6 | Append updates balances | Multi-tx blocks credit/debit correctly |
| 7 | Append updates nonces | Per-sender nonce increments after mining |
| 8 | Rejects tampered tx amount | Changing an amount post-signing breaks validation |
| 9 | Rejects tampered block nonce | Flipping the PoW nonce breaks the difficulty check |
| 10 | Mining produces valid PoW | Mined hash has the required leading zero bits |
| 11 | Adopts longer valid chain | `replace_if_longer` switches to a strictly longer chain |
| 12 | Refuses equal-length chain | Longest-chain rule is strict `>`, not `>=` |

### Live-network checkpoints (8)

| # | Checkpoint | What it proves |
|---|-----------|----------------|
| 1 | Tracker registration | All 3 peers register with the tracker |
| 2 | Peer list propagation | Every peer discovers the other two |
| 3 | Miner liveness | Peer A produces blocks at `difficulty_bits=4` |
| 4 | NEW_TX flooding | A transaction submitted to A appears in B's and C's mempools |
| 5 | Overspend rejection | An overspending tx is rejected and never enters the mempool |
| 6 | Tampered block rejection | A block with a flipped nonce is rejected; receiver's chain is unchanged |
| 7 | Three-peer convergence | After mining and gossip, A, B, and C share the same chain tip |
| 8 | Strict longest-chain on live data | Equal-length chain is refused even with live peers |

## What this demonstrates

- **Cryptographic integrity** -- signature forgery and tx/block tampering are rejected
- **Economic safety** -- overspends, replays, and nonce-skip attacks all fail
- **Distributed consensus** -- peer discovery, tx/block gossip, chain sync, and longest-chain fork resolution all work across three live nodes
- **Liveness** -- cooperative async mining doesn't starve the network; peers converge in seconds
