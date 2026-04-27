"""Transaction dataclass with ECDSA sign/verify.

Tx format (pending §5 of Design.md): coin transfer with replay protection via
a per-sender monotonically increasing nonce.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Optional

from . import wallet as _wallet


@dataclass
class Transaction:
    sender: str       # hex-encoded DER pubkey of sender (see Wallet.address)
    recipient: str    # hex-encoded DER pubkey of recipient
    amount: int
    nonce: int        # per-sender monotonically increasing
    signature: Optional[str] = None  # hex-encoded ECDSA signature, set by sign()

    def signing_payload(self) -> bytes:
        """Canonical bytes signed by the sender. MUST exclude the signature."""
        payload = {
            "sender": self.sender,
            "recipient": self.recipient,
            "amount": self.amount,
            "nonce": self.nonce,
        }
        return json.dumps(payload, sort_keys=True).encode()

    def hash(self) -> bytes:
        h = hashlib.sha256()
        h.update(self.signing_payload())
        if self.signature:
            h.update(bytes.fromhex(self.signature))
        return h.digest()

    def sign(self, w: "_wallet.Wallet") -> None:
        if w.address() != self.sender:
            raise ValueError("wallet address does not match tx.sender")
        sig = w.sign(self.signing_payload())
        self.signature = sig.hex()

    def verify_signature(self) -> bool:
        if not self.signature:
            return False
        return _wallet.verify(
            bytes.fromhex(self.sender),
            self.signing_payload(),
            bytes.fromhex(self.signature),
        )

    def to_dict(self) -> dict:
        return {
            "sender": self.sender,
            "recipient": self.recipient,
            "amount": self.amount,
            "nonce": self.nonce,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Transaction":
        return cls(**d)
