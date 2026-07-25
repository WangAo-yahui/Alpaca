from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from v2.exceptions import ConfigurationError
from v2.profiles import load_profile
from tests.v2.support import copy_v2_config


class ProfileLoadingTests(unittest.TestCase):
    def test_profile_env_names_are_not_secrets(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            copy_v2_config(root)
            profile = load_profile(
                "paper1",
                project_root=root,
            )
            self.assertEqual(
                profile.credential_key_env,
                "ALPACA_API_KEY",
            )
            self.assertEqual(
                profile.credential_secret_env,
                "ALPACA_SECRET_KEY",
            )

    def test_missing_and_disabled_profiles_fail(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            copy_v2_config(root)
            for profile_id, code in (
                ("missing", "PROFILE_NOT_FOUND"),
                ("paper2", "PROFILE_DISABLED"),
                ("paper3", "PROFILE_DISABLED"),
                ("live", "PROFILE_DISABLED"),
            ):
                with self.assertRaises(
                    ConfigurationError
                ) as caught:
                    load_profile(
                        profile_id,
                        project_root=root,
                    )
                self.assertEqual(
                    caught.exception.code,
                    code,
                )


if __name__ == "__main__":
    unittest.main()
