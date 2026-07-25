from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from v2.profiles import load_risk_profile
from tests.v2.support import copy_v2_config


class RiskProfileTests(unittest.TestCase):
    def test_versioned_profiles_are_separate(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            copy_v2_config(root)
            paper = load_risk_profile(
                "paper_standard@1.0.0",
                project_root=root,
            )
            live = load_risk_profile(
                "live_conservative@1.0.0",
                project_root=root,
            )
            self.assertEqual(
                paper.environment,
                "paper",
            )
            self.assertEqual(
                live.environment,
                "live",
            )
            self.assertGreater(
                paper.settings[
                    "maximum_gross_exposure"
                ],
                live.settings[
                    "maximum_gross_exposure"
                ],
            )


if __name__ == "__main__":
    unittest.main()
