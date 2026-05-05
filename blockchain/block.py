"""Block dataclass and header hashing.

A Block's header is the canonical bytes that PoW hashes over. Anything that
must be committed to by the hash must live in `header_bytes()`.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field, asdict
from typing import List

from .tx import Transaction


GENESIS_PREV_HASH = b"\x00" * 32


@dataclass
class Block:
    index: int
    prev_hash: bytes
    timestamp: float
    nonce: int
    txs: List[Transaction] = field(default_factory=list)
    _tx_commitment_cache: bytes | None = field(
        default=None, repr=False, compare=False,
    )

    def _tx_commitment(self) -> bytes:
        if self._tx_commitment_cache is None:
            self._tx_commitment_cache = b"".join(t.hash() for t in self.txs)
        return self._tx_commitment_cache

    def invalidate_tx_cache(self) -> None:
        """Call after mutating `txs` to force the commitment to be recomputed."""
        self._tx_commitment_cache = None

    def header_bytes(self) -> bytes:
        """Canonical byte serialization of the header fields hashed by PoW.

        Includes a commitment to txs via their concatenated hashes (swap this
        for a Merkle root once `merkle_utils` is wired in). The tx portion is
        cached because it's invariant during mining (only `nonce` changes).
        """
        return (
            self.index.to_bytes(8, "big")
            + self.prev_hash
            + str(self.timestamp).encode()
            + self.nonce.to_bytes(8, "big")
            + self._tx_commitment()
        )

    def hash(self) -> bytes:
        return hashlib.sha256(self.header_bytes()).digest()

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "prev_hash": self.prev_hash.hex(),
            "timestamp": self.timestamp,
            "nonce": self.nonce,
            "txs": [t.to_dict() for t in self.txs],
            "hash": self.hash().hex(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Block":
        return cls(
            index=d["index"],
            prev_hash=bytes.fromhex(d["prev_hash"]),
            timestamp=d["timestamp"],
            nonce=d["nonce"],
            txs=[Transaction.from_dict(t) for t in d["txs"]],
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> "Block":
        return cls.from_dict(json.loads(s))


def genesis_block() -> Block:
    """Deterministic genesis so every peer agrees on block 0."""
    return Block(
        index=0,
        prev_hash=GENESIS_PREV_HASH,
        timestamp=0.0,
        nonce=0,
        txs=[],
    )
