"""ECDSA keypair wrapper used by transactions.

Uses the `cryptography` package (SECP256R1 / NIST P-256). Add to requirements:
    cryptography>=42

A "pubkey" on the wire is the DER-encoded SubjectPublicKeyInfo, hex-encoded.
"""

from __future__ import annotations

from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.exceptions import InvalidSignature


CURVE = ec.SECP256R1()
HASH_ALG = hashes.SHA256()


@dataclass
class Wallet:
    private_key: ec.EllipticCurvePrivateKey

    @classmethod
    def generate(cls) -> "Wallet":
        return cls(private_key=ec.generate_private_key(CURVE))

    @property
    def public_key(self) -> ec.EllipticCurvePublicKey:
        return self.private_key.public_key()

    def pubkey_bytes(self) -> bytes:
        return self.public_key.public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def address(self) -> str:
        """Hex-encoded public key. Used as the on-chain identity."""
        return self.pubkey_bytes().hex()

    def sign(self, message: bytes) -> bytes:
        return self.private_key.sign(message, ec.ECDSA(HASH_ALG))


def load_pubkey(pubkey_bytes: bytes) -> ec.EllipticCurvePublicKey:
    return serialization.load_der_public_key(pubkey_bytes)


def verify(pubkey_bytes: bytes, message: bytes, signature: bytes) -> bool:
    try:
        load_pubkey(pubkey_bytes).verify(signature, message, ec.ECDSA(HASH_ALG))
        return True
    except InvalidSignature:
        return False
    except Exception:
        return False
