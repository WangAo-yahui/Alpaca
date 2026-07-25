from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from v2.data.daily_bars import DailyBarStore
from v2.runtime import (
    build_cycle_paths,
    build_shared_data_paths,
)


class SharedDataPathTests(unittest.TestCase):
    def test_market_is_shared_but_account_data_is_not(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shared = build_shared_data_paths(
                project_root=root
            )
            store = DailyBarStore.for_project(root)
            self.assertEqual(store.root, shared.daily)
            paper1 = build_cycle_paths(
                run_date="2026-07-24",
                cycle_id="20260724T130000",
                project_root=root,
                profile_id="paper1",
            )
            paper2 = build_cycle_paths(
                run_date="2026-07-24",
                cycle_id="20260724T130000",
                project_root=root,
                profile_id="paper2",
            )
            for first, second in (
                (
                    paper1.base_snapshot,
                    paper2.base_snapshot,
                ),
                (
                    paper1.initial_guidance,
                    paper2.initial_guidance,
                ),
                (
                    paper1.broker_submission,
                    paper2.broker_submission,
                ),
                (
                    paper1.daily_report,
                    paper2.daily_report,
                ),
            ):
                self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
