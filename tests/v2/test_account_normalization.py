from __future__ import annotations

import unittest

from v2.data.account import normalize_account
from v2.exceptions import StateValidationError
from tests.v2.fakes import fake_account


class AccountNormalizationTests(unittest.TestCase):
    def test_account_id_is_hashed(self) -> None:
        raw = fake_account(id="sensitive-id")
        normalized = normalize_account(raw)
        self.assertNotEqual(
            normalized["account_id_hash"],
            "sensitive-id",
        )
        self.assertEqual(
            len(normalized["account_id_hash"]),
            64,
        )
        self.assertNotIn(
            "sensitive-id",
            repr(normalized),
        )
        self.assertEqual(
            normalized["cash"],
            10000.5,
        )

    def test_missing_critical_numeric_field_fails(
        self,
    ) -> None:
        with self.assertRaises(
            StateValidationError
        ):
            normalize_account(
                fake_account(cash=None)
            )


if __name__ == "__main__":
    unittest.main()
