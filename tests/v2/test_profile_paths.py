from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from v2.runtime import build_daily_paths


class ProfilePathTests(unittest.TestCase):
    def test_profiles_and_strategies_are_isolated(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paper1 = build_daily_paths(
                "2026-07-24",
                project_root=root,
                profile_id="paper1",
                strategy_id="core_long",
                strategy_version="1.0.0",
            )
            paper2 = build_daily_paths(
                "2026-07-24",
                project_root=root,
                profile_id="paper2",
                strategy_id="core_long",
                strategy_version="1.0.0",
            )
            next_release = build_daily_paths(
                "2026-07-24",
                project_root=root,
                profile_id="paper1",
                strategy_id="core_long",
                strategy_version="1.1.0",
            )
            self.assertNotEqual(
                paper1.day_directory,
                paper2.day_directory,
            )
            self.assertNotEqual(
                paper1.day_directory,
                next_release.day_directory,
            )
            self.assertNotEqual(
                paper1.daily_report,
                paper2.daily_report,
            )


if __name__ == "__main__":
    unittest.main()
