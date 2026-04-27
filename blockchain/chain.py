"""Chain state: validation, extension, fork resolution.

This is mostly a structured stub. Each method below is the seam where a
specific piece of Design.md §4 lives; fill in as the protocol stabilizes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from .block import Block, genesis_block
from .pow import meets_difficulty, DEFAULT_DIFFICULTY_BITS
from .tx import Transaction


class ValidationError(Exception):
    """Raised when a block or tx fails validation."""


@dataclass
class Chain:
    blocks: List[Block] = field(default_factory=lambda: [genesis_block()])
    difficulty_bits: int = DEFAULT_DIFFICULTY_BITS
    balances: Dict[str, int] = field(default_factory=dict)
    nonces: Dict[str, int] = field(default_factory=dict)

    @property
    def tip(self) -> Block:
        return self.blocks[-1]

    @property
    def height(self) -> int:
        return len(self.blocks) - 1

    # ---- validation ----------------------------------------------------

    def validate_tx(self, tx: Transaction, *, applied_now: bool = False) -> None:
        """Design.md §5 validity rules. Raises ValidationError on failure.

        TODO: signature check, balance check, monotonic nonce check.
        """
        if not tx.verify_signature():
            raise ValidationError("bad signature")
        # TODO: balance & nonce checks against self.balances / self.nonces
        raise NotImplementedError

    def validate_block(self, block: Block) -> None:
        """Design.md §4 verification: prev_hash, recompute, difficulty, txs.

        TODO: implement the four checks.
        """
        if block.prev_hash != self.tip.hash():
            raise ValidationError("prev_hash does not match tip")
        if not meets_difficulty(block.hash(), self.difficulty_bits):
            raise ValidationError("hash does not meet difficulty")
        # TODO: validate every tx via validate_tx
        raise NotImplementedError

    # ---- mutation ------------------------------------------------------

    def append(self, block: Block) -> None:
        """Validate and append. Updates balances/nonces."""
        self.validate_block(block)
        self.blocks.append(block)
        # TODO: apply tx effects to self.balances / self.nonces

    def replace_if_longer(self, candidate: Iterable[Block]) -> bool:
        """Design.md §4 fork rule: longest chain wins.

        Re-validate the entire candidate chain from genesis; only swap in if
        it validates AND is strictly longer than ours.
        Returns True if we replaced, False otherwise.

        TODO: implement.
        """
        raise NotImplementedError

    # ---- mempool helpers (optional, useful for §5 demo) ---------------

    def next_block_template(self, miner_address: str,
                             pending: Optional[List[Transaction]] = None) -> Block:
        """Build an unmined block on top of the current tip."""
        return Block(
            index=self.height + 1,
            prev_hash=self.tip.hash(),
            timestamp=0.0,
            nonce=0,
            txs=list(pending or []),
        )
