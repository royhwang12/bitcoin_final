# Design Document: P2P Proof-of-Work Blockchain

**Authors:** Ian Kammerman, Roy Hwang, Alexander Du

---
## 1. Overview

This project implements a small Bitcoin-style proof-of-work blockchain that runs on a local peer-to-peer network. A tracker maintains live peer membership, peers exchange transactions and blocks directly over TCP, and the browser dashboard drives the demo by creating wallets, starting peers, submitting signed transactions, and watching chain state converge.

The target demo uses one tracker and at least three peers on localhost. One or more peers can mine, while all peers validate incoming transactions and blocks before accepting or forwarding them.

## 2. Blockchain Design

The chain uses an account-balance model rather than UTXOs. Wallet addresses are hex-encoded DER SubjectPublicKeyInfo public keys. A wallet signs transactions with ECDSA, and a transaction is valid only if the signature verifies against the sender address.

Transactions contain:

- `sender`: sender wallet address
- `recipient`: recipient wallet address
- `amount`: positive integer transfer amount
- `nonce`: next per-sender sequential nonce
- `signature`: hex ECDSA signature over the canonical transaction payload

Blocks contain:

- `index`: block height
- `prev_hash`: previous block hash as raw bytes, hex when serialized
- `timestamp`: mining timestamp
- `nonce`: proof-of-work nonce
- `txs`: zero or more transactions
- `hash`: computed SHA-256 block hash, included when serialized for display and transport

The genesis block is deterministic: index `0`, all-zero previous hash, timestamp `0.0`, nonce `0`, and no transactions. This lets every peer start from the same block hash.

## 3. Hashing and Proof of Work

Block hashes are SHA-256 over canonical header bytes:

```text
index as 8-byte big-endian integer
|| prev_hash as 32 raw bytes
|| str(timestamp) as UTF-8
|| nonce as 8-byte big-endian integer
|| concatenated transaction hashes
```

Transaction signing payloads use sorted-key JSON over `sender`, `recipient`, `amount`, and `nonce`. The signature is excluded from the signed payload and included in the transaction hash.

Difficulty is measured in leading zero bits of the block hash. The default runtime difficulty is `24` bits, while tests and the integration demo lower difficulty where needed to keep checks fast. Peer mining uses `mine_chunk()` so CPU-bound nonce search periodically yields back to the asyncio event loop.

## 4. Validation Rules

A transaction is valid when:

- its signature verifies against the sender public key,
- its amount is positive,
- the sender has enough balance,
- its nonce is exactly one greater than the sender's last accepted nonce.

A block is valid when:

- its index extends the local chain by one,
- its `prev_hash` equals the local tip hash,
- its hash satisfies the current difficulty,
- every transaction is valid in order,
- no transaction hash appears twice within the block,
- applying all transactions keeps balances non-negative and advances sender nonces sequentially.

When a peer appends a block, it updates balances and nonces, removes mined or now-invalid transactions from the mempool, and floods the block to known peers.

## 5. Peer-to-Peer Protocol

The tracker is responsible for peer discovery only. Peers connect to it, register their listener address, send heartbeats, and unregister on shutdown when possible. The tracker evicts peers that miss heartbeats and pushes a fresh `PEER_LIST` to all registered peers after membership changes.

Peers communicate directly with each other after discovery. Each peer runs a TCP listener for inbound peer messages, keeps a set of known peer addresses, deduplicates transactions and blocks by hash, and uses best-effort one-shot outbound TCP connections for gossip.

The current timing constants are intentionally small for a local demo: peers send `HEARTBEAT` every 5 seconds, the tracker evicts peers after 15 seconds without a heartbeat, and the tracker checks for stale peers every 5 seconds. Peer-to-peer connection attempts are bounded by a 2-second timeout, and chain-request replies are bounded by a 5-second timeout so one unresponsive peer cannot stall gossip or mining.

Every network frame uses a 4-byte big-endian length prefix followed by UTF-8 JSON. Frames larger than 8 MiB are rejected. Every JSON body has this envelope:

```json
{
  "type": "MESSAGE_TYPE",
  "payload": {}
}
```

Tracker messages:

- `REGISTER`: peer to tracker, `{"addr": "host:port"}`
- `HEARTBEAT`: peer to tracker, `{"addr": "host:port"}`
- `UNREGISTER`: peer to tracker, `{"addr": "host:port"}`
- `PEER_LIST`: tracker to peer, `{"peers": ["host:port"]}`

Peer messages:

- `NEW_TX`: floods a serialized transaction
- `NEW_BLOCK`: floods a serialized block
- `GET_CHAIN`: asks another peer for its full chain
- `CHAIN`: replies with serialized blocks

## 6. Protocol Invariants

- All hashes are SHA-256 digests stored as 32 raw bytes internally and converted to hex when crossing a JSON boundary.
- Wallet addresses and transaction senders are hex-encoded DER SubjectPublicKeyInfo public keys. The rest of the system treats those strings as opaque identities.
- Any JSON that contributes to a hash or signature is serialized with sorted keys, so peers independently produce the same byte string.
- The serialized block `hash` field is for display and transport convenience. Deserialization ignores it, and validation recomputes the hash from the canonical block header.
- Tracker addresses are the peer listener addresses in `host:port` form, not the ephemeral source ports of tracker TCP connections.
- New network message types require a message constant, a documented payload shape, and a dispatch branch in the tracker or peer handler.

## 7. Mempool, Forks, and Chain Sync

Peers validate transactions before accepting them into the mempool. The mempool accounts for already-pending transactions from the same sender, so a peer rejects overspends and skipped nonces before mining. When a valid transaction is accepted, it is flooded with `NEW_TX`.

If an incoming block does not extend the local tip, the peer treats it as evidence that it may be behind or on a fork. It asks known peers for their full chains with `GET_CHAIN`, validates each candidate from genesis, and adopts only a strictly longer valid chain. Equal-length and shorter chains are refused.

When a peer learns about new peers from the tracker, it starts a background chain sync and gossips its current mempool to the newcomers. This helps newly joined peers catch up before they mine or receive fresh transactions.

## 8. Demo Application + Dashboard

The demo application is a browser dashboard backed by a FastAPI control plane. It runs the tracker and peers in-process as asyncio tasks so the full network can be demonstrated from one local server while still exercising the real tracker, peer, wallet, transaction, mining, and chain-validation code.

The FastAPI layer exposes:

- `GET /`: returns the dashboard HTML.
- `GET /api/state`: returns the latest tracker, wallet, peer, mempool, balance, and block snapshot.
- `POST /api/start`: starts the in-process tracker.
- `POST /api/wallets`: creates a wallet with an optional initial balance.
- `POST /api/peers`: creates a peer and optionally enables mining with a selected wallet address.
- `POST /api/transactions`: creates, signs, validates, and submits a transaction through a selected peer.
- `WS /ws/state`: pushes a fresh dashboard snapshot every 0.5 seconds.

`DashboardOrchestrator` is the demo control plane. It starts the tracker as an asyncio task, creates peers as asyncio tasks, assigns peer listener ports starting at `127.0.0.1:9201`, and connects all peers to the tracker on `127.0.0.1:9000`. Creating a peer automatically starts the tracker if it is not already running.

Wallets are generated in memory with ECDSA keypairs. Initial wallet balances are seeded into each peer's `Chain` when peers are created, and if a funded wallet is added later the orchestrator seeds existing peers too. Transaction submission computes the next nonce from the sender's confirmed chain nonce plus any pending mempool transactions, signs the transaction with the sender wallet, and calls the selected peer's `submit_tx()`.

The frontend calls the REST endpoints for user actions and uses the WebSocket stream for live rendering. It provides controls to:

- start the tracker,
- create wallets with optional initial balances,
- add peers on sequential localhost ports,
- enable mining for a peer by assigning a miner wallet,
- submit signed wallet-to-wallet transactions through a selected peer.

It displays:

- network status, peer heights, known-peer counts, mining status, and tip hashes,
- per-peer wallet balances,
- blocks on the first peer's chain,
- per-peer mempool contents.

## 9. Resilience

The project demonstrates the required failure and convergence behavior:

- Invalid signatures and malformed transaction data are rejected.
- Overspending transactions are rejected before they enter the mempool.
- Duplicate transaction hashes inside a block are rejected.
- Tampering with a transaction amount after signing breaks validation.
- Tampering with a mined nonce breaks proof-of-work validation.
- Three live peers discover each other through the tracker.
- Transactions and blocks propagate through peer gossip.
- Peers converge to the same chain tip after mining and broadcasting.
- Strict longest-chain fork resolution accepts only a longer valid chain.

## 10. Limitations

- The demo uses seeded initial wallet balances rather than coinbase rewards.
- Difficulty is fixed per chain instance; dynamic difficulty adjustment is not implemented.
- Blocks commit to concatenated transaction hashes rather than a Merkle root.
- Peer gossip is best-effort and does not retry failed outbound sends.
