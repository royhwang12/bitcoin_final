## bitcoin_final

Computer Networks final project: a small peer-to-peer proof-of-work blockchain with a local dashboard.

## What this project includes

- A blockchain core (`blockchain/`) with blocks, transactions, wallets, proof-of-work, Merkle utilities, and chain validation.
- A networking layer (`net/`) with an asyncio tracker + peers for gossiping transactions and blocks.
- A dashboard (`dashboard/`) built with FastAPI + plain HTML/JS to run a local multi-peer demo.

## High-level architecture

The system is split into two main layers:

- `blockchain/`: deterministic blockchain logic (block format, transaction signatures, PoW checks, chain validation, fork-choice).
- `net/`: asynchronous peer-to-peer communication (tracker-based discovery, message framing, transaction/block broadcast, chain sync).

At runtime, peers maintain their own local `Chain`, exchange state over the wire protocol in `net/messages.py`, and converge using longest-valid-chain selection.

### Protocol flow (conceptual)

1. Peer registers with tracker and receives known peers.
2. Peer gossips transactions (`NEW_TX`) and blocks (`NEW_BLOCK`) to neighbors.
3. Miners build candidate blocks from pending txs and run PoW.
4. Receiving peers validate signatures, balances/nonces, linkage, and difficulty before appending.
5. On fork/conflict, peers adopt a strictly longer valid chain.

### Core validation and consensus rules

- The chain starts from a deterministic genesis block.
- Every non-genesis block must reference the exact hash of the previous block.
- Difficulty is measured in leading zero bits of SHA-256 block hashes.
- Transactions are ECDSA-signed (`SECP256R1` + `SHA-256`) and invalid signatures are rejected.
- Sender nonces must increase sequentially (`next nonce = last nonce + 1`).
- Transactions cannot overspend balances, including cumulative in-block spending.
- Fork-choice is strict longest-chain (`>`), not equal-length replacement.

## Repository structure

```text
bitcoin_final/
  README.md
  Architecture.md
  Design.md
  TESTING.md
  pyproject.toml
  uv.lock

  blockchain/
    __init__.py
    block.py            # Block model + genesis helper
    chain.py            # Chain state, validation, append, fork-choice
    merkle_utils.py     # Merkle utilities (Didn't integrate with working demo)
    pow.py              # Difficulty check + miner
    tx.py               # Transaction model + signatures
    wallet.py           # ECDSA wallet utilities

  net/
    __init__.py
    messages.py         # Wire message encoding/framing
    peer.py             # Peer node runtime
    tracker.py          # Tracker service for peer discovery

  dashboard/
    __init__.py
    app.py              # FastAPI entrypoint (blockchain-dashboard)
    orchestrator.py     # Dashboard control plane for tracker/peers/wallets
    static/
      index.html        # Browser UI

  tests/
    test_chain.py
    integration_demo.py
```

## Requirements

- Python `3.10+`
- [`uv`](https://docs.astral.sh/uv/)

## Setup (install dependencies)

```bash
uv sync
```

This creates/updates the project virtual environment and installs runtime dependencies from `pyproject.toml` and lockfile constraints from `uv.lock`.

## Build / compilation

This project is Python-only, so there is no separate compile step. The equivalent setup step is dependency resolution + environment creation:

```bash
uv sync
```

If you need a distributable wheel/sdist, you can use standard Python packaging tools (setuptools backend configured in `pyproject.toml`).

## Usage

### Run the dashboard app

```bash
uv run blockchain-dashboard
```

Then open:

- `http://127.0.0.1:8000`

From the UI you can:

- Start the tracker
- Create wallets (with optional initial balances)
- Create peers (optionally start mining)
- Submit transactions
- Watch live chain/mempool/balance state updates

### Run tests

```bash
# Unit tests
python3 tests/test_chain.py

# End-to-end integration demo (tracker + 3 peers)
python3 tests/integration_demo.py
```

See `TESTING.md` for the full checkpoint list and expected outcomes.

## Script entrypoint

`pyproject.toml` defines:

- `blockchain-dashboard = "dashboard.app:main"`

So `uv run blockchain-dashboard` launches Uvicorn on `127.0.0.1:8000`.

## Additional documentation

- `Design.md`: higher-level design context
- `TESTING.md`: test strategy and observed results
