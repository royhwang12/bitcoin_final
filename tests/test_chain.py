import unittest

from blockchain.chain import Chain, ValidationError
from blockchain.tx import Transaction
from blockchain.wallet import Wallet


class ChainValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.chain = Chain(difficulty_bits=0)
        self.alice = Wallet.generate()
        self.bob = Wallet.generate()
        self.carol = Wallet.generate()

        self.alice_addr = self.alice.address()
        self.bob_addr = self.bob.address()
        self.carol_addr = self.carol.address()

    def _signed_tx(self, sender_wallet: Wallet, recipient: str, amount: int, nonce: int) -> Transaction:
        tx = Transaction(
            sender=sender_wallet.address(),
            recipient=recipient,
            amount=amount,
            nonce=nonce,
        )
        tx.sign(sender_wallet)
        return tx

    def test_append_valid_block_updates_balances_and_nonces(self) -> None:
        self.chain.balances[self.alice_addr] = 100

        tx1 = self._signed_tx(self.alice, self.bob_addr, 30, 1)
        tx2 = self._signed_tx(self.alice, self.carol_addr, 20, 2)
        block = self.chain.next_block_template(miner_address=self.alice_addr, pending=[tx1, tx2])

        self.chain.append(block)

        self.assertEqual(self.chain.height, 1)
        self.assertEqual(self.chain.balances[self.alice_addr], 50)
        self.assertEqual(self.chain.balances[self.bob_addr], 30)
        self.assertEqual(self.chain.balances[self.carol_addr], 20)
        self.assertEqual(self.chain.nonces[self.alice_addr], 2)

    def test_validate_block_rejects_duplicate_transaction_hashes(self) -> None:
        self.chain.balances[self.alice_addr] = 100
        tx = self._signed_tx(self.alice, self.bob_addr, 10, 1)
        block = self.chain.next_block_template(miner_address=self.alice_addr, pending=[tx, tx])

        with self.assertRaises(ValidationError):
            self.chain.validate_block(block)

    def test_validate_block_rejects_bad_index(self) -> None:
        self.chain.balances[self.alice_addr] = 100
        tx = self._signed_tx(self.alice, self.bob_addr, 10, 1)
        block = self.chain.next_block_template(miner_address=self.alice_addr, pending=[tx])
        block.index = 99

        with self.assertRaises(ValidationError):
            self.chain.validate_block(block)

    def test_validate_block_rejects_in_block_overspend(self) -> None:
        self.chain.balances[self.alice_addr] = 50
        tx1 = self._signed_tx(self.alice, self.bob_addr, 40, 1)
        tx2 = self._signed_tx(self.alice, self.carol_addr, 20, 2)
        block = self.chain.next_block_template(miner_address=self.alice_addr, pending=[tx1, tx2])

        with self.assertRaises(ValidationError):
            self.chain.validate_block(block)


if __name__ == "__main__":
    unittest.main()
