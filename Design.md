# Design Document: P2P Blockchain + [App Name]

**Authors:** Ian Kammerman, Roy Hwang, Alexander Du

---

## 1. Overview
We are making a Proof of Work blockchain protocol with at least 1 tracker, and 3 clients. We will set the mining difficulty low enough so we can average one block every 15-30 (we can change this time) seconds. 

## 2. Tech Stack
- Language: Python
- Networking: socket, asyncio, socketserver
- Crypto / hashing: hashlib (sha-256)
- GUI (if any): tkinter

## 3. Network (Tracker + Peers)
- Setup: At least 1 tracker and 3 peers
- How peers join / leave: Peers send REGISTER/UNREGISTER messages to the tracker on startup/shutdown, with heartbeats so the tracker can drop crashed peers.
- How the peer list is updated and shared:  Tracker pushes the updated list to all peers after any join/leave.
- How peers talk to each other: Each peer runs its own TCP listener and sends blocks/transactions directly to every peer on its list, dropping duplicates by hash

## 4. Blockchain
- **Block fields:** _index, prev_hash, timestamp, nonce, txs, hash_
- **Mining (PoW):** SHA-256 of the block header must have N leading zero bits (start with N=4).
- **Broadcast:** When a peer mines a block, it sends NEW_BLOCK to every peer on its list.
- **Verification:**  On receive, check (1) prev_hash matches local tip, (2) hash recomputes correctly, (3) hash meets difficulty, (4) all txs are valid.
- **Forks:** Longest chain wins. If an incoming block's prev_hash doesn't match our tip, request the sender's full chain and switch if it's longer.
- **Signatures:** ECDSA

## 5. Demo Application
- What it does:
- A simple wallet/demo app that creates, signs, broadcasts, and accepts peer-to-peer transactions.
- Users can submit transactions from one wallet address to another, and peers mine blocks containing those transactions.
- Transaction format:
- Each transaction is a JSON-like object containing `sender`, `recipient`, `amount`, `timestamp`, and `signature`.
- Optional `txid` can be computed as `SHA-256(sender|recipient|amount|timestamp|signature)` for duplicate detection.
- Validity rules:
- Transactions must be signed by the sender's private key and include a valid signature for the `sender` address.
- The sender must have sufficient balance according to the local UTXO/account state before the transaction is accepted.
- Duplicate transactions and malformed data are rejected.
- Blocks accept only valid transactions and require PoW difficulty before being broadcast.

## 6. Resilience Demos
- Invalid transaction → Submit a transaction where the `sender` balance is insufficient or the signature is invalid; the receiving peer should reject it and keep it out of the mempool.
- Tampered block → Modify a mined block's `txs` or `nonce` before sending it; the recipient peer should recompute the hash, detect the mismatch, and reject the block.
- Fork scenario → Create two competing blocks at the same height from different peers; the network should keep both temporarily, then adopt the longer chain once one peer mines the next block.

## 7. Extra Credit (planned)
- [ ] GUI
- [ ] Dynamic difficulty
- [ ] Merkle tree / multiple txs per block
- [ ] _[Other]_

## 8. Timeline
| Milestone | Date |
|-----------|------|
| Network + tracker | |
| Blockchain + mining | |
| Broadcast + forks | |
| Demo app | |
| Resilience demos | |

## 9. Work Partition
- Ian: tracker and peer networking, registration/heartbeat, peer list synchronization, broadcast message handling.
- Roy: blockchain core, mining loop, PoW validation, block verification, fork resolution, chain management.
- Alex: demo application, transaction format/signing, wallet UI or CLI, transaction validity rules, end-to-end demo scenarios.

## 10. Open Questions
- Should the tracker and peer components be separate processes, or can they be combined for the demo?
- What is the preferred transaction model: simple account balances or UTXO-style outputs?
- Do we need a specific number of transactions per block for the demo, or is one transaction per block acceptable?
- Is a CLI wallet/demo sufficient, or do you expect a simple GUI as well?
- Should the demo emphasize network resilience and fork handling over transaction signing and validation?