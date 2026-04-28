# Architecture

This document is a precise spec of the current scaffolding for the
P2P-Blockchain project (see `Design.md` for the higher-level plan).
It is written so that another agent / LLM can reproduce the same code
layout, public APIs, wire formats, and invariants from scratch.

If you change the protocol, update this file in the same commit.

---

## 1. Scope

Two Python packages:

- `blockchain/` — pure-logic primitives: `Block`, `Transaction`, `Wallet`,
  `Chain`, proof-of-work, plus a pre-existing Merkle helper module.
- `net/` — asyncio-based networking: `Tracker`, `Peer`, and the wire
  message catalog with framing.

No other top-level code is part of the scaffold. There is no test suite,
CLI app, or `requirements.txt` yet.

External runtime dependency:

- `cryptography>=42` (used by `blockchain/wallet.py` for ECDSA on
  SECP256R1 / NIST P-256).

Python: 3.10+ (uses `from __future__ import annotations`, PEP 604
`X | None` syntax, dataclasses).

---

## 2. Repository layout

```text
bitcoin_final/
  Design.md
  Architecture.md          (this file)
  README.md
  blockchain/
    __init__.py
    block.py
    pow.py
    wallet.py
    tx.py
    chain.py
    merkle_utils.py        (pre-existing)
    merkle_prover.py       (pre-existing)
    merkle_verifier.py     (pre-existing)
  net/
    __init__.py
    messages.py
    tracker.py
    peer.py
```

Both `blockchain/` and `net/` are real Python packages (have
`__init__.py`). All cross-package imports use absolute paths
(`from blockchain.tx import Transaction`). Intra-package imports use
relative form (`from .tx import Transaction`).

The three pre-existing `merkle_*.py` files were moved into `blockchain/`
without behavior changes, except their `from merkle_utils import ...`
lines were guarded:

```python
try:
    from .merkle_utils import MerkleProof, hash_internal_node, hash_leaf
except ImportError:
    from merkle_utils import MerkleProof, hash_internal_node, hash_leaf
```

so they keep working both as `python -m blockchain.merkle_prover` and
as a direct script.

---

## 3. `blockchain/` package

### 3.1 `block.py`

Public surface:

- `GENESIS_PREV_HASH: bytes` — 32 zero bytes.
- `@dataclass class Block`:
  - `index: int`
  - `prev_hash: bytes` (32 bytes)
  - `timestamp: float`
  - `nonce: int`
  - `txs: list[Transaction]` (default empty)
  - `header_bytes() -> bytes`
  - `hash() -> bytes` — `sha256(header_bytes())`
  - `to_dict() -> dict` / `from_dict(d) -> Block`
  - `to_json() -> str` / `from_json(s) -> Block`
- `genesis_block() -> Block` — deterministic block 0:
  `Block(index=0, prev_hash=GENESIS_PREV_HASH, timestamp=0.0, nonce=0, txs=[])`.

Canonical header serialization (must be byte-stable across peers):

```text
header_bytes =
      index            as 8-byte big-endian unsigned int
   || prev_hash        raw 32 bytes
   || str(timestamp)   UTF-8 encoded
   || nonce            as 8-byte big-endian unsigned int
   || tx_commitment    concat of each tx.hash() (32 bytes each)
```

`to_dict` JSON shape:

```json
{
  "index": 0,
  "prev_hash": "<hex>",
  "timestamp": 0.0,
  "nonce": 0,
  "txs": [ <tx-dict>, ... ],
  "hash": "<hex>"          // computed; ignored on from_dict
}
```

Note: `from_dict` does NOT verify the `hash` field; PoW verification is
the chain's job.

### 3.2 `pow.py`

Public surface:

- `DEFAULT_DIFFICULTY_BITS = 4`
- `meets_difficulty(block_hash: bytes, difficulty_bits: int) -> bool`
- `mine(block: Block, difficulty_bits: int = 4, max_iters: int | None = None) -> Block`

Difficulty is in **leading zero bits** of the SHA-256 digest, not bytes.
Implementation:

1. `full_zero_bytes, remainder = divmod(difficulty_bits, 8)`.
2. All bytes in `block_hash[:full_zero_bytes]` must be `0x00`.
3. If `remainder > 0`, the next byte ANDed with `0xFF << (8 - remainder)`
   must be `0`.
4. `difficulty_bits <= 0` short-circuits to `True`.

`mine()` sets `block.timestamp = time.time()`, then increments
`block.nonce` until `meets_difficulty(block.hash(), difficulty_bits)`.
Raises `RuntimeError` if `max_iters` is given and exceeded.

### 3.3 `wallet.py`

Public surface:

- `CURVE = ec.SECP256R1()`
- `HASH_ALG = hashes.SHA256()`
- `@dataclass class Wallet(private_key: ec.EllipticCurvePrivateKey)`:
  - `Wallet.generate() -> Wallet`
  - `public_key` property
  - `pubkey_bytes() -> bytes` — DER `SubjectPublicKeyInfo` encoding.
  - `address() -> str` — `pubkey_bytes().hex()`. This is the on-chain identity.
  - `sign(message: bytes) -> bytes` — ECDSA(SHA-256) DER signature.
- `load_pubkey(pubkey_bytes) -> EllipticCurvePublicKey`
- `verify(pubkey_bytes: bytes, message: bytes, signature: bytes) -> bool`
  Returns `False` on `InvalidSignature` *or* on any other exception
  (e.g. malformed key); never raises.

The "address" is the full DER SPKI hex. We deliberately did NOT introduce
a hashed-pubkey-style address; if we add one, do it in this module.

### 3.4 `tx.py`

Public surface:

- `@dataclass class Transaction`:
  - `sender: str` (hex DER pubkey)
  - `recipient: str` (hex DER pubkey)
  - `amount: int`
  - `nonce: int` (per-sender monotonic)
  - `signature: str | None` (hex; populated by `sign()`)
  - `signing_payload() -> bytes` — sorted-key JSON of
    `{sender, recipient, amount, nonce}`. Excludes `signature` so the
    signature can commit to a stable byte string.
  - `hash() -> bytes` — `sha256(signing_payload || sig_bytes)`. The
    signature *is* part of the tx hash to prevent malleability collisions
    in the mempool dedup cache.
  - `sign(w: Wallet) -> None` — asserts `w.address() == self.sender`,
    sets `self.signature`.
  - `verify_signature() -> bool`
  - `to_dict()` / `from_dict()`

### 3.5 `chain.py`

Public surface:

- `class ValidationError(Exception)`
- `@dataclass class Chain`:
  - `blocks: list[Block]` (defaults to `[genesis_block()]`)
  - `difficulty_bits: int = DEFAULT_DIFFICULTY_BITS`
  - `balances: dict[str, int]`
  - `nonces: dict[str, int]`
  - `tip` (property), `height` (property; `len(blocks) - 1`)
  - `validate_tx(tx, *, applied_now=False) -> None`
  - `validate_block(block) -> None`
  - `append(block) -> None`
  - `replace_if_longer(candidate: Iterable[Block]) -> bool`
  - `next_block_template(miner_address, pending=None) -> Block`

Status: this module is a **structured stub**. It contains the method
shapes and partial logic, but the following raise `NotImplementedError`
or contain `TODO`s:

- `validate_tx`: signature check is implemented; balance and nonce
  checks are TODO.
- `validate_block`: `prev_hash` and difficulty checks are implemented;
  per-tx validation and applying tx effects are TODO.
- `append`: applies block but does not yet update `balances` / `nonces`.
- `replace_if_longer`: not implemented.

Invariants the implementation must maintain:

- `blocks[0]` is always the deterministic genesis from `genesis_block()`.
- For all `i > 0`: `blocks[i].prev_hash == blocks[i-1].hash()`.
- For all `i`: `meets_difficulty(blocks[i].hash(), difficulty_bits)` is
  True (the genesis is allowed to violate this — it's never re-checked).
- `balances[a] >= 0` for all `a`.
- `nonces[a]` is the highest nonce successfully included for sender `a`;
  next acceptable tx from `a` has `nonce == nonces[a] + 1`.

---

## 4. `net/` package

### 4.1 Wire framing (`messages.py`)

**Frame:** length-prefixed JSON.

```text
+-------------------+----------------------+
| 4 bytes BE uint32 |   N bytes UTF-8 JSON |
+-------------------+----------------------+
        N
```

- `LENGTH_PREFIX = struct.Struct(">I")` (4 bytes, big-endian).
- `MAX_FRAME_BYTES = 8 * 1024 * 1024` — frames over 8 MiB are rejected
  with `ValueError`.

**Envelope:** every frame's JSON body is

```json
{ "type": "<TYPE>", "payload": { ... } }
```

`type` strings are exact uppercase tokens defined as module constants.

Public surface:

- Constants: `REGISTER`, `UNREGISTER`, `HEARTBEAT`, `PEER_LIST`,
  `NEW_TX`, `NEW_BLOCK`, `GET_CHAIN`, `CHAIN`.
- `@dataclass class Message(type: str, payload: dict)`
  - `to_bytes() -> bytes`
  - `Message.from_json_bytes(body: bytes) -> Message`
- `async send(writer, msg) -> None` — writes one frame and `await drain()`.
- `async recv(reader) -> Message` — reads exactly one frame; relies on
  `StreamReader.readexactly`, so a clean half-close raises
  `IncompleteReadError`.

### 4.2 Message catalog

Tracker ↔ Peer:

| Type        | Direction        | Payload                          |
|-------------|------------------|----------------------------------|
| `REGISTER`  | peer → tracker   | `{"addr": "host:port"}`          |
| `HEARTBEAT` | peer → tracker   | `{"addr": "host:port"}`          |
| `UNREGISTER`| peer → tracker   | `{"addr": "host:port"}`          |
| `PEER_LIST` | tracker → peer   | `{"peers": ["host:port", ...]}`  |

Peer ↔ Peer:

| Type        | Direction        | Payload                                  |
|-------------|------------------|------------------------------------------|
| `NEW_TX`    | peer → peer      | `{"tx": <Transaction.to_dict()>}`        |
| `NEW_BLOCK` | peer → peer      | `{"block": <Block.to_dict()>}`           |
| `GET_CHAIN` | peer → peer      | `{}`                                     |
| `CHAIN`     | peer → peer      | `{"blocks": [<Block.to_dict()>, ...]}`   |

`addr` is always the listener address (`"host:port"`) the peer is bound
to for inbound peer-to-peer traffic, NOT the ephemeral source port of
the tracker connection.

### 4.3 `tracker.py`

Constants:

- `HEARTBEAT_TIMEOUT_S = 15.0` — peers idle for this long get evicted.
- `SWEEP_INTERVAL_S = 5.0`.

Class `Tracker(host, port)`:

- State:
  - `peers: dict[str, PeerInfo]` — keyed by `addr`.
  - `_writers: dict[str, StreamWriter]` — open connection back to each peer
    (used to push `PEER_LIST`).
  - `_lock: asyncio.Lock` guards both.
- `PeerInfo(addr, last_seen)` — `last_seen` defaults to `time.time()`.
- `async serve()` runs `asyncio.start_server(self._handle_client, ...)`
  concurrently with `_sweep_loop()`.
- `_handle_client(reader, writer)` reads frames until EOF; on the first
  message that carries an `addr` we remember it and on disconnect we
  call `_drop(addr)`.
- `_dispatch(msg, writer)` routes by `msg.type`; unknown types are
  logged at WARNING and ignored.
- `_register(addr, writer)`: store peer, store writer, then
  `_broadcast_peer_list()`.
- `_drop(addr)`: remove from both maps, then `_broadcast_peer_list()`.
- `_touch(addr)`: bump `last_seen`.
- `_broadcast_peer_list()`: send `PEER_LIST` (sorted addrs) to every
  writer in `_writers`. Best-effort: failures are logged, not retried
  (TODO: drop those peers).
- `_sweep_loop()`: every `SWEEP_INTERVAL_S` seconds, evict every peer
  with `last_seen < now - HEARTBEAT_TIMEOUT_S`.

`__main__` entry point:

```bash
python -m net.tracker [--host 127.0.0.1] [--port 9000]
```

### 4.4 `peer.py`

Constants:

- `HEARTBEAT_INTERVAL_S = 5.0` (note: must be < tracker's `HEARTBEAT_TIMEOUT_S`).
- `SEEN_CACHE_LIMIT = 4096` (defined but not yet enforced).

Class `Peer(listen_host, listen_port, tracker_host, tracker_port)`:

- State:
  - `chain: Chain` — defaults to a fresh `Chain()` (genesis only).
  - `peers: set[str]` — other peers' `host:port` strings (from `PEER_LIST`,
    self excluded).
  - `seen_hashes: set[str]` — hex hashes of dedup-tracked txs and blocks.
  - `_tracker_writer: StreamWriter | None`.
- `addr` property = `f"{listen_host}:{listen_port}"`.
- `async run()`:
  1. `start_server(self._handle_peer_conn, listen_host, listen_port)`.
  2. `gather(server.serve_forever(), self._tracker_session())`. A
     `_mining_loop()` is intentionally absent — it is the next TODO.
- `_tracker_session()`:
  1. Open TCP connection to tracker.
  2. Send `REGISTER {addr: self.addr}`.
  3. Concurrently run a heartbeat loop (`HEARTBEAT` every
     `HEARTBEAT_INTERVAL_S`) and a reader loop that consumes
     `PEER_LIST` frames.
  4. On exit, best-effort send `UNREGISTER` and close.
- `_on_peer_list(addrs)` updates `self.peers`, excluding `self.addr`,
  and logs the diff.
- `_handle_peer_conn(reader, writer)`: read frames in a loop, dispatch.
- `_dispatch_peer_msg(msg, writer)`:
  - `NEW_TX` → `_on_new_tx(Transaction.from_dict(...))`
  - `NEW_BLOCK` → `_on_new_block(Block.from_dict(...))`
  - `GET_CHAIN` → reply on the same `writer` with a `CHAIN` frame.
  - `CHAIN` → `_on_chain(blocks)`.
- Dedup: `_on_new_tx` and `_on_new_block` skip if hash hex is already
  in `seen_hashes`, otherwise add it and re-flood. Validation /
  mempool / chain integration are TODOs.
- `_flood(msg)`: opens a fresh connection per peer in `self.peers`,
  sends one frame, closes. Best-effort, no retries.

`__main__` entry point:

```bash
python -m net.peer --port <P> [--host 127.0.0.1] [--tracker-host ...] [--tracker-port 9000]
```

---

## 5. Concurrency model

- Single asyncio event loop per process.
- Tracker: one server task + one sweep task. State protected by a
  single `asyncio.Lock`.
- Peer: one server task + one tracker-session task (hearbeat + reader
  subtasks `gather`'d together). Outbound flooding opens one short-lived
  connection per (peer, message) pair; not optimized.
- No threads. No multiprocessing. CPU-bound mining will block the loop;
  when `_mining_loop` is added it must run nonces in chunks via
  `await asyncio.sleep(0)` or be offloaded to an executor.

---

## 6. Status matrix

Implemented and behaviorally complete (modulo bugs not yet caught by
tests):

- `block.py`, `pow.py`, `wallet.py`, `tx.py`.
- `net/messages.py` framing and envelope.
- Tracker membership: REGISTER / HEARTBEAT / UNREGISTER, sweep
  eviction, PEER_LIST broadcast.
- Peer ↔ tracker session: register, heartbeat, PEER_LIST consumption.
- Peer ↔ peer dispatch and flooding plumbing.
- `GET_CHAIN` → `CHAIN` reply.

Stubbed (raise `NotImplementedError` or marked `TODO:`):

- `Chain.validate_tx` — balance & nonce.
- `Chain.validate_block` — per-tx validation, apply effects.
- `Chain.append` — apply tx effects to balances/nonces.
- `Chain.replace_if_longer` — fork resolution (Design.md §4).
- `Peer._on_new_tx` — call `validate_tx`, mempool insert, flood.
- `Peer._on_new_block` — call `chain.append`; on `prev_hash` mismatch,
  send `GET_CHAIN` to the sender; on `CHAIN` reply call
  `replace_if_longer`.
- `Peer._on_chain` — call `replace_if_longer`.
- `Peer._mining_loop` — not yet started in `run()`.
- `_broadcast_peer_list` should drop peers whose write fails (currently
  just logs).

No tests, no `requirements.txt`, no app code, no scripts directory.

---

## 7. Conventions / invariants

1. All hashes are SHA-256, 32 raw bytes; serialized to hex when crossing
   a JSON boundary.
2. Peer / sender identity is the hex-encoded DER SPKI of the public key.
   Treat this string as opaque elsewhere.
3. JSON for hashing or signing MUST be produced with
   `json.dumps(..., sort_keys=True)`. Anything that ends up inside a
   hash must be byte-stable.
4. The block hash commits to the tx commitment (concatenated tx
   hashes today; replace with a Merkle root for §7 extra credit). Any
   change to the header layout requires updating `Block.header_bytes`
   and incrementing... well, it's a class project, just keep peers in
   lockstep.
5. Tracker addresses use `host:port` strings throughout; never split-
   then-rejoin in user-facing logs.
6. All network code is asyncio. Do not introduce blocking I/O in
   `net/` without offloading via `loop.run_in_executor`.
7. New message types require: a constant in `messages.py`, a row in the
   §4.2 table here, and a branch in `Peer._dispatch_peer_msg` (or the
   tracker's `_dispatch`).

---

## 8. Reproduction recipe (for another LLM)

To recreate this scaffold deterministically:

1. Create the directories in §2 and the two `__init__.py` files with
   one-line docstrings.
2. Write the five `blockchain/` modules per §3 with the exact public
   surfaces and serialization rules listed.
3. Move (don't rewrite) the three `merkle_*.py` files into
   `blockchain/` and apply the import guard in §2.
4. Write `net/messages.py` per §4.1 and §4.2.
5. Write `net/tracker.py` per §4.3 with constants exactly as given.
6. Write `net/peer.py` per §4.4. The `_mining_loop` is intentionally
   omitted; do NOT silently invent one.
7. Leave the items in §6 "Stubbed" as `NotImplementedError` or `TODO:`
   comments. Do not improvise behavior for them; the human team owns
   those decisions.
