from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from v2.cli import CLIOptions
from v2.exceptions import LiveTradingRejected
from v2.main import bootstrap_main
from v2.models.state import (
    CycleKind,
    CycleStatus,
    ReviewMode,
    StepName,
)
from tests.v2.support import copy_v2_config


def options(**overrides: object) -> CLIOptions:
    values: dict[str, object] = {
        "run_date": "2026-07-23",
        "cycle_id": None,
        "no_review": True,
        "allow_trade": False,
        "force_full": False,
        "force_rebalance": False,
        "execution_only": False,
        "maintenance_only": False,
        "new_cycle": False,
        "paper": True,
        "live": False,
    }
    values.update(overrides)
    return CLIOptions(**values)  # type: ignore[arg-type]


class MainBootstrapTests(unittest.TestCase):
    def test_bootstrap_and_resume_are_persisted(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copy_v2_config(root)
            first = bootstrap_main(
                options(),
                project_root=root,
            )
            second = bootstrap_main(
                options(),
                project_root=root,
            )

            self.assertFalse(first.resumed)
            self.assertTrue(second.resumed)
            self.assertEqual(
                first.paths.cycle_id,
                second.paths.cycle_id,
            )
            self.assertEqual(
                second.state.resume_count,
                1,
            )
            self.assertEqual(
                second.state.cycle_kind,
                CycleKind.DAILY_FULL,
            )
            self.assertEqual(
                second.state.status,
                CycleStatus.INITIALIZED,
            )
            self.assertEqual(
                second.state.current_step,
                StepName.START,
            )
            self.assertEqual(
                second.state.review_mode,
                ReviewMode.SKIPPED_BY_FLAG,
            )
            self.assertTrue(
                second.state.invocation.no_review
            )
            self.assertFalse(
                second.state.invocation.allow_trade
            )
            self.assertTrue(
                second.state.trade_permission.dry_run
            )
            self.assertFalse(
                second.state.trade_permission
                .submission_enabled
            )
            self.assertTrue(
                second.paths.user_review.exists()
            )

    def test_live_is_rejected_before_any_write(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(
                LiveTradingRejected
            ):
                bootstrap_main(
                    options(
                        live=True,
                        paper=False,
                    ),
                    project_root=root,
                )
            self.assertFalse(
                (
                    root / "decision_runtime_v2"
                ).exists()
            )

    def test_maintenance_marks_unused_stages_skipped(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copy_v2_config(root)
            result = bootstrap_main(
                options(maintenance_only=True),
                project_root=root,
            )
            self.assertEqual(
                result.state.cycle_kind,
                CycleKind.MAINTENANCE_ONLY,
            )
            self.assertEqual(
                result.state.stages[
                    "base_data"
                ].status.value,
                "skipped",
            )


if __name__ == "__main__":
    unittest.main()
