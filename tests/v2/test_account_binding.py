from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from v2.exceptions import ConfigurationError
from v2.profiles import (
    load_profile,
    verify_or_bind_account,
)
from tests.v2.support import copy_v2_config


class AccountBindingTests(unittest.TestCase):
    def test_explicit_first_bind_and_mismatch(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            copy_v2_config(root)
            profile = load_profile(
                "paper1",
                project_root=root,
            )
            with self.assertRaises(
                ConfigurationError
            ) as required:
                verify_or_bind_account(
                    profile,
                    "account-a",
                    project_root=root,
                )
            self.assertEqual(
                required.exception.code,
                "ACCOUNT_BINDING_REQUIRED",
            )
            self.assertNotIn(
                "account-a",
                str(required.exception.details),
            )
            binding = verify_or_bind_account(
                profile,
                "account-a",
                bind_account=True,
                project_root=root,
            )
            self.assertEqual(
                len(binding["account_id_hash"]),
                64,
            )
            with self.assertRaises(
                ConfigurationError
            ) as mismatch:
                verify_or_bind_account(
                    profile,
                    "account-b",
                    project_root=root,
                )
            self.assertEqual(
                mismatch.exception.code,
                "ACCOUNT_BINDING_MISMATCH",
            )


if __name__ == "__main__":
    unittest.main()
