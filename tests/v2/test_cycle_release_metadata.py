from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from v2.cli import CLIOptions
from v2.main import bootstrap_main
from v2.runtime import create_cycle_paths
from tests.v2.support import copy_v2_config


class CycleReleaseMetadataTests(unittest.TestCase):
    def test_cycle_records_profile_release_and_guidance(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            copy_v2_config(root)
            options = CLIOptions(
                run_date="2026-07-24",
                cycle_id=None,
                no_review=True,
                allow_trade=False,
                force_full=False,
                force_rebalance=False,
                execution_only=False,
                maintenance_only=False,
                new_cycle=False,
                paper=True,
                live=False,
                profile="paper2",
                no_guidance=True,
            )
            result = bootstrap_main(
                options,
                project_root=root,
            )
            resumed = bootstrap_main(
                options,
                project_root=root,
            )
            state = result.state
            self.assertEqual(
                state.profile_id,
                "paper2",
            )
            self.assertEqual(
                state.release["app_version"],
                "2.0.0",
            )
            self.assertEqual(
                state.release["strategy_id"],
                "core_long",
            )
            self.assertEqual(
                state.release["risk_profile"],
                "paper_standard@1.0.0",
            )
            self.assertEqual(
                len(state.guidance["guidance_hash"]),
                64,
            )
            self.assertTrue(
                result.paths.initial_guidance.exists()
            )
            self.assertTrue(resumed.resumed)
            self.assertEqual(
                resumed.paths.cycle_id,
                result.paths.cycle_id,
            )
            empty = create_cycle_paths(
                "2026-07-24",
                project_root=root,
                profile_id="paper2",
                strategy_id="core_long",
                strategy_version="1.0.1",
                now=datetime(
                    2026,
                    7,
                    24,
                    14,
                    tzinfo=ZoneInfo(
                        "America/New_York"
                    ),
                ),
            )
            explicit = bootstrap_main(
                replace(
                    options,
                    cycle_id=empty.cycle_id,
                ),
                project_root=root,
            )
            self.assertEqual(
                explicit.paths.cycle_id,
                empty.cycle_id,
            )


if __name__ == "__main__":
    unittest.main()
