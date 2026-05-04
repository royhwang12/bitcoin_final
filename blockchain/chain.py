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

    def __post_init__(self) -> None:
        self._seed_balances: Dict[str, int] = dict(self.balances)
        self._seed_nonces: Dict[str, int] = dict(self.nonces)

    @property
    def tip(self) -> Block:
        return self.blocks[-1]

    @property
    def height(self) -> int:
        return len(self.blocks) - 1

    def validate_tx(self, tx: Transaction, *, applied_now: bool = False) -> None:
        """Design.md §5 validity rules. Raises ValidationError on failure.

        TODO: signature check, balance check, monotonic nonce check.
        """
        if not tx.verify_signature():
            raise ValidationError("bad signature")
        if tx.amount <= 0:
            raise ValidationError("amount must be positive")

        sender_balance = self.balances.get(tx.sender, 0)
        if sender_balance < tx.amount:
            raise ValidationError("insufficient balance")

        expected_nonce = self.nonces.get(tx.sender, 0) + 1
        if tx.nonce != expected_nonce:
            raise ValidationError(
                f"bad nonce: expected {expected_nonce}, got {tx.nonce}"
            )

    def validate_block(self, block: Block) -> None:
        """Design.md §4 verification: prev_hash, recompute, difficulty, txs.

        TODO: implement the four checks.
        """
        if block.index != self.height + 1:
            raise ValidationError("bad block index")
        if block.prev_hash != self.tip.hash():
            raise ValidationError("prev_hash does not match tip")
        if not meets_difficulty(block.hash(), self.difficulty_bits):
            raise ValidationError("hash does not meet difficulty")

        tmp_balances = dict(self.balances)
        tmp_nonces = dict(self.nonces)
        seen_tx_hashes: set[str] = set()

        for tx in block.txs:
            txh = tx.hash().hex()
            if txh in seen_tx_hashes:
                raise ValidationError("duplicate tx in block")
            seen_tx_hashes.add(txh)

            if not tx.verify_signature():
                raise ValidationError("bad signature")
            if tx.amount <= 0:
                raise ValidationError("amount must be positive")

            sender_balance = tmp_balances.get(tx.sender, 0)
            if sender_balance < tx.amount:
                raise ValidationError("insufficient balance")

            expected_nonce = tmp_nonces.get(tx.sender, 0) + 1
            if tx.nonce != expected_nonce:
                raise ValidationError(
                    f"bad nonce: expected {expected_nonce}, got {tx.nonce}"
                )

            tmp_balances[tx.sender] = sender_balance - tx.amount
            tmp_balances[tx.recipient] = tmp_balances.get(tx.recipient, 0) + tx.amount
            tmp_nonces[tx.sender] = tx.nonce

    def append(self, block: Block) -> None:
        """Validate and append. Updates balances/nonces."""
        self.validate_block(block)

        for tx in block.txs:
            self.balances[tx.sender] = self.balances.get(tx.sender, 0) - tx.amount
            self.balances[tx.recipient] = self.balances.get(tx.recipient, 0) + tx.amount
            self.nonces[tx.sender] = tx.nonce

        self.blocks.append(block)

    def replace_if_longer(self, candidate: Iterable[Block]) -> bool:
        """Design.md §4 fork rule: longest chain wins.

        Re-validate the entire candidate chain from genesis; only swap in if
        it validates AND is strictly longer than ours.
        Returns True if we replaced, False otherwise.
        """
        candidate_blocks = list(candidate)
        if len(candidate_blocks) <= len(self.blocks):
            return False
        if not candidate_blocks:
            return False
        if candidate_blocks[0].hash() != self.blocks[0].hash():
            return False

        temp = Chain(
            blocks=[candidate_blocks[0]],
            difficulty_bits=self.difficulty_bits,
            balances=dict(self._seed_balances),
            nonces=dict(self._seed_nonces),
        )
        try:
            for block in candidate_blocks[1:]:
                temp.append(block)
        except ValidationError:
            return False

        self.blocks = temp.blocks
        self.balances = temp.balances
        self.nonces = temp.nonces
        return True


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
