"""Proof-of-Work: mining loop and difficulty checks.

Difficulty is expressed as a number of leading zero *bits* in SHA-256(header).
Per Design.md we start with N=4.
"""

from __future__ import annotations

import time

from .block import Block


DEFAULT_DIFFICULTY_BITS = 4


def meets_difficulty(block_hash: bytes, difficulty_bits: int) -> bool:
    """True if `block_hash` has at least `difficulty_bits` leading zero bits."""
    if difficulty_bits <= 0:
        return True
    full_zero_bytes, remainder = divmod(difficulty_bits, 8)
    if len(block_hash) < full_zero_bytes + (1 if remainder else 0):
        return False
    if any(b != 0 for b in block_hash[:full_zero_bytes]):
        return False
    if remainder:
        next_byte = block_hash[full_zero_bytes]
        mask = 0xFF << (8 - remainder) & 0xFF
        if next_byte & mask:
            return False
    return True


def mine(block: Block, difficulty_bits: int = DEFAULT_DIFFICULTY_BITS,
         max_iters: int | None = None) -> Block:
    """Mutates `block.nonce` (and refreshes timestamp) until PoW is satisfied.

    Returns the same block for convenience. Raises RuntimeError if `max_iters`
    is given and exceeded.
    """
    block.timestamp = time.time()
    iters = 0
    while not meets_difficulty(block.hash(), difficulty_bits):
        block.nonce += 1
        iters += 1
        if max_iters is not None and iters >= max_iters:
            raise RuntimeError(f"mining exceeded max_iters={max_iters}")
    return block
