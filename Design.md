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
- **Mining (PoW):** _difficulty rule_
- **Broadcast:** _how a new block is sent out_
- **Verification:** _checks done on receive_
- **Forks:** _resolution rule (e.g., longest chain)_

## 5. Demo Application
- What it does:
- Transaction format:
- Validity rules:

## 6. Resilience Demos
- Invalid transaction →
- Tampered block →
- Fork scenario →

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

## 9. Open Questions
-